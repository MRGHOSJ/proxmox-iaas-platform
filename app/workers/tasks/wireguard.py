"""
WireGuard provisioning tasks.

Flow for `provision_wireguard_tunnel_task`:
  1.  Allocate /24 + gateway from wireguard_ip_pool (race-safe via SKIP LOCKED)
  2.  Generate server keypair via OPNsense REST API
  3.  Add the WG server in OPNsense (returns server UUID)
  4.  Compute endpoint = f"{tenant.wan_ip}:{listen_port}" (or caller-supplied)
  5.  set_server to set the endpoint
  6.  Assign a brand-new opt# interface to the WG instance via in-VM PHP
  7.  Insert the WAN firewall rule (UDP/51820 -> opt#) and the WG interface
      allow-all rule into `opnsense_firewall_rules` with apply_status="pending"
  8.  Reconfigure WireGuard service
  9.  Reload OPNsense config (interface reconfigure)
 10.  Mark tunnel active, audit log.

Flow for `provision_wireguard_peer_task`:
  1.  Allocate peer /32 inside tunnel.cidr
  2.  Generate peer keypair on OPNsense
  3.  Generate a 32-byte PSK (base64) locally with `secrets`
  4.  add_client_builder on OPNsense, return both the WireGuard .conf (built
      server-side from get_server_info + our generated keys) and the row id.
  5.  Persist peer row with private_key / preshared_key encrypted at rest.
  6.  Reconfigure WireGuard service.

Flow for `destroy_wireguard_*` rolls back the same in reverse:
  - OPNsense del_client / del_server
  - DB row + IPAM release
  - interface remove (best-effort: the WG instance's opt entry will still
    exist as a placeholder; full removal requires also removing the
    wireguard/<server_uuid> element from config.xml — out of scope for v1,
    it can be cleaned up by the tenant's "Rebuild" action).
"""
import logging
import secrets
import base64
from datetime import datetime, timezone
from typing import Optional

from celery import shared_task
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.audit import log_audit_event, AUDIT_ACTIONS
from app.core.websocket import (
    publish_status_update,
    publish_wireguard_log_update,
    publish_wireguard_tunnel_step,
    publish_wireguard_peer_step,
)
from app.core.crypto import encrypt, decrypt
from app.core.exceptions import WireGuardConfigError
from app.models.tenant import Tenant
from app.models.wireguard import WireGuardTunnel, WireGuardPeer, WireGuardPool
from app.models.opnsense_firewall_rule import OPNsenseFirewallRule
from app.providers.firewall_provider import OPNsenseFirewallProvider
from app.services.wireguard_ipam import (
    allocate_tunnel_subnet,
    release_tunnel_subnet,
    allocate_peer_ip,
)
from app.workers.modules.opnsense_config_invm import OPNsenseConfigInVM
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _build_cfg(vm_id: int, node: str) -> OPNsenseConfigInVM:
    from app.providers import get_hypervisor_provider
    return OPNsenseConfigInVM(
        get_hypervisor_provider(),
        vm_id=vm_id,
        node=node,
        config_path=settings.OPNSENSE_CONFIG_PATH,
    )


def _next_opt(existing_iface_names: list) -> str:
    nums = []
    for name in existing_iface_names:
        if name.startswith("opt"):
            try:
                nums.append(int(name[3:]))
            except ValueError:
                pass
    return f"opt{(max(nums) + 1) if nums else 1}"


def _generate_psk() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def _build_peer_config(
    peer: WireGuardPeer,
    tunnel: WireGuardTunnel,
    server_pubkey: str,
    server_endpoint: str,
    psk: str,
    client_private_key: str,
    lan_cidr: str = "",
) -> str:
    """Render a WireGuard .conf file for the given peer."""
    dns_line = f"DNS = {tunnel.dns}\n" if tunnel.dns else ""
    address_cidr = f"{peer.allowed_ip}/32"
    allowed_ips = f"{tunnel.cidr}, {lan_cidr}" if lan_cidr else f"{tunnel.cidr}"
    return (
        "[Interface]\n"
        f"PrivateKey = {client_private_key}\n"
        f"Address = {address_cidr}\n"
        f"{dns_line}"
        "\n"
        "[Peer]\n"
        f"PublicKey = {server_pubkey}\n"
        f"PresharedKey = {psk}\n"
        f"Endpoint = {server_endpoint}\n"
        f"AllowedIPs = {allowed_ips}\n"
        f"PersistentKeepalive = {peer.keepalive or tunnel.peer_keepalive or 0}\n"
    )


