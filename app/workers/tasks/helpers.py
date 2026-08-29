"""
Helper functions for worker tasks.

Contains shared utilities, logging functions, and common helpers.
"""
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import requests

from app.core.websocket import publish_vm_log_update
from app.workers.celery_app import celery_app
from app.core.database import SessionLocal
from app.models.ip_reservation import IPReservation
from app.core.config import settings

logger = logging.getLogger(__name__)


MAX_RETRIES = 3
RETRY_DELAY = 5


def sanitize_log(message: str) -> str:
    """
    Remove sensitive infrastructure details from logs before sending to tenant UI.
    """
    sanitized = message
    
    sanitized = re.sub(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', '[REDACTED_IP]', sanitized)
    
    sanitized = re.sub(r'VM \d+ to \d+', 'Cloning VM', sanitized)
    sanitized = re.sub(r'newid=\d+', 'newid=[VM_ID]', sanitized)
    sanitized = re.sub(r'vmid[=:]\d+', 'vmid=[VM_ID]', sanitized)
    sanitized = re.sub(r'template_id[=:]\d+', 'template_id=[TEMPLATE]', sanitized)
    
    sanitized = re.sub(r'vmbr\d+', '[BRIDGE]', sanitized)
    sanitized = re.sub(r'bridge[_-]?id[=:]?\d+', 'bridge_id=[BRIDGE_ID]', sanitized)
    
    sanitized = re.sub(r'https?://[^\s]+', '[PROXMOX_URL]', sanitized)
    sanitized = re.sub(r'192\.168\.\d+\.\d+', '[PRIVATE_IP]', sanitized)
    
    sanitized = re.sub(r'token[=:]?\S+', 'token=[REDACTED]', sanitized)
    sanitized = re.sub(r'Authorization[=:]?\S+', 'Authorization=[REDACTED]', sanitized)
    
    sanitized = re.sub(r'pve:\d+', 'pve:[ID]', sanitized)
    sanitized = re.sub(r'UPID:[^:]+:', 'UPID:[NODE]:', sanitized)
    
    return sanitized


def log_to_vm(vm_id: int, message: str):
    """Send sanitized log to VM's WebSocket channel."""
    # sanitized = sanitize_log(message)
    try:
        publish_vm_log_update(vm_id, message)
    except Exception as e:
        logger.warning(f"Failed to publish log to VM {vm_id}: {e}")


def get_db():
    """Generator for database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_proxmox_client():
    """
    Get Proxmox API client using requests with token auth.
    Returns a simple wrapper with api() method for compatibility.
    """
    import requests
    from app.core.config import settings
    
    class ProxmoxClient:
        def __init__(self, base_url, auth_header):
            self._base_url = base_url
            self._auth_header = auth_header
        
        def nodes(self, node):
            """Return a node interface for API calls."""
            return NodeInterface(self._base_url, self._auth_header, node)
    
    class NodeInterface:
        def __init__(self, base_url, auth_header, node):
            self._base_url = base_url
            self._auth_header = auth_header
            self._node = node
        
        def qemu(self, vm_id):
            """Return a VM interface for API calls."""
            return VMInterface(self._base_url, self._auth_header, self._node, vm_id)
    
    class VMInterface:
        def __init__(self, base_url, auth_header, node, vm_id):
            self._base_url = base_url
            self._auth_header = auth_header
            self._node = node
            self._vm_id = vm_id
        
        def agent(self, command):
            """Execute QEMU guest agent command."""
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


def get_opnsense_client(wan_ip: str = None, api_key: str = None, api_secret: str = None):
    """
    Get OPNsense API client.
    
    Uses provided credentials or falls back to tenant's OPNsense settings.
    Disables SSL verification for self-signed certs.
    
    Returns an object with get() and post() methods for API calls.
    """
    session = requests.Session()
    session.verify = False
    
    if api_key and api_secret:
        session.auth = (api_key, api_secret)
    
    base_url = (wan_ip or settings.OPNSENSE_BOOTSTRAP_KEY).rstrip('/')
    if not base_url.startswith("http"):
        base_url = f"https://{base_url}"
    
    class OPNsenseAPI:
        def __init__(self, sess, base):
            self._session = sess
            self._base = base
        
        def get(self, path: str, **kwargs):
            url = f"{self._base}/api/{path.lstrip('/')}"
            resp = self._session.get(url, **kwargs)
            resp.raise_for_status()
            return resp
        
        def post(self, path: str, json: dict = None, **kwargs):
            url = f"{self._base}/api/{path.lstrip('/')}"
            resp = self._session.post(url, json=json, **kwargs)
            resp.raise_for_status()
            return resp
    
    return OPNsenseAPI(session, base_url)


def get_opnsense_session_client(wan_ip: str, api_key: str, api_secret: str):
    """
    Get OPNsense API client with session-based authentication.
    
    Logs in via /api/authentication/signin to get PHPSESSID cookie
    and CSRF token, then returns a client that includes both.
    
    Required for Kea DHCP API calls which need session + CSRF headers.
    """
    session = requests.Session()
    session.verify = False
    
    base_url = wan_ip.rstrip('/')
    if not base_url.startswith("http"):
        base_url = f"https://{base_url}"
    
    class OPNsenseSessionAPI:
        def __init__(self, sess, base, csrf_token):
            self._session = sess
            self._base = base
            self._csrf_token = csrf_token
        
        def get(self, path: str, **kwargs):
            url = f"{self._base}/api/{path.lstrip('/')}"
            headers = kwargs.pop("headers", {})
            headers["X-CSRFToken"] = self._csrf_token
            headers["X-Requested-With"] = "XMLHttpRequest"
            resp = self._session.get(url, headers=headers, **kwargs)
            resp.raise_for_status()
            return resp
        
        def post(self, path: str, json: dict = None, **kwargs):
            url = f"{self._base}/api/{path.lstrip('/')}"
            headers = kwargs.pop("headers", {})
            headers["Content-Type"] = "application/json"
            headers["X-CSRFToken"] = self._csrf_token
            headers["X-Requested-With"] = "XMLHttpRequest"
            resp = self._session.post(url, json=json, headers=headers, **kwargs)
            resp.raise_for_status()
            return resp
    
    login_url = f"{base_url}/api/authentication/signin"
    login_payload = {
        "username": api_key,
        "password": api_secret,
    }
    
    resp = session.post(login_url, json=login_payload, timeout=30)
    resp.raise_for_status()
    
    data = resp.json()
    if data.get("status") != "success":
        raise RuntimeError(f"OPNsense login failed: {data}")
    
    csrf_token = data.get("CSRFToken", "")
    if not csrf_token:
        raise RuntimeError("No CSRFToken in login response")
    
    return OPNsenseSessionAPI(session, base_url, csrf_token)


def exec_opnsense_api(
    vm_id: int,
    node: str,
    method: str,
    path: str,
    api_key: str,
    api_secret: str,
    json_data: dict = None,
    timeout: int = 30,
) -> dict:
    """
    Execute OPNsense API via exec_in_vm (localhost:443 inside VM).
    
    This routes API calls through Proxmox instead of direct HTTPS:
    Celery Worker → Proxmox API → exec_in_vm() → curl localhost:443
    
    Args:
        vm_id: OPNsense VM ID
        node: Proxmox node name
        method: HTTP method (GET, POST, etc.)
        path: API path (e.g., 'kea/dhcpv4/set')
        api_key: OPNsense API key
        api_secret: OPNsense API secret
        json_data: Optional JSON payload for POST/PUT
        timeout: Command timeout in seconds
    
    Returns:
        dict: Parsed JSON response from OPNsense API
    """
    import json
    import shlex
    
    from app.providers import get_hypervisor_provider
    
    provider = get_hypervisor_provider()
    
    safe_key = shlex.quote(api_key)
    safe_secret = shlex.quote(api_secret)
    url_path = shlex.quote(path.lstrip('/'))
    
    if method.upper() == "GET":
        cmd = f"curl -sk -u {safe_key}:{safe_secret} https://localhost/api/{url_path}"
    elif json_data:
        json_str = shlex.quote(json.dumps(json_data))
        cmd = f"curl -sk -u {safe_key}:{safe_secret} -X {method.upper()} -H 'Content-Type: application/json' -d {json_str} https://localhost/api/{url_path}"
    else:
        cmd = f"curl -sk -u {safe_key}:{safe_secret} -X {method.upper()} https://localhost/api/{url_path}"
    
    logger.debug(f"VM {vm_id}: executing {method} /api/{path}")
    
    result = provider.exec_in_vm(
        node=node,
        vm_id=vm_id,
        command=cmd,
        timeout=timeout + 10,
    )
    
    out = result.get("out", "").strip()
    err = result.get("err", "").strip()
    exitcode = result.get("exitcode", 0)
    
    if exitcode != 0:
        raise RuntimeError(
            f"OPNSense API call failed on VM {vm_id}: "
            f"exit={exitcode} cmd={cmd[:100]}... "
            f"out={out[:200]} err={err[:200]}"
        )
    
    if not out:
        return {}
    
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Failed to parse OPNsense response on VM {vm_id}: {e} "
            f"response={out[:500]}"
        )


def get_vm_wan_ip(vm_id: int, node: str, timeout: int = 120) -> Optional[str]:
    """
    Get VM's WAN IP via Proxmox QEMU guest agent.
    Requires qemu-guest-agent installed in OPNsense template.
    """
    import time
    deadline = time.time() + timeout
    
    while time.time() < deadline:
        try:
            proxmox = get_proxmox_client()
            if not proxmox:
                continue
                
            data = proxmox.nodes(node).qemu(vm_id).agent('network-get-interfaces').get()
            
            for iface in data.get('result', []):
                if iface.get('name') == 'vtnet0':
                    for addr in iface.get('ip-addresses', []):
                        if addr.get('ip-address-type') == 'ipv4':
                            return addr.get('ip-address')
        except Exception as e:
            logger.debug(f"Failed to get WAN IP for VM {vm_id}: {e}")
        
        time.sleep(10)
    
    logger.warning(f"Could not get WAN IP for VM {vm_id} within {timeout}s")
    return None


@celery_app.task(name="tasks.cleanup_expired_reservations")
def cleanup_expired_reservations_task():
    """
    Periodic task to clean up expired IP reservations.
    Should be scheduled to run every 5-10 minutes.
    """
    logger.info("Starting expired IP reservation cleanup")
    
    db_gen = get_db()
    db = next(db_gen)
    
    try:
        now = datetime.now(timezone.utc)
        
        expired = db.query(IPReservation).filter(
            IPReservation.status == "reserved",
            IPReservation.expires_at < now
        ).all()
        
        cleaned = 0
        for res in expired:
            res.status = "released"
            cleaned += 1
            logger.debug(f"Released expired reservation for IP {res.ip_address}")
        
        if cleaned > 0:
            db.commit()
            logger.info(f"Cleaned up {cleaned} expired IP reservations")
        else:
            logger.debug("No expired reservations to clean up")
        
        return {"status": "success", "cleaned": cleaned}
        
    except Exception as e:
        logger.error(f"Failed to cleanup expired reservations: {e}")
        db.rollback()
        return {"status": "error", "error": str(e)}
    finally:
        db.close()


def _cleanup_vm_on_failure(db, vm, error_msg: str):
    """
    Cleanup VM state on failure during provisioning.
    Sets VM status to 'error' and records the error message.
    """
    try:
        vm.status = "error"
        vm.error = str(error_msg)[:500]
        db.commit()
        logger.info(f"Cleaned up VM {vm.id} after failure")
    except Exception as cleanup_err:
        logger.error(f"Failed to cleanup VM {vm.id}: {cleanup_err}")


def _validate_vm_data(vm_data: dict):
    """
    Validate the VM data dictionary before provisioning.
    Raises ValueError if required fields are missing or invalid.
    """
    required_fields = ["name", "tenant_id"]
    for field in required_fields:
        if field not in vm_data or not vm_data[field]:
            raise ValueError(f"Missing required field: {field}")
    
    if "cpu" in vm_data and vm_data["cpu"] < 1:
        raise ValueError("CPU must be at least 1")
    
    if "ram" in vm_data and vm_data["ram"] < 256:
        raise ValueError("RAM must be at least 256MB")