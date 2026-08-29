"""
Tenant provisioning tasks.

Contains tasks for provisioning and destroying tenant OPNsense VMs.
"""
import logging
import time
from datetime import datetime, timezone

from celery import Task
from typing import Optional
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.tenant import Tenant, TenantStatus
from app.workers.celery_app import celery_app
from app.providers import get_hypervisor_provider
from app.core.audit import log_audit_event, AUDIT_ACTIONS
from app.workers.modules.opnsense_config_invm import OPNsenseConfigInVM
from app.workers.tasks.kea import configure_kea_dhcp
from app.core.websocket import publish_tenant_log_update, publish_status_update

logger = logging.getLogger(__name__)


def _check_ip_collision(provider, bridge_name: str, ip: str) -> bool:
    """
    Check if an IP is already in use on the network via Proxmox node shell.
    Returns True if collision (in use), False if free.
    """
    import requests
    from app.core.config import settings

    try:
        auth_header = {
            "Authorization": f"PVEAPIToken={settings.PROXMOX_USERNAME}={settings.PROXMOX_TOKEN}"
        }
        base_url = settings.PROXMOX_URL
        if not base_url.startswith("http"):
            base_url = f"https://{base_url}"
        if ":8006" not in base_url:
            base_url = f"{base_url}:8006"
        node = settings.PROXMOX_NODE

        cmd = f"ip neigh flush dev {bridge_name} {ip} 2>/dev/null; ip neigh show {ip}"
        url = f"{base_url}/api2/json/nodes/{node}/execute"
        resp = requests.post(
            url,
            headers=auth_header,
            json={"command": ["/bin/sh", "-c", cmd]},
            verify=False,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        pid = data.get("data", {}).get("pid")

        if not pid:
            logger.warning("No PID from execute, falling back to ping check")
            return _ping_ip_check(provider, ip)

        for _ in range(20):
            time.sleep(1)
            status_url = f"{base_url}/api2/json/nodes/{node}/tasks/{pid}/status"
            status_resp = requests.get(status_url, headers=auth_header, verify=False, timeout=10)
            task_status = status_resp.json().get("data", {}).get("status")

            if task_status == "stopped":
                output_url = f"{base_url}/api2/json/nodes/{node}/tasks/{pid}/log"
                output_resp = requests.get(output_url, headers=auth_header, verify=False, timeout=10)
                output_lines = output_resp.json().get("data", [])

                output_text = "\n".join(line.get("t", "") for line in output_lines)

                for line in output_text.split("\n"):
                    if ip in line and ("REACHABLE" in line or "STALE" in line):
                        logger.warning(f"IP {ip} in use (ARP: {line.strip()})")
                        return True
                    if ip in line and "FAILED" in line:
                        logger.warning(f"IP {ip} ARP failed, checking ping")
                        return _ping_ip_check(provider, ip)
                return False

        logger.warning("ARP check timeout, falling back to ping")
        return _ping_ip_check(provider, ip)

    except Exception as e:
        logger.warning(f"ARP check failed ({e}), falling back to ping")
        return _ping_ip_check(provider, ip)


def _ping_ip_check(provider, ip: str) -> bool:
    """Fallback: check if IP responds to ping."""
    import requests
    from app.core.config import settings

    try:
        auth_header = {
            "Authorization": f"PVEAPITOKEN={settings.PROXMOX_USERNAME}={settings.PROXMOX_TOKEN}"
        }
        base_url = settings.PROXMOX_URL
        if not base_url.startswith("http"):
            base_url = f"https://{base_url}"
        if ":8006" not in base_url:
            base_url = f"{base_url}:8006"
        node = settings.PROXMOX_NODE

        cmd = f"ping -c 2 -W 1 {ip}"
        url = f"{base_url}/api2/json/nodes/{node}/execute"
        resp = requests.post(
            url,
            headers=auth_header,
            json={"command": ["/bin/sh", "-c", cmd]},
            verify=False,
            timeout=30,
        )
        resp.raise_for_status()
        pid = resp.json().get("data", {}).get("pid")

        if not pid:
            return False

        for _ in range(10):
            time.sleep(1)
            status_url = f"{base_url}/api2/json/nodes/{node}/tasks/{pid}/status"
            status_resp = requests.get(status_url, headers=auth_header, verify=False, timeout=10)
            task_status = status_resp.json().get("data", {}).get("status")

            if task_status == "stopped":
                exitcode = status_resp.json().get("data", {}).get("exitcode")
                if exitcode == 0:
                    logger.warning(f"IP {ip} responds to ping - in use")
                    return True
                return False
        return False

    except Exception as e:
        logger.warning(f"Ping check failed: {e}")
        return False


def _get_bootstrap_auth_header() -> str:
    """Generate Basic Auth header for OPNsense bootstrap key."""
    import base64
    from app.core.config import settings
    creds = f"{settings.OPNSENSE_BOOTSTRAP_KEY}:{settings.OPNSENSE_BOOTSTRAP_SECRET}"
    return base64.b64encode(creds.encode()).decode()


def wait_for_opnsense(vm_id: int, node: str, api_key: str, api_secret: str, max_retries: int = 60) -> bool:
    """
    Poll OPNsense API via exec_in_vm until it responds or max retries reached.
    
    Routes through Proxmox: Celery → Proxmox API → exec_in_vm() → OPNsense localhost:443
    """
    import base64
    
    from app.core.config import settings
    from app.workers.tasks.helpers import exec_opnsense_api
    
    retries = 0

    while retries < max_retries:
        try:
            exec_opnsense_api(
                vm_id=vm_id,
                node=node,
                method="GET",
                path="core/firmware/status",
                api_key=api_key,
                api_secret=api_secret,
                timeout=10,
            )
            logger.info(f"OPNsense VM {vm_id} is up")
            return True
        except Exception as e:
            logger.debug(f"OPNsense not ready (attempt {retries + 1}): {e}")

        time.sleep(3)
        retries += 1

    raise TimeoutError(f"OPNsense VM {vm_id} did not respond after {max_retries} attempts")


def rotate_opnsense_api_key(provider, vm_id: int) -> tuple[str, str]:
    """Rotate OPNsense API key for a tenant."""
    result = provider.exec_in_vm(vm_id, "php /conf/rotate_keys.php")
    output = result.get("out", "")

    api_key = None
    api_secret = None
    for line in output.split("\n"):
        if line.startswith("APIKEY="):
            api_key = line.split("=", 1)[1].strip()
        elif line.startswith("APISECRET="):
            api_secret = line.split("=", 1)[1].strip()

    if not api_key or not api_secret:
        raise RuntimeError(f"Failed to parse API key: {output!r}")

    logger.info(f"API key rotated for VM {vm_id}")
    return api_key, api_secret


def _get_wan_ip(provider, vm_id: int, timeout: int = 120) -> Optional[str]:
    """Get VM's WAN IP via Proxmox QEMU guest agent."""
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            interfaces = provider.get_vm_interfaces(vm_id)
            if interfaces is None:
                logger.debug(f"VM {vm_id} not running or guest agent not ready")
                time.sleep(3)
                continue

            for iface in interfaces:
                if iface.name == "vtnet0" and iface.ip:
                    return iface.ip

            logger.debug(f"Waiting for WAN IP on VM {vm_id} (vtnet0 has no IPv4 yet)")

        except Exception as e:
            logger.debug(f"Failed to get WAN IP: {e}")

        time.sleep(3)

    logger.warning(f"Could not get WAN IP within {timeout}s")
    return None


def get_proxmox_client():
    """Get Proxmox API client."""
    import requests
    from app.core.config import settings

    class ProxmoxClient:
        def __init__(self, base_url, auth_header):
            self._base_url = base_url
            self._auth_header = auth_header

        def nodes(self, node):
            return NodeInterface(self._base_url, self._auth_header, node)

    class NodeInterface:
        def __init__(self, base_url, auth_header, node):
            self._base_url = base_url
            self._auth_header = auth_header
            self._node = node

        def qemu(self, vm_id):
            return VMInterface(self._base_url, self._auth_header, self._node, vm_id)

    class VMInterface:
        def __init__(self, base_url, auth_header, node, vm_id):
            self._base_url = base_url
            self._auth_header = auth_header
            self._node = node
            self._vm_id = vm_id

        def agent(self, command):
            class AgentInterface:
                def __init__(self, base_url, auth_header, node, vm_id, command):
                    self._base_url = base_url
                    self._auth_header = auth_header
                    self._node = node
                    self._vm_id = vm_id
                    self._command = command

                def get(self):
                    url = f"{self._base_url}/api2/json/nodes/{self._node}/qemu/{self._vm_id}/agent/{self._command}"
                    resp = requests.get(url, headers=self._auth_header, verify=False, timeout=30)
                    resp.raise_for_status()
                    return resp.json()

            return AgentInterface(base_url, auth_header, node, vm_id, command)

    try:
        auth_header = {
            "Authorization": f"PVEAPIToken={settings.PROXMOX_USERNAME}={settings.PROXMOX_TOKEN}"
        }
        base_url = settings.PROXMOX_URL
        if not base_url.startswith("http"):
            base_url = f"https://{base_url}"
        if ":8006" not in base_url:
            base_url = f"{base_url}:8006"

        return ProxmoxClient(base_url, auth_header)
    except Exception as e:
        logger.warning(f"Failed to get Proxmox client: {e}")
        return None


def _rollback_tenant_provisioning_v2(db: Session, tenant: Tenant, error_msg: str):
    """Rollback tenant provisioning on failure."""
    logger.warning(f"Rolling back tenant {tenant.id}: {error_msg}")

    if tenant.opnsense_vm_id:
        try:
            provider = get_hypervisor_provider()
            provider.delete_vm(tenant.opnsense_vm_id)
        except Exception as e:
            logger.error(f"Failed to destroy VM: {e}")

    tenant.status = TenantStatus.ERROR
    tenant.error = error_msg
    db.commit()


def _do_provision_tenant(tenant_id: int, template_id: int, pod_id: int = None,
                       bridge_id: int = None, gateway_ip: str = None, cidr: str = None):
    """
    Provision a tenant's OPNsense VM.

    Linear flow:
      1. Clone OPNsense template VM
      2. Wait for WAN IP via DHCP (guest agent)
      3. Set LAN IP (172.x.x.x) via in-VM config
      4. Wait for OPNsense API to come up
      5. Rotate API credentials
      6. Configure Kea DHCP for the LAN subnet
      7. Mark tenant ACTIVE
    """
    import ipaddress as ipmod
    from app.core.config import settings

    logger.info(f"[Tenant {tenant_id}] Provisioning started - template_id={template_id}")
    publish_tenant_log_update(tenant_id, f"Provisioning started - template_id={template_id}")
    publish_status_update("tenant", tenant_id, "provisioning", "provisioning")

    db = SessionLocal()
    provider = get_hypervisor_provider()
    vm = None

    try:
        # ── Guard: tenant must exist and be in PROVISIONING state ──────────
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            return {"status": "error", "error": "Tenant not found"}
        if tenant.status != TenantStatus.PROVISIONING:
            return {"status": "error", "error": f"Invalid state: {tenant.status}"}

        # ── Check node has enough memory ────────────────────────────────────
        node_status = provider.get_node_status()
        required_mb = settings.OPNSENSE_MIN_MEMORY_MB
        if node_status.free_memory_mb < required_mb:
            raise ValueError(f"Only {node_status.free_memory_mb}MB free, need {required_mb}MB")

        # ── Step 1: Clone OPNsense template ─────────────────────────────────
        bridge_name = f"vmbr{tenant.bridge_id}"
        logger.info(f"[Tenant {tenant_id}] Cloning template {template_id} → VM {tenant.opnsense_vm_id} on {bridge_name}")
        publish_tenant_log_update(tenant_id, f"Cloning template {template_id} → VM {tenant.opnsense_vm_id} on {bridge_name}")
        vm = provider.clone_opnsense(
            template_id=template_id,
            new_vm_id=tenant.opnsense_vm_id,
            name=tenant.opnsense_vm_name,
            lan_bridge=bridge_name,
        )

        # ── Step 2: Wait for WAN IP via DHCP ────────────────────────────────
        # Poll the guest agent for vtnet0's IPv4 address.
        # No background task — all polling happens inline so rollback is clean.
        logger.info(f"[Tenant {tenant_id}] Waiting for WAN IP on VM {vm.vm_id}...")
        publish_tenant_log_update(tenant_id, f"Waiting for WAN IP on VM {vm.vm_id}...")
        wan_ip = _get_wan_ip(provider, vm.vm_id, timeout=300)
        if not wan_ip:
            raise ValueError("Timeout waiting for WAN IP via DHCP")

        logger.info(f"[Tenant {tenant_id}] Got WAN IP: {wan_ip}")
        publish_tenant_log_update(tenant_id, f"Got WAN IP: {wan_ip}")
        tenant.wan_ip = wan_ip
        tenant.wan_ip_last_changed_at = datetime.utcnow()
        db.commit()

        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["WAN_IP_ASSIGNED"],
            target_type="tenant",
            actor_id=None,
            actor_username="system",
            target_id=tenant.id,
            target_name=tenant.name,
            new_value=wan_ip,
            details=f"DHCP WAN assigned to VM {vm.vm_id}",
        )

        # ── Step 3: Set LAN IP (172.x.x.x) via in-VM config ────────────────
        # gateway_ip and cidr come from the tenant's network allocation
        # e.g. gateway_ip="172.16.1.1", cidr="172.16.1.0/24"
        lan_gateway = gateway_ip or tenant.lan_ip
        lan_cidr = cidr or (f"{tenant.lan_ip}/24" if tenant.lan_ip else None)

        if not lan_gateway or not lan_cidr:
            raise ValueError("No LAN IP/CIDR configured for tenant — cannot set LAN interface")

        net = ipmod.ip_network(lan_cidr, strict=False)
        lan_prefixlen = net.prefixlen

        cfg = OPNsenseConfigInVM(
            provider=provider,
            vm_id=vm.vm_id,
            node=settings.PROXMOX_NODE,
            config_path=settings.OPNSENSE_CONFIG_PATH,
        )

        logger.info(f"[Tenant {tenant_id}] Setting LAN IP: {lan_gateway}/{lan_prefixlen}")
        publish_tenant_log_update(tenant_id, f"Setting LAN IP: {lan_gateway}/{lan_prefixlen}")
        for attempt in range(3):
            try:
                cfg.set_lan_ip(ip=lan_gateway, subnet=lan_prefixlen)
                cfg.reload_config()
                break
            except Exception as e:
                logger.warning(f"[Tenant {tenant_id}] LAN IP set attempt {attempt + 1}/3 failed: {e}")
                if attempt == 2:
                    raise RuntimeError(f"LAN IP configuration failed after 3 attempts: {e}")
                time.sleep(2)

        tenant.lan_ip = lan_gateway
        db.commit()
        logger.info(f"[Tenant {tenant_id}] LAN IP set: {lan_gateway}/{lan_prefixlen}")
        publish_tenant_log_update(tenant_id, f"LAN IP configured: {lan_gateway}/{lan_prefixlen}")