def _ensure_firewall_rules(
    db: Session,
    tenant: Tenant,
    tunnel: WireGuardTunnel,
    opt_interface: str,
    allowed_network_ids: list[int] | None = None,
) -> None:
    """
    Insert firewall rules for the tunnel with apply_status='pending'.

    The WAN rule allows UDP/{listen_port} from any -> WAN address (required
    for WireGuard handshake).

    For each network in allowed_network_ids, a specific pass rule is created
    on the tunnel OPT interface allowing traffic to that network's CIDR.
    If allowed_network_ids is empty or None, NO tunnel traffic rules are
    created (zero-trust default).
    """
    existing_wan = db.query(OPNsenseFirewallRule).filter(
        OPNsenseFirewallRule.tenant_id == tenant.id,
        OPNsenseFirewallRule.description == f"WireGuard {tunnel.name} (WAN)",
    ).first()
    if not existing_wan:
        db.add(OPNsenseFirewallRule(
            tenant_id=tenant.id,
            uuid=f"pending-wg-{tunnel.id}-wan",
            sequence=100,
            enabled="1",
            description=f"WireGuard {tunnel.name} (WAN)",
            interface="wan",
            interfacenot="0",
            quick="1",
            action="pass",
            direction="in",
            ipprotocol="inet",
            protocol="UDP",
            source_not="0",
            source_net="any",
            source_port="",
            destination_not="0",
            destination_net="wanip",
            destination_port=str(tunnel.listen_port),
            gateway="",
            log="0",
            statetype="keep",
            apply_status="pending",
        ))

    if allowed_network_ids:
        from app.models.network import TenantNetwork
        networks = db.query(TenantNetwork).filter(
            TenantNetwork.id.in_(allowed_network_ids),
            TenantNetwork.tenant_id == tenant.id,
            TenantNetwork.status == "active",
        ).all()
        for idx, network in enumerate(networks):
            rule_desc = f"WireGuard {tunnel.name} -> {network.name}"
            existing_rule = db.query(OPNsenseFirewallRule).filter(
                OPNsenseFirewallRule.tenant_id == tenant.id,
                OPNsenseFirewallRule.description == rule_desc,
            ).first()
            if not existing_rule:
                db.add(OPNsenseFirewallRule(
                    tenant_id=tenant.id,
                    uuid=f"pending-wg-{tunnel.id}-net-{network.id}",
                    sequence=200 + idx,
                    enabled="1",
                    description=rule_desc,
                    interface=opt_interface,
                    interfacenot="0",
                    quick="1",
                    action="pass",
                    direction="in",
                    ipprotocol="inet",
                    protocol="any",
                    source_not="0",
                    source_net="any",
                    source_port="",
                    destination_not="0",
                    destination_net=network.cidr,
                    destination_port="",
                    gateway="",
                    log="0",
                    statetype="keep",
                    apply_status="pending",
                ))


