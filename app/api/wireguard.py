"""
WireGuard API router.

Tunnels:
  GET    /v1/wireguard/tunnels
  POST   /v1/wireguard/tunnels               (queues provision task)
  GET    /v1/wireguard/tunnels/{id}
  PATCH  /v1/wireguard/tunnels/{id}
  DELETE /v1/wireguard/tunnels/{id}          (queues destroy task)
  GET    /v1/wireguard/tunnels/{id}/logs

Peers:
  GET    /v1/wireguard/tunnels/{id}/peers
  POST   /v1/wireguard/tunnels/{id}/peers    (queues peer provision; returns
                                              one-time .conf in response)
  GET    /v1/wireguard/tunnels/{id}/peers/{peer_id}
  PATCH  /v1/wireguard/tunnels/{id}/peers/{peer_id}
  DELETE /v1/wireguard/tunnels/{id}/peers/{peer_id}
  GET    /v1/wireguard/tunnels/{id}/peers/{peer_id}/config  (re-emit .conf)

Permissions follow the existing pattern (firewall:create / firewall:read /
firewall:update / firewall:delete).
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional

from app.core.database import get_db
from app.core.dependencies import get_current_tenant, get_current_user
from app.core.iam import has_permission
from app.core.audit import log_audit_event, AUDIT_ACTIONS
from app.core.crypto import decrypt
from app.core.exceptions import WireGuardConfigError
from app.models.tenant import Tenant
from app.models.user import User
from app.models.wireguard import WireGuardTunnel, WireGuardPeer
from app.schemas.wireguard import (
    WireGuardTunnelCreate,
    WireGuardTunnelUpdate,
    WireGuardTunnelResponse,
    WireGuardTunnelListResponse,
    WireGuardPeerCreate,
    WireGuardPeerUpdate,
    WireGuardPeerResponse,
    WireGuardPeerListResponse,
    WireGuardPeerConfigResponse,
    WireGuardStatusResponse,
)
from app.services.wireguard_ipam import allocate_tunnel_subnet
from app.workers.tasks.wireguard import (
    provision_wireguard_tunnel_task,
    destroy_wireguard_tunnel_task,
    provision_wireguard_peer_task,
    destroy_wireguard_peer_task,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/wireguard", tags=["wireguard"])


def _compute_allowed_ips(tunnel: WireGuardTunnel, db: Session) -> str:
    """Build AllowedIPs from the tunnel's own CIDR + its allowed_network_ids."""
    parts = [tunnel.cidr]
    if tunnel.allowed_network_ids:
        from app.models.network import TenantNetwork
        networks = db.query(TenantNetwork.cidr).filter(
            TenantNetwork.id.in_(tunnel.allowed_network_ids),
            TenantNetwork.tenant_id == tunnel.tenant_id,
            TenantNetwork.status == "active",
        ).all()
        parts.extend(n.cidr for n in networks if n.cidr)
    return ", ".join(parts)


def _tunnel_to_dict(t: WireGuardTunnel) -> dict:
    return {
        "id": t.id,
        "tenant_id": t.tenant_id,
        "name": t.name,
        "opnsense_server_uuid": t.opnsense_server_uuid,
        "listen_port": t.listen_port,
        "mtu": t.mtu,
        "dns": t.dns,
        "tunnel_address": t.tunnel_address,
        "cidr": t.cidr,
        "gateway_ip": t.gateway_ip,
        "subnet_mask": t.subnet_mask,
        "public_key": t.public_key,
        "endpoint": t.endpoint,
        "opt_interface": t.opt_interface,
        "status": t.status,
        "error": t.error,
        "peer_keepalive": t.peer_keepalive,
        "is_enabled": t.is_enabled,
        "peer_count": len(t.peers) if t.peers is not None else 0,
        "allowed_network_ids": t.allowed_network_ids or [],
        "created_at": t.created_at,
        "updated_at": t.updated_at,
        "provisioned_at": t.provisioned_at,
    }


def _peer_to_dict(p: WireGuardPeer) -> dict:
    return {
        "id": p.id,
        "tunnel_id": p.tunnel_id,
        "name": p.name,
        "public_key": p.public_key,
        "allowed_ip": p.allowed_ip,
        "endpoint": p.endpoint,
        "keepalive": p.keepalive,
        "is_enabled": p.is_enabled,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }


@router.get("/tunnels", response_model=WireGuardTunnelListResponse)
def list_tunnels(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(current_user, current_tenant.id, "wireguard:read", db):
        raise HTTPException(status_code=403, detail="Not authorized")

    tunnels = (
        db.query(WireGuardTunnel)
        .filter_by(tenant_id=current_tenant.id)
        .order_by(WireGuardTunnel.id.asc())
        .all()
    )
    return {"total": len(tunnels), "tunnels": [_tunnel_to_dict(t) for t in tunnels]}


@router.get("/status", response_model=WireGuardStatusResponse)
def get_status(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(current_user, current_tenant.id, "wireguard:read", db):
        raise HTTPException(status_code=403, detail="Not authorized")

    tunnels = db.query(WireGuardTunnel).filter_by(tenant_id=current_tenant.id).all()
    peers = db.query(WireGuardPeer).join(
        WireGuardTunnel, WireGuardPeer.tunnel_id == WireGuardTunnel.id
    ).filter(WireGuardTunnel.tenant_id == current_tenant.id).all()

    return WireGuardStatusResponse(
        total_tunnels=len(tunnels),
        active_tunnels=sum(1 for t in tunnels if t.status == "active"),
        provisioning_tunnels=sum(1 for t in tunnels if t.status in ("pending", "provisioning")),
        error_tunnels=sum(1 for t in tunnels if t.status == "error"),
        total_peers=len(peers),
    )


@router.get("/available-networks")
def list_available_networks(
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
):
    """List active tenant networks available for VPN firewall access selection."""
    if not has_permission(current_user, current_tenant.id, "wireguard:read", db):
        raise HTTPException(status_code=403, detail="Not authorized")

    from app.models.network import TenantNetwork
    networks = (
        db.query(TenantNetwork)
        .filter(
            TenantNetwork.tenant_id == current_tenant.id,
            TenantNetwork.status == "active",
        )
        .order_by(TenantNetwork.id.asc())
        .all()
    )
    return {
        "total": len(networks),
        "networks": [
            {
                "id": n.id,
                "name": n.name,
                "cidr": n.cidr,
                "gateway_ip": n.gateway_ip,
                "vlan_id": n.vlan_id,
                "is_default": n.is_default,
                "opnsense_interface": n.opnsense_interface,
            }
            for n in networks
        ],
    }


@router.post("/tunnels", response_model=WireGuardTunnelResponse, status_code=201)
async def create_tunnel(
    body: WireGuardTunnelCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(current_user, current_tenant.id, "wireguard:create", db):
        raise HTTPException(status_code=403, detail="Not authorized")
    if not current_tenant.opnsense_vm_id:
        raise HTTPException(status_code=400, detail="OPNsense is not configured for this tenant")

    existing = db.query(WireGuardTunnel).filter_by(
        tenant_id=current_tenant.id, name=body.name
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Tunnel with name '{body.name}' already exists")

    from app.services.quota import check_network_quota, QuotaExceededError
    try:
        check_network_quota(current_tenant.id, db)
    except QuotaExceededError as e:
        raise HTTPException(status_code=429, detail=str(e))

    from app.models.network import TenantNetwork
    valid_networks = (
        db.query(TenantNetwork.id)
        .filter(
            TenantNetwork.tenant_id == current_tenant.id,
            TenantNetwork.id.in_(body.allowed_network_ids),
            TenantNetwork.status == "active",
        )
        .all()
    )
    valid_ids = {n.id for n in valid_networks}
    invalid_ids = set(body.allowed_network_ids) - valid_ids
    if invalid_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid or inactive network IDs: {sorted(invalid_ids)}",
        )
    allowed_network_ids = sorted(valid_ids)

    pool_row = allocate_tunnel_subnet(db)
    if body.listen_port:
        listen_port = body.listen_port
    else:
        from app.core.config import settings
        existing_ports = {
            t.listen_port for t in db.query(WireGuardTunnel.listen_port)
            .filter(WireGuardTunnel.tenant_id == current_tenant.id,
                    WireGuardTunnel.status.in_(["active", "provisioning"]))
            .all()
            if t.listen_port
        }
        listen_port = settings.WIREGUARD_DEFAULT_LISTEN_PORT
        while listen_port in existing_ports:
            listen_port += 1

    tunnel = WireGuardTunnel(
        tenant_id=current_tenant.id,
        name=body.name,
        listen_port=listen_port,
        mtu=body.mtu or 1420,
        dns=body.dns,
        endpoint=body.endpoint,
        tunnel_address=f"{pool_row.gateway_ip}/24",
        cidr=pool_row.cidr,
        gateway_ip=pool_row.gateway_ip,
        subnet_mask=24,
        pool_id=pool_row.id,
        public_key="pending",
        private_key="pending",
        peer_keepalive=body.peer_keepalive or 25,
        allowed_network_ids=allowed_network_ids,
        status="pending",
    )
    db.add(tunnel)
    db.flush()
    pool_row.wireguard_tunnel_id = tunnel.id
    db.commit()
    db.refresh(tunnel)

    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["WIREGUARD_TUNNEL_CREATE"],
        target_type="wireguard_tunnel",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=tunnel.id,
        target_name=tunnel.name,
        new_value=f"cidr={tunnel.cidr},port={listen_port},endpoint={body.endpoint or '(auto)'},allowed_networks={allowed_network_ids}",
        request_id=request.headers.get("X-Request-ID", "unknown"),
        ip_address=request.client.host if request.client else None,
        tenant_id=current_tenant.id,
    )

    provision_wireguard_tunnel_task.delay(
        tenant_id=current_tenant.id,
        tunnel_id=tunnel.id,
        actor_id=current_user.id,
        actor_username=current_user.username,
        allowed_network_ids=allowed_network_ids,
    )

    return _tunnel_to_dict(tunnel)


@router.get("/tunnels/{tunnel_id}", response_model=WireGuardTunnelResponse)
def get_tunnel(
    tunnel_id: int,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(current_user, current_tenant.id, "wireguard:read", db):
        raise HTTPException(status_code=403, detail="Not authorized")

    tunnel = db.query(WireGuardTunnel).filter_by(
        id=tunnel_id, tenant_id=current_tenant.id
    ).first()
    if not tunnel:
        raise HTTPException(status_code=404, detail="Tunnel not found")
    return _tunnel_to_dict(tunnel)


@router.get("/tunnels/{tunnel_id}/access")
def get_tunnel_access(
    tunnel_id: int,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
):
    """
    Return the current effective network access for a tunnel by reading
    actual firewall rules on its OPT interface (source of truth).
    """
    if not has_permission(current_user, current_tenant.id, "wireguard:read", db):
        raise HTTPException(status_code=403, detail="Not authorized")

    tunnel = db.query(WireGuardTunnel).filter_by(
        id=tunnel_id, tenant_id=current_tenant.id
    ).first()
    if not tunnel:
        raise HTTPException(status_code=404, detail="Tunnel not found")
    if not tunnel.opt_interface:
        raise HTTPException(status_code=400, detail="Tunnel has no assigned interface yet")

    from app.models.opnsense_firewall_rule import OPNsenseFirewallRule
    from app.models.network import TenantNetwork

    pass_rules = db.query(OPNsenseFirewallRule).filter(
        OPNsenseFirewallRule.tenant_id == current_tenant.id,
        OPNsenseFirewallRule.interface == tunnel.opt_interface,
        OPNsenseFirewallRule.action == "pass",
        OPNsenseFirewallRule.direction == "in",
        OPNsenseFirewallRule.apply_status.in_(["pending", "synced"]),
    ).all()

    active_networks = db.query(TenantNetwork).filter(
        TenantNetwork.tenant_id == current_tenant.id,
        TenantNetwork.status == "active",
    ).all()
    cidr_to_net = {n.cidr: n.id for n in active_networks}

    allowed_network_ids = set()
    matched_rules = []
    for rule in pass_rules:
        net_id = cidr_to_net.get(rule.destination_net)
        if net_id:
            allowed_network_ids.add(net_id)
            matched_rules.append({
                "uuid": rule.uuid,
                "description": rule.description,
                "destination_net": rule.destination_net,
                "enabled": rule.enabled,
                "apply_status": rule.apply_status,
            })

    return {
        "tunnel_id": tunnel.id,
        "opt_interface": tunnel.opt_interface,
        "allowed_network_ids": sorted(allowed_network_ids),
        "rules": matched_rules,
    }


@router.patch("/tunnels/{tunnel_id}", response_model=WireGuardTunnelResponse)
def update_tunnel(
    tunnel_id: int,
    body: WireGuardTunnelUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(current_user, current_tenant.id, "wireguard:update", db):
        raise HTTPException(status_code=403, detail="Not authorized")

    tunnel = db.query(WireGuardTunnel).filter_by(
        id=tunnel_id, tenant_id=current_tenant.id
    ).first()
    if not tunnel:
        raise HTTPException(status_code=404, detail="Tunnel not found")

    old = {
        "name": tunnel.name, "mtu": tunnel.mtu, "dns": tunnel.dns,
        "endpoint": tunnel.endpoint, "peer_keepalive": tunnel.peer_keepalive,
        "is_enabled": tunnel.is_enabled,
        "allowed_network_ids": tunnel.allowed_network_ids or [],
    }

    for field in ("name", "mtu", "dns", "endpoint", "peer_keepalive", "is_enabled"):
        value = getattr(body, field, None)
        if value is not None:
            setattr(tunnel, field, value)

    network_rules_changed = False
    if body.allowed_network_ids is not None and tunnel.opt_interface:
        from app.models.network import TenantNetwork
        from app.models.opnsense_firewall_rule import OPNsenseFirewallRule

        new_ids = set(body.allowed_network_ids)
        if new_ids:
            valid_networks = (
                db.query(TenantNetwork.id, TenantNetwork.name, TenantNetwork.cidr)
                .filter(
                    TenantNetwork.tenant_id == current_tenant.id,
                    TenantNetwork.id.in_(new_ids),
                    TenantNetwork.status == "active",
                )
                .all()
            )
            valid_map = {n.id: n for n in valid_networks}
            invalid_ids = new_ids - set(valid_map.keys())
            if invalid_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid or inactive network IDs: {sorted(invalid_ids)}",
                )
        else:
            valid_map = {}

        old_ids = set(tunnel.allowed_network_ids or [])
        to_add = new_ids - old_ids
        to_remove = old_ids - new_ids

        for net_id in to_remove:
            removed_net = db.query(TenantNetwork).filter(
                TenantNetwork.id == net_id,
                TenantNetwork.tenant_id == current_tenant.id,
            ).first()
            if removed_net:
                rule_desc = f"WireGuard {tunnel.name} -> {removed_net.name}"
                rule_to_delete = db.query(OPNsenseFirewallRule).filter(
                    OPNsenseFirewallRule.tenant_id == current_tenant.id,
                    OPNsenseFirewallRule.description == rule_desc,
                ).first()
                if rule_to_delete:
                    if rule_to_delete.apply_status == "synced":
                        rule_to_delete.apply_status = "pending_delete"
                    else:
                        db.delete(rule_to_delete)
                    network_rules_changed = True

        for net_id in sorted(to_add):
            net = valid_map[net_id]
            rule_desc = f"WireGuard {tunnel.name} -> {net.name}"
            existing_rule = db.query(OPNsenseFirewallRule).filter(
                OPNsenseFirewallRule.tenant_id == current_tenant.id,
                OPNsenseFirewallRule.description == rule_desc,
            ).first()
            if not existing_rule:
                max_seq = db.query(func.max(OPNsenseFirewallRule.sequence)).filter(
                    OPNsenseFirewallRule.tenant_id == current_tenant.id,
                ).scalar() or 200
                db.add(OPNsenseFirewallRule(
                    tenant_id=current_tenant.id,
                    uuid=f"pending-wg-{tunnel.id}-net-{net_id}",
                    sequence=max_seq + 1,
                    enabled="1",
                    description=rule_desc,
                    interface=tunnel.opt_interface,
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
                    destination_net=net.cidr,
                    destination_port="",
                    gateway="",
                    log="0",
                    statetype="keep",
                    apply_status="pending",
                ))
            network_rules_changed = True

        tunnel.allowed_network_ids = sorted(new_ids)

    db.commit()
    db.refresh(tunnel)

    if network_rules_changed:
        try:
            from app.workers.tasks.firewall_manager import apply_all_pending_rules_task
            apply_all_pending_rules_task.delay(
                tenant_id=current_tenant.id,
                provider_type="opnsense",
            )
        except Exception as fw_exc:
            logger.warning("Auto-apply of pending firewall rules failed: %s", fw_exc)

    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["WIREGUARD_TUNNEL_UPDATE"],
        target_type="wireguard_tunnel",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=tunnel.id,
        target_name=tunnel.name,
        old_value=str(old),
        new_value=str({k: getattr(tunnel, k) for k in old.keys()}),
        tenant_id=current_tenant.id,
    )
    return _tunnel_to_dict(tunnel)


@router.delete("/tunnels/{tunnel_id}", status_code=202)
def delete_tunnel(
    tunnel_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(current_user, current_tenant.id, "wireguard:delete", db):
        raise HTTPException(status_code=403, detail="Not authorized")

    tunnel = db.query(WireGuardTunnel).filter_by(
        id=tunnel_id, tenant_id=current_tenant.id
    ).first()
    if not tunnel:
        raise HTTPException(status_code=404, detail="Tunnel not found")

    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["WIREGUARD_TUNNEL_DELETE"],
        target_type="wireguard_tunnel",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=tunnel.id,
        target_name=tunnel.name,
        tenant_id=current_tenant.id,
    )

    destroy_wireguard_tunnel_task.delay(
        tenant_id=current_tenant.id,
        tunnel_id=tunnel.id,
        actor_id=current_user.id,
        actor_username=current_user.username,
    )
    return {"status": "destroying", "tunnel_id": tunnel.id}


@router.get("/tunnels/{tunnel_id}/logs")
def get_tunnel_logs(
    tunnel_id: int,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(current_user, current_tenant.id, "wireguard:read", db):
        raise HTTPException(status_code=403, detail="Not authorized")
    tunnel = db.query(WireGuardTunnel).filter_by(
        id=tunnel_id, tenant_id=current_tenant.id
    ).first()
    if not tunnel:
        raise HTTPException(status_code=404, detail="Tunnel not found")
    return {"tunnel_id": tunnel_id, "logs": []}


# --- Peer endpoints ---


@router.get(
    "/tunnels/{tunnel_id}/peers",
    response_model=WireGuardPeerListResponse,
)
def list_peers(
    tunnel_id: int,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(current_user, current_tenant.id, "wireguard:read", db):
        raise HTTPException(status_code=403, detail="Not authorized")

    tunnel = db.query(WireGuardTunnel).filter_by(
        id=tunnel_id, tenant_id=current_tenant.id
    ).first()
    if not tunnel:
        raise HTTPException(status_code=404, detail="Tunnel not found")

    peers = (
        db.query(WireGuardPeer)
        .filter_by(tunnel_id=tunnel_id)
        .order_by(WireGuardPeer.id.asc())
        .all()
    )
    return {"total": len(peers), "peers": [_peer_to_dict(p) for p in peers]}


@router.post(
    "/tunnels/{tunnel_id}/peers",
    response_model=WireGuardPeerResponse,
    status_code=201,
)
async def create_peer(
    tunnel_id: int,
    body: WireGuardPeerCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(current_user, current_tenant.id, "wireguard:create", db):
        raise HTTPException(status_code=403, detail="Not authorized")

    tunnel = db.query(WireGuardTunnel).filter_by(
        id=tunnel_id, tenant_id=current_tenant.id
    ).first()
    if not tunnel:
        raise HTTPException(status_code=404, detail="Tunnel not found")
    if tunnel.status != "active":
        raise HTTPException(
            status_code=400,
            detail=f"Tunnel is in status '{tunnel.status}', not 'active'. Cannot add peer.",
        )

    if db.query(WireGuardPeer).filter_by(tunnel_id=tunnel_id, name=body.name).first():
        raise HTTPException(status_code=409, detail=f"Peer with name '{body.name}' already exists")

    peer = WireGuardPeer(
        tunnel_id=tunnel_id,
        name=body.name,
        public_key="pending",
        private_key_enc="",
        preshared_key_enc="",
        allowed_ip="",
        endpoint=body.endpoint,
        keepalive=body.keepalive or tunnel.peer_keepalive or 25,
        status="pending",
    )
    db.add(peer)
    db.commit()
    db.refresh(peer)

    from app.workers.tasks.wireguard import provision_wireguard_peer_task
    provision_wireguard_peer_task.delay(
        tenant_id=current_tenant.id,
        tunnel_id=tunnel_id,
        peer_id=peer.id,
        actor_id=current_user.id,
        actor_username=current_user.username,
    )

    return peer


@router.get(
    "/tunnels/{tunnel_id}/peers/{peer_id}/config",
    response_model=WireGuardPeerConfigResponse,
)
def get_peer_config(
    tunnel_id: int,
    peer_id: int,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(current_user, current_tenant.id, "wireguard:read", db):
        raise HTTPException(status_code=403, detail="Not authorized")

    tunnel = db.query(WireGuardTunnel).filter_by(
        id=tunnel_id, tenant_id=current_tenant.id
    ).first()
    peer = db.query(WireGuardPeer).filter_by(
        id=peer_id, tunnel_id=tunnel_id
    ).first()
    if not tunnel or not peer:
        raise HTTPException(status_code=404, detail="Tunnel or peer not found")

    client_private_key = decrypt(peer.private_key_enc)
    psk = decrypt(peer.preshared_key_enc)
    dns_line = f"DNS = {tunnel.dns}\n" if tunnel.dns else ""
    allowed_ips = _compute_allowed_ips(tunnel, db)
    conf = (
        "[Interface]\n"
        f"PrivateKey = {client_private_key}\n"
        f"Address = {peer.allowed_ip}/32\n"
        f"{dns_line}"
        "\n"
        "[Peer]\n"
        f"PublicKey = {tunnel.public_key}\n"
        f"PresharedKey = {psk}\n"
        f"Endpoint = {tunnel.endpoint}\n"
        f"AllowedIPs = {allowed_ips}\n"
        f"PersistentKeepalive = {peer.keepalive}\n"
    )
    return WireGuardPeerConfigResponse(
        peer_id=peer.id,
        tunnel_id=tunnel.id,
        name=peer.name,
        config=conf,
        client_private_key=client_private_key,
        client_address=f"{peer.allowed_ip}/32",
        server_public_key=tunnel.public_key,
        server_endpoint=tunnel.endpoint,
        preshared_key=psk,
        allowed_ips=allowed_ips,
        dns=tunnel.dns,
    )


@router.patch(
    "/tunnels/{tunnel_id}/peers/{peer_id}",
    response_model=WireGuardPeerResponse,
)
def update_peer(
    tunnel_id: int,
    peer_id: int,
    body: WireGuardPeerUpdate,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(current_user, current_tenant.id, "wireguard:update", db):
        raise HTTPException(status_code=403, detail="Not authorized")

    peer = db.query(WireGuardPeer).filter_by(
        id=peer_id, tunnel_id=tunnel_id,
    ).first()
    tunnel = db.query(WireGuardTunnel).filter_by(
        id=tunnel_id, tenant_id=current_tenant.id
    ).first()
    if not peer or not tunnel:
        raise HTTPException(status_code=404, detail="Tunnel or peer not found")

    old = {"name": peer.name, "endpoint": peer.endpoint, "keepalive": peer.keepalive, "is_enabled": peer.is_enabled}
    for field in ("name", "endpoint", "keepalive", "is_enabled"):
        value = getattr(body, field, None)
        if value is not None:
            setattr(peer, field, value)
    db.commit()
    db.refresh(peer)

    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["WIREGUARD_PEER_UPDATE"],
        target_type="wireguard_peer",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=peer.id,
        target_name=peer.name,
        old_value=str(old),
        new_value=str({k: getattr(peer, k) for k in old.keys()}),
        tenant_id=current_tenant.id,
    )
    return _peer_to_dict(peer)


@router.delete(
    "/tunnels/{tunnel_id}/peers/{peer_id}",
    status_code=202,
)
def delete_peer(
    tunnel_id: int,
    peer_id: int,
    db: Session = Depends(get_db),
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
):
    if not has_permission(current_user, current_tenant.id, "wireguard:delete", db):
        raise HTTPException(status_code=403, detail="Not authorized")

    peer = db.query(WireGuardPeer).filter_by(
        id=peer_id, tunnel_id=tunnel_id,
    ).first()
    tunnel = db.query(WireGuardTunnel).filter_by(
        id=tunnel_id, tenant_id=current_tenant.id
    ).first()
    if not peer or not tunnel:
        raise HTTPException(status_code=404, detail="Tunnel or peer not found")

    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["WIREGUARD_PEER_DELETE"],
        target_type="wireguard_peer",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=peer.id,
        target_name=peer.name,
        tenant_id=current_tenant.id,
    )
    destroy_wireguard_peer_task.delay(
        tenant_id=current_tenant.id,
        tunnel_id=tunnel.id,
        peer_id=peer.id,
        actor_id=current_user.id,
        actor_username=current_user.username,
    )
    return {"status": "destroying", "peer_id": peer.id, "tunnel_id": tunnel.id}