# ── Step 4: Wait for OPNsense API to come up ────────────────────────
        logger.info(f"[Tenant {tenant_id}] Waiting for OPNsense API on VM {vm.vm_id}...")
        publish_tenant_log_update(tenant_id, f"Waiting for OPNsense API to come up...")
        wait_for_opnsense(
            vm_id=vm.vm_id,
            node=settings.PROXMOX_NODE,
            api_key=settings.OPNSENSE_BOOTSTRAP_KEY,
            api_secret=settings.OPNSENSE_BOOTSTRAP_SECRET,
            max_retries=60,
        )

        # ── Step 5: Rotate API credentials ──────────────────────────────────
        if not tenant.opnsense_api_key:
            try:
                api_key, api_secret = rotate_opnsense_api_key(provider, vm.vm_id)
                tenant.opnsense_api_key = api_key
                tenant.opnsense_api_secret = api_secret
                db.commit()
                logger.info(f"[Tenant {tenant_id}] API credentials rotated")
                publish_tenant_log_update(tenant_id, "API credentials rotated")
            except Exception as e:
                logger.error(f"[Tenant {tenant_id}] API key rotation failed: {e}")
                raise RuntimeError(f"API key rotation failed: {e}")

        # ── Step 6: Configure Kea DHCP for the LAN subnet ───────────────────
        if not tenant.dhcp_pool_start or not tenant.dhcp_pool_end:
            tenant.dhcp_pool_start = str(net[10])
            tenant.dhcp_pool_end = str(net[200])
            db.commit()

        logger.info(
            f"[Tenant {tenant_id}] Configuring Kea DHCP: {lan_cidr} "
            f"pool {tenant.dhcp_pool_start}–{tenant.dhcp_pool_end}"
        )
        publish_tenant_log_update(tenant_id, f"Configuring Kea DHCP: {lan_cidr} pool {tenant.dhcp_pool_start}–{tenant.dhcp_pool_end}")
        configure_kea_dhcp(
            vm_id=vm.vm_id,
            node=settings.PROXMOX_NODE,
            api_key=tenant.opnsense_api_key,
            api_secret=tenant.opnsense_api_secret,
            lan_cidr=lan_cidr,
            lan_gateway=lan_gateway,
            pool_start=tenant.dhcp_pool_start,
            pool_end=tenant.dhcp_pool_end,
            tenant_id=tenant_id,
        )
        logger.info(f"[Tenant {tenant_id}] Kea DHCP configured")
        publish_tenant_log_update(tenant_id, f"DHCP configured: {lan_cidr} pool {tenant.dhcp_pool_start}–{tenant.dhcp_pool_end}")

        # ── Step 7: Mark tenant ACTIVE ───────────────────────────────────────
        tenant.status = TenantStatus.ACTIVE
        tenant.provisioned_at = datetime.utcnow()
        db.commit()
        publish_status_update("tenant", tenant_id, "provisioning", "active")

        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["TENANT_PROVISIONED"],
            target_type="tenant",
            actor_id=None,
            actor_username="system",
            target_id=tenant.id,
            target_name=tenant.name,
            new_value=f"vm_id={vm.vm_id}, wan_ip={wan_ip}, lan={lan_gateway}/{lan_prefixlen}",
            details="Tenant provisioned successfully",
        )

        logger.info(f"[Tenant {tenant_id}] Done — VM {vm.vm_id}, WAN {wan_ip}, LAN {lan_gateway}/{lan_prefixlen}")
        publish_tenant_log_update(tenant_id, f"Tenant provisioned successfully — VM {vm.vm_id}, WAN {wan_ip}, LAN {lan_gateway}/{lan_prefixlen}")
        return {
            "status": "success",
            "tenant_id": tenant_id,
            "vm_id": vm.vm_id,
            "wan_ip": wan_ip,
            "lan_ip": lan_gateway,
            "lan_cidr": lan_cidr,
        }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"[Tenant {tenant_id}] Provisioning failed: {error_msg}")
        publish_tenant_log_update(tenant_id, f"Tenant provisioning failed: {error_msg}", level="error")
        publish_status_update("tenant", tenant_id, "provisioning", "error")
        if tenant:
            _rollback_tenant_provisioning_v2(db, tenant, error_msg)
        raise

    finally:
        db.close()