@shared_task(bind=True, max_retries=3, default_retry_delay=15, name="tasks.provision_wireguard_tunnel")
def provision_wireguard_tunnel_task(
    self,
    tenant_id: int,
    tunnel_id: int,
    actor_id: Optional[int] = None,
    actor_username: str = "system",
    allowed_network_ids: Optional[list[int]] = None,
):
    """Provision a WireGuard tunnel end-to-end."""
    TOTAL_STEPS = 8
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            return {"status": "error", "error": f"Tenant {tenant_id} not found"}
        if not tenant.opnsense_vm_id or not tenant.opnsense_api_key or not tenant.opnsense_api_secret:
            raise WireGuardConfigError(f"Tenant {tenant_id} is not fully provisioned (no OPNsense VM/API)")

        tunnel = db.query(WireGuardTunnel).filter(
            WireGuardTunnel.id == tunnel_id,
            WireGuardTunnel.tenant_id == tenant_id,
        ).first()
        if not tunnel:
            return {"status": "error", "error": f"Tunnel {tunnel_id} not found"}
        if tunnel.status == "active":
            return {"status": "success", "tunnel_id": tunnel_id, "already_active": True}

        tunnel.status = "provisioning"
        tunnel.error = None
        db.commit()
        publish_status_update("wireguard_tunnel", tunnel_id, "pending", "provisioning")

        provider = OPNsenseFirewallProvider(tenant)

        publish_wireguard_tunnel_step(tunnel_id, 1, TOTAL_STEPS, "Generating WireGuard keypair", "doing")
        wg_keys = provider.generate_keypair()
        publish_wireguard_tunnel_step(tunnel_id, 1, TOTAL_STEPS, "Keypair generated", "done")

        publish_wireguard_tunnel_step(tunnel_id, 2, TOTAL_STEPS, "Allocating subnet", "doing")
        if not tunnel.pool_id:
            pool_row = allocate_tunnel_subnet(db)
            tunnel.pool_id = pool_row.id
            tunnel.cidr = pool_row.cidr
            tunnel.gateway_ip = pool_row.gateway_ip
            tunnel.tunnel_address = f"{pool_row.gateway_ip}/24"
            pool_row.wireguard_tunnel_id = tunnel.id
            db.commit()
        publish_wireguard_tunnel_step(tunnel_id, 2, TOTAL_STEPS, f"Subnet: {tunnel.cidr}", "done")

        if tunnel.listen_port:
            listen_port = tunnel.listen_port
        else:
            existing_ports = {
                t.listen_port for t in db.query(WireGuardTunnel.listen_port)
                .filter(
                    WireGuardTunnel.tenant_id == tenant_id,
                    WireGuardTunnel.status.in_(["active", "provisioning"]),
                )
                .all()
                if t.listen_port
            }
            listen_port = settings.WIREGUARD_DEFAULT_LISTEN_PORT
            while listen_port in existing_ports:
                listen_port += 1
        tunnel.listen_port = listen_port
        mtu = tunnel.mtu or settings.WIREGUARD_DEFAULT_MTU
        tunnel.mtu = mtu

        publish_wireguard_tunnel_step(tunnel_id, 3, TOTAL_STEPS, "Creating WireGuard server on OPNsense", "doing")
        server_uuid = provider.add_wg_server(
            name=tunnel.name,
            pubkey=wg_keys["pubkey"],
            privkey=wg_keys["privkey"],
            listen_port=listen_port,
            tunnel_address=tunnel.tunnel_address,
            mtu=mtu,
            peer_keepalive=tunnel.peer_keepalive or settings.WIREGUARD_PEER_KEEPALIVE,
        )
        tunnel.opnsense_server_uuid = server_uuid
        tunnel.public_key = wg_keys["pubkey"]
        tunnel.private_key = wg_keys["privkey"]
        publish_wireguard_tunnel_step(tunnel_id, 3, TOTAL_STEPS, "Server created on OPNsense", "done")

        endpoint = tunnel.endpoint
        if not endpoint:
            if not tenant.wan_ip:
                raise WireGuardConfigError("Tenant has no WAN IP and tunnel endpoint was not provided")
            endpoint = f"{tenant.wan_ip}:{listen_port}"
        publish_wireguard_tunnel_step(tunnel_id, 4, TOTAL_STEPS, "Setting endpoint", "doing")
        provider.set_wg_server_endpoint(server_uuid=server_uuid, endpoint=endpoint)
        tunnel.endpoint = endpoint
        db.commit()
        publish_wireguard_tunnel_step(tunnel_id, 4, TOTAL_STEPS, f"Endpoint: {endpoint}", "done")

        publish_wireguard_tunnel_step(tunnel_id, 5, TOTAL_STEPS, "Enabling WireGuard service", "doing")
        provider.wg_general_enable(True)
        provider.wg_service_reconfigure()
        publish_wireguard_tunnel_step(tunnel_id, 5, TOTAL_STEPS, "WireGuard service enabled", "done")

        cfg = _build_cfg(vm_id=tenant.opnsense_vm_id, node=settings.PROXMOX_NODE)
        existing_iface_names = cfg.get_interface_names()
        opt_name = _next_opt(existing_iface_names)
        wg_if = provider.get_wg_device_name(server_uuid)
        publish_wireguard_tunnel_step(tunnel_id, 6, TOTAL_STEPS, "Creating OPT interface", "doing")
        cfg.add_opt_interface(
            opt_name=opt_name,
            vlanif=wg_if,
            ip=tunnel.gateway_ip,
            subnet=24,
            descr=f"WireGuard-{tunnel.name}",
        )
        cfg.reload_config()
        tunnel.opt_interface = opt_name
        db.commit()
        publish_wireguard_tunnel_step(tunnel_id, 6, TOTAL_STEPS, f"Interface {opt_name} created", "done")

        effective_network_ids = allowed_network_ids or tunnel.allowed_network_ids or []
        publish_wireguard_tunnel_step(tunnel_id, 7, TOTAL_STEPS, "Inserting firewall rules", "doing")
        _ensure_firewall_rules(
            db=db, tenant=tenant, tunnel=tunnel,
            opt_interface=opt_name, allowed_network_ids=effective_network_ids,
        )
        db.commit()
        publish_wireguard_tunnel_step(tunnel_id, 7, TOTAL_STEPS, "Firewall rules inserted", "done")

        publish_wireguard_tunnel_step(tunnel_id, 8, TOTAL_STEPS, "Applying firewall rules", "doing")
        try:
            from app.workers.tasks.firewall_manager import apply_all_pending_rules_task
            apply_all_pending_rules_task.delay(
                tenant_id=tenant.id,
                provider_type="opnsense",
            )
            logger.info("Dispatched auto-apply of pending firewall rules for tenant %s", tenant.id)
        except Exception as fw_exc:
            logger.warning("Auto-apply of pending firewall rules failed (rules remain pending): %s", fw_exc)
        publish_wireguard_tunnel_step(tunnel_id, 8, TOTAL_STEPS, "Firewall rules applied", "done")

        tunnel.status = "active"
        tunnel.provisioned_at = datetime.now(timezone.utc)
        db.commit()
        publish_status_update("wireguard_tunnel", tunnel_id, "provisioning", "active")

        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["WIREGUARD_TUNNEL_CREATE"],
            target_type="wireguard_tunnel",
            actor_id=actor_id,
            actor_username=actor_username,
            target_id=tunnel.id,
            target_name=tunnel.name,
            new_value=f"cidr={tunnel.cidr},port={listen_port},endpoint={endpoint},opnsense_uuid={server_uuid}",
            details=f"WireGuard tunnel {tunnel.name} provisioned",
            tenant_id=tenant.id,
        )

        return {
            "status": "success",
            "tunnel_id": tunnel.id,
            "server_uuid": server_uuid,
            "opt_interface": opt_name,
            "endpoint": endpoint,
        }

    except Exception as exc:
        logger.exception("Tunnel %s provisioning failed: %s", tunnel_id, exc)
        publish_wireguard_log_update(tunnel_id, f"Provisioning failed: {exc}", level="error")
        db.rollback()
        try:
            tunnel = db.query(WireGuardTunnel).filter(WireGuardTunnel.id == tunnel_id).first()
            if tunnel:
                tunnel.status = "error"
                tunnel.error = str(exc)[:500]
                db.commit()
            log_audit_event(
                db=db,
                action=AUDIT_ACTIONS["WIREGUARD_PROVISION_ERROR"],
                target_type="wireguard_tunnel",
                actor_id=actor_id,
                actor_username=actor_username,
                target_id=tunnel_id,
                target_name=(tunnel.name if tunnel else f"tunnel-{tunnel_id}"),
                new_value=f"error: {str(exc)[:300]}",
                tenant_id=tenant_id,
            )
        except Exception:
            logger.exception("Failed to persist tunnel error state")
        publish_status_update("wireguard_tunnel", tunnel_id, "provisioning", "error")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=15 * (2 ** self.request.retries))
        return {"status": "error", "error": str(exc)}
    finally:
        db.close()


@shared_task(bind=True, max_retries=3, default_retry_delay=10, name="tasks.destroy_wireguard_tunnel")
def destroy_wireguard_tunnel_task(
    self,
    tenant_id: int,
    tunnel_id: int,
    actor_id: Optional[int] = None,
    actor_username: str = "system",
):
    """Tear down a WireGuard tunnel (deletes server, releases IPAM)."""
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            return {"status": "error", "error": f"Tenant {tenant_id} not found"}

        tunnel = db.query(WireGuardTunnel).filter(
            WireGuardTunnel.id == tunnel_id,
            WireGuardTunnel.tenant_id == tenant_id,
        ).first()
        if not tunnel:
            return {"status": "success", "tunnel_id": tunnel_id, "already_gone": True}

        tunnel.status = "destroying"
        db.commit()
        publish_status_update("wireguard_tunnel", tunnel_id, "active", "destroying")

        if tenant.opnsense_vm_id and tenant.opnsense_api_key and tunnel.opnsense_server_uuid:
            try:
                provider = OPNsenseFirewallProvider(tenant)
                provider.del_wg_server(tunnel.opnsense_server_uuid)
                provider.wg_service_reconfigure()
            except Exception as e:
                logger.warning("OPNsense del_server failed (continuing): %s", e)

        if tunnel.pool_id:
            release_tunnel_subnet(db, tunnel.pool_id)

        tunnel_name = tunnel.name

        fw_rules = db.query(OPNsenseFirewallRule).filter(
            OPNsenseFirewallRule.tenant_id == tenant_id,
            OPNsenseFirewallRule.description.like(f"WireGuard {tunnel_name}%"),
        ).all()
        for rule in fw_rules:
            if rule.apply_status == "synced":
                rule.apply_status = "pending_delete"
            else:
                db.delete(rule)

        db.delete(tunnel)
        db.commit()

        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["WIREGUARD_TUNNEL_DELETE"],
            target_type="wireguard_tunnel",
            actor_id=actor_id,
            actor_username=actor_username,
            target_id=tunnel_id,
            target_name=tunnel_name,
            tenant_id=tenant_id,
        )

        publish_status_update("wireguard_tunnel", tunnel_id, "destroying", "destroyed")
        return {"status": "success", "tunnel_id": tunnel_id}
    except Exception as exc:
        logger.exception("Tunnel %s destroy failed: %s", tunnel_id, exc)
        db.rollback()
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=10 * (2 ** self.request.retries))
        return {"status": "error", "error": str(exc)}
    finally:
        db.close()