@celery_app.task(name="tasks.provision_tenant", bind=True, max_retries=0, time_limit=600, soft_time_limit=540)
def provision_tenant_task(self, tenant_id: int, pod_id: int = None, bridge_id: int = None,
                    gateway_ip: str = None, cidr: str = None, template_id: int = None):
    """Provision a tenant's OPNsense VM."""
    from celery.exceptions import SoftTimeLimitExceeded
    from app.core.config import settings

    try:
        _do_provision_tenant(tenant_id, template_id or settings.OPNSENSE_TEMPLATE_ID,
                         pod_id, bridge_id, gateway_ip, cidr)
    except SoftTimeLimitExceeded:
        logger.error(f"Task timeout for tenant {tenant_id}")
        db = SessionLocal()
        try:
            tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
            if tenant:
                _rollback_tenant_provisioning_v2(db, tenant, "Task timeout")
        finally:
            db.close()
        raise


@celery_app.task(name="tasks.destroy_tenant", bind=True, max_retries=0, time_limit=300)
def destroy_tenant_task(self, tenant_id: int):
    """Destroy a tenant."""
    from app.services.provisioning import destroy_tenant as svc_destroy_tenant

    db = SessionLocal()
    try:
        result = svc_destroy_tenant(db, tenant_id)
        logger.info(f"Tenant {tenant_id} destroyed: {result}")
        return result
    except Exception as e:
        logger.error(f"Failed to destroy: {e}")
        raise
    finally:
        db.close()


@celery_app.task(name="tasks.poll_opnsense_wan_ip", bind=True, max_retries=30, default_retry_delay=10)
def poll_opnsense_wan_ip_task(self, tenant_id: int, vm_id: int = None, node: str = "pve"):
    """Poll for OPNsense WAN IP after provisioning."""
    db = SessionLocal()

    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            return {"status": "error", "error": "Tenant not found"}

        if not vm_id:
            vm_id = tenant.opnsense_vm_id

        if not vm_id:
            return {"status": "error", "error": "No VM ID"}

        logger.info(f"[Tenant {tenant_id}] Polling WAN IP for VM {vm_id}")

        provider = get_hypervisor_provider()
        interfaces = provider.get_vm_interfaces(vm_id)

        for iface in interfaces:
            if iface.name == "vtnet0" and iface.ip:
                wan_ip = iface.ip

                old_wan_ip = tenant.wan_ip
                tenant.wan_ip = wan_ip
                tenant.wan_ip_last_changed_at = datetime.utcnow()
                db.commit()

                logger.info(f"[Tenant {tenant_id}] WAN IP: {wan_ip}")

                if old_wan_ip != wan_ip:
                    log_audit_event(
                        db=db,
                        action=AUDIT_ACTIONS["WAN_IP_ASSIGNED"],
                        target_type="tenant",
                        actor_id=None,
                        actor_username="system",
                        target_id=tenant.id,
                        target_name=tenant.name,
                        old_value=old_wan_ip,
                        new_value=wan_ip,
                        details=f"DHCP WAN: {wan_ip}",
                    )

                if not tenant.opnsense_api_key:
                    try:
                        api_key, api_secret = rotate_opnsense_api_key(provider, vm_id)
                        tenant.opnsense_api_key = api_key
                        tenant.opnsense_api_secret = api_secret
                        db.commit()
                    except Exception as api_err:
                        logger.error(f"API key rotate failed: {api_err}")

                return {
                    "status": "success",
                    "tenant_id": tenant_id,
                    "vm_id": vm_id,
                    "wan_ip": wan_ip,
                }

        logger.info(f"[Tenant {tenant_id}] No WAN IP, retrying")
        raise self.retry(countdown=10)

    except Exception as e:
        from celery.exceptions import Retry
        if isinstance(e, Retry):
            raise
        logger.warning(f"WAN IP query failed: {e}")
        raise self.retry(countdown=10, exc=e)

    finally:
        db.close()