@shared_task(bind=True, max_retries=3, default_retry_delay=10, name="tasks.provision_wireguard_peer")
def provision_wireguard_peer_task(
    self,
    tenant_id: int,
    tunnel_id: int,
    peer_id: int,
    actor_id: Optional[int] = None,
    actor_username: str = "system",
):
    """Provision a WireGuard peer under a tunnel."""
    TOTAL_STEPS = 5
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        tunnel = db.query(WireGuardTunnel).filter(
            WireGuardTunnel.id == tunnel_id,
            WireGuardTunnel.tenant_id == tenant_id,
        ).first()
        peer = db.query(WireGuardPeer).filter(
            WireGuardPeer.id == peer_id,
            WireGuardPeer.tunnel_id == tunnel_id,
        ).first()
        if not tenant or not tunnel or not peer:
            return {"status": "error", "error": "tenant/tunnel/peer not found"}
        if not tunnel.opnsense_server_uuid:
            raise WireGuardConfigError("Tunnel has no OPNsense server UUID; is it provisioned?")
        if tunnel.status != "active":
            raise WireGuardConfigError(f"Tunnel is in status {tunnel.status}; cannot add peer")

        peer.status = "provisioning"
        db.commit()
        publish_status_update("wireguard_peer", peer_id, "pending", "provisioning")

        provider = OPNsenseFirewallProvider(tenant)

        publish_wireguard_peer_step(peer_id, 1, TOTAL_STEPS, "Generating WireGuard keypair", "doing")
        keys = provider.generate_keypair()
        publish_wireguard_peer_step(peer_id, 1, TOTAL_STEPS, "Keypair generated", "done")

        publish_wireguard_peer_step(peer_id, 2, TOTAL_STEPS, "Generating preshared key", "doing")
        psk = _generate_psk()
        publish_wireguard_peer_step(peer_id, 2, TOTAL_STEPS, "Preshared key generated", "done")

        publish_wireguard_peer_step(peer_id, 3, TOTAL_STEPS, "Allocating peer IP address", "doing")
        peer_ip = peer.allowed_ip or allocate_peer_ip(db, tunnel)
        peer.allowed_ip = peer_ip
        publish_wireguard_peer_step(peer_id, 3, TOTAL_STEPS, f"IP allocated: {peer_ip}", "done")

        publish_wireguard_peer_step(peer_id, 4, TOTAL_STEPS, "Registering peer on OPNsense", "doing")
        client_uuid = provider.add_wg_client(
            server_uuid=tunnel.opnsense_server_uuid,
            name=peer.name,
            pubkey=keys["pubkey"],
            psk=psk,
            tunnel_address=f"{peer_ip}/32",
            keepalive=peer.keepalive or tunnel.peer_keepalive or settings.WIREGUARD_PEER_KEEPALIVE,
            endpoint=peer.endpoint or "",
        )
        peer.opnsense_client_uuid = client_uuid
        peer.public_key = keys["pubkey"]
        peer.private_key_enc = encrypt(keys["privkey"])
        peer.preshared_key_enc = encrypt(psk)
        db.commit()
        publish_wireguard_peer_step(peer_id, 4, TOTAL_STEPS, "Peer registered on OPNsense", "done")

        publish_wireguard_peer_step(peer_id, 5, TOTAL_STEPS, "Reconfiguring WireGuard service", "doing")
        if tunnel.endpoint:
            provider.set_wg_server_endpoint(
                server_uuid=tunnel.opnsense_server_uuid,
                endpoint=tunnel.endpoint,
            )
        provider.wg_general_enable(True)
        provider.wg_service_reconfigure()
        publish_wireguard_peer_step(peer_id, 5, TOTAL_STEPS, "WireGuard reconfigured", "done")

        peer.status = "active"
        peer.error = None
        db.commit()
        publish_status_update("wireguard_peer", peer_id, "provisioning", "active")

        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["WIREGUARD_PEER_CREATE"],
            target_type="wireguard_peer",
            actor_id=actor_id,
            actor_username=actor_username,
            target_id=peer.id,
            target_name=peer.name,
            new_value=f"tunnel_id={tunnel.id},allowed_ip={peer.allowed_ip}",
            tenant_id=tenant.id,
        )

        return {
            "status": "success",
            "peer_id": peer.id,
            "tunnel_id": tunnel.id,
            "client_uuid": client_uuid,
        }
    except Exception as exc:
        logger.exception("Peer %s provisioning failed: %s", peer_id, exc)
        db.rollback()
        try:
            peer = db.query(WireGuardPeer).filter(WireGuardPeer.id == peer_id).first()
            if peer:
                if self.request.retries < self.max_retries:
                    raise self.retry(exc=exc, countdown=10 * (2 ** self.request.retries))
                peer.status = "error"
                peer.error = str(exc)
                db.commit()
                publish_status_update("wireguard_peer", peer_id, "provisioning", "error")
                db.delete(peer)
                db.commit()
        except Exception:
            db.rollback()
        return {"status": "error", "error": str(exc)}
    finally:
        db.close()