@celery_app.task(name="tasks.sync_all_wan_ips", bind=True, time_limit=600)
def sync_all_wan_ips_task(self):
    """Periodic task to sync WAN IPs for all active tenants."""
    db = SessionLocal()
    checked = 0
    updated = 0

    try:
        tenants = db.query(Tenant).filter(
            Tenant.status == TenantStatus.ACTIVE,
            Tenant.opnsense_vm_id.isnot(None),
        ).all()

        provider = get_hypervisor_provider()

        for tenant in tenants:
            try:
                checked += 1
                vm_id = tenant.opnsense_vm_id


                interfaces = provider.get_vm_interfaces(vm_id)
                
                if interfaces is None:
                    logger.debug(f"VM {vm_id} not running, skipping")
                    continue
                
                current_wan_ip = None
                
                for iface in interfaces:
                    if iface.name == "vtnet0" and iface.ip:
                        current_wan_ip = iface.ip
                        break

                if not current_wan_ip:
                    logger.debug(f"VM {vm_id} has no WAN IP yet")
                    continue

                old_wan_ip = tenant.wan_ip

                if current_wan_ip != old_wan_ip:
                    tenant.wan_ip = current_wan_ip
                    tenant.wan_ip_last_changed_at = datetime.utcnow()
                    db.commit()

                    logger.warning(f"[Drift Correction] Tenant {tenant.id} WAN changed: {old_wan_ip} -> {current_wan_ip}")

                    log_audit_event(
                        db=db,
                        action=AUDIT_ACTIONS["WAN_IP_CHANGED"],
                        target_type="tenant",
                        actor_id=None,
                        actor_username="system/drift-correction",
                        target_id=tenant.id,
                        target_name=tenant.name,
                        old_value=old_wan_ip,
                        new_value=current_wan_ip,
                        details=f"WAN IP changed from {old_wan_ip} to {current_wan_ip} for VM {vm_id}",
                    )

                    updated += 1

            except Exception as e:
                logger.error(f"Error processing tenant {tenant.id}: {e}")
                continue

        logger.info(f"WAN IP sync complete: {checked} checked, {updated} updated")
        return {"status": "success", "checked": checked, "updated": updated}

    except Exception as e:
        logger.error(f"WAN IP sync failed: {e}")
        raise

    finally:
        db.close()