@shared_task(bind=True, max_retries=3, default_retry_delay=10, name="tasks.destroy_wireguard_peer")
def destroy_wireguard_peer_task(
    self,
    tenant_id: int,
    tunnel_id: int,
    peer_id: int,
    actor_id: Optional[int] = None,
    actor_username: str = "system",
):
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        peer = db.query(WireGuardPeer).filter(
            WireGuardPeer.id == peer_id,
            WireGuardPeer.tunnel_id == tunnel_id,
        ).first()
        if not peer:
            return {"status": "success", "peer_id": peer_id, "already_gone": True}

        if (
            tenant
            and tenant.opnsense_vm_id
            and tenant.opnsense_api_key
            and peer.opnsense_client_uuid
        ):
            try:
                provider = OPNsenseFirewallProvider(tenant)
                provider.del_wg_client(peer.opnsense_client_uuid)
                provider.wg_service_reconfigure()
            except Exception as e:
                logger.warning("OPNsense del_client failed (continuing): %s", e)

        peer_name = peer.name
        db.delete(peer)
        db.commit()

        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["WIREGUARD_PEER_DELETE"],
            target_type="wireguard_peer",
            actor_id=actor_id,
            actor_username=actor_username,
            target_id=peer_id,
            target_name=peer_name,
            tenant_id=tenant_id,
        )

        return {"status": "success", "peer_id": peer_id}
    except Exception as exc:
        logger.exception("Peer %s destroy failed: %s", peer_id, exc)
        db.rollback()
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc, countdown=10 * (2 ** self.request.retries))
        return {"status": "error", "error": str(exc)}
    finally:
        db.close()
