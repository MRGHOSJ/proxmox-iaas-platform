import time
import logging
import requests
import urllib3
import urllib.parse
import re
from typing import List, Optional
from passlib.hash import sha512_crypt
from urllib.parse import quote
from app.providers.base import (
    HypervisorProvider,
    ProviderType,
    BridgeResult,
    VMResult,
    InterfaceInfo,
    NodeStatus,
)
from app.core.config import settings

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


class ProxmoxProvider(HypervisorProvider):
    """
    Proxmox implementation of HypervisorProvider.
    Uses raw requests for ALL operations (bypasses proxmoxer bugs).
    """

    def __init__(self, host: str = None, db=None):
        target = host or settings.PROXMOX_URL
        
        # Normalize URL
        if not target.startswith("http"):
            target = f"https://{target}"
        if ":8006" not in target:
            target = f"{target}:8006"
        if not target.endswith("/"):
            target = target + "/"
        
        self._base_url = target  # e.g. https://192.168.100.51:8006/
        self._node = settings.PROXMOX_NODE
        self._storage = settings.PROXMOX_STORAGE
        self.db = db  # Store database session for network lookups
        self._stable_agents: set = set()
        
        # Build proper Proxmox API token auth header
        self._auth_header = {
            "Authorization": f"PVEAPIToken={settings.PROXMOX_USERNAME}={settings.PROXMOX_TOKEN}"
        }
        
        logger.info(f"Initializing ProxmoxProvider: {self._base_url}")
        
        # Test API connection
        try:
            response = requests.get(
                f"{self._base_url}api2/json/nodes",
                headers=self._auth_header,
                verify=False,
                timeout=10
            )
            if response.status_code == 200:
                logger.info("Successfully connected to Proxmox API")
            else:
                logger.warning(f"Proxmox API returned status {response.status_code}")
        except Exception as e:
            logger.error(f"Failed to connect to Proxmox API: {e}")

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.PROXMOX

    # ── Direct API helper ──
    def _api(self, method: str, path: str, data: dict = None, json: dict = None, timeout: int = 30):
        """Make direct Proxmox API call using token auth.
        
        For GET requests: uses params (query string)
        For POST/PUT/DELETE: uses data (form body) or json (JSON body)
        Returns the raw response for endpoints that return plain strings (like UPID).
        """
        url = f"{self._base_url}api2/json/{path.lstrip('/')}"
        
        kwargs = {
            "headers": self._auth_header,
            "verify": False,
            "timeout": timeout,
        }
        
        if method.upper() == "GET":
            kwargs["params"] = data
        elif json is not None:
            kwargs["json"] = json  # sends Content-Type: application/json
        else:
            kwargs["data"] = data
        
        # Debug: log the request details for clone operations
        if "clone" in path:
            logger.info(f"DEBUG: Clone request - method={method}, path={path}, data={data}")
        
        try:
            resp = requests.request(method, url, **kwargs)
            resp.raise_for_status()
        except requests.HTTPError as e:
            # Log full error response for debugging
            error_text = e.response.text if e.response else str(e)
            logger.error(f"DEBUG: HTTP {e.response.status_code if e.response else '?'} - {error_text}")
            raise
        
        # Some endpoints return plain strings (like UPID), not JSON
        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            return resp.json().get("data", {})
        else:
            # Return raw text (e.g., UPID string)
            return resp.text

    def _api_delete(self, path: str, params: dict = None) -> dict:
        """DELETE requests — Proxmox doesn't accept a body on DELETE, use query params."""
        url = f"{self._base_url}api2/json/{path.lstrip('/')}"
        resp = requests.delete(
            url,
            headers=self._auth_header,
            params=params,
            verify=False,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

    def _api_full(self, method: str, path: str, params: dict = None) -> dict:
        """Like _api, but returns the full JSON response envelope so the caller
        can read sibling fields like `total` (returned by the task log API)
        alongside the `data` payload."""
        url = f"{self._base_url}api2/json/{path.lstrip('/')}"
        resp = requests.request(
            method,
            url,
            headers=self._auth_header,
            params=params,
            verify=False,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Bridge operations ──
    def _bridge_exists(self, bridge_name: str) -> bool:
        """Check if a bridge already exists on the node."""
        try:
            ifaces = self._api("GET", f"nodes/{self._node}/network")
            return any(i.get("iface") == bridge_name for i in ifaces.get("data", []))
        except Exception:
            return False
    
    def create_bridge(self, bridge_id: int, tenant_id: int) -> BridgeResult:
        bridge_name = f"vmbr{bridge_id}"
        logger.info(f"Creating bridge {bridge_name} on node {self._node}")
        
        if self._bridge_exists(bridge_name):
            logger.info(f"Bridge {bridge_name} already exists, skipping creation")
            return BridgeResult(bridge_name=bridge_name, bridge_id=bridge_id)
        
        try:
            # VLAN-aware bridge - allows tagged traffic for VMs using VLANs
            self._api("POST", f"nodes/{self._node}/network", {
                "type": "bridge",
                "iface": bridge_name,
                "autostart": "1",
                "bridge_vlan_aware": "1",
                "bridge_vids": "2-4094",
                "comments": f"tenant-{tenant_id}-lan",
            })
            logger.info(f"Bridge {bridge_name} created (VLAN-aware)")

            self._api("PUT", f"nodes/{self._node}/network", {})
            logger.info(f"Bridge {bridge_name} applied")

            return BridgeResult(bridge_name=bridge_name, bridge_id=bridge_id)

        except requests.HTTPError as e:
            logger.error(f"HTTP error creating bridge: {e.response.text if e.response else e}")
            raise RuntimeError(f"Failed to create bridge {bridge_name}: {e}")
        except Exception as e:
            logger.error(f"Error creating bridge: {e}")
            raise RuntimeError(f"Failed to create bridge {bridge_name}: {e}")

    def delete_bridge(self, bridge_id: int) -> None:
        bridge_name = f"vmbr{bridge_id}"
        logger.info(f"Deleting bridge {bridge_name}")
        
        if not self._bridge_exists(bridge_name):
            logger.info(f"Bridge {bridge_name} does not exist, skipping deletion")
            return
        
        try:
            self._api("DELETE", f"nodes/{self._node}/network/{bridge_name}")
            self._api("PUT", f"nodes/{self._node}/network")
            logger.info(f"Bridge {bridge_name} deleted")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                logger.warning(f"Bridge {bridge_name} not found, skipping")
            else:
                logger.warning(f"HTTP error deleting bridge: {e.response.text if e.response else e}")
        except Exception as e:
            logger.warning(f"Error deleting bridge: {e}")

    # ── VM operations (use raw requests) ──
    def clone_opnsense(
        self,
        template_id: int,
        new_vm_id: int,
        name: str,
        lan_bridge: str,
    ) -> VMResult:
        logger.info(f"Cloning VM {template_id} to {new_vm_id} ({name})")
        
        try:
            # Sanitize name to be DNS-compatible: alphanumeric and hyphens only
            sanitized_name = re.sub(r'[^a-zA-Z0-9-]', '-', name)
            sanitized_name = sanitized_name.strip('-')
            if not sanitized_name:
                sanitized_name = f"vm-{new_vm_id}"
            logger.info(f"Original name: {name}, sanitized to: {sanitized_name}")
            
            # Pre-encode the data as form-urlencoded string
            data = f"newid={new_vm_id}&name={sanitized_name}&full=1&target={self._node}&storage={self._storage}"
            url = f"{self._base_url}api2/json/nodes/{self._node}/qemu/{template_id}/clone"
            
            # Explicit headers: authentication + content-type
            headers = self._auth_header.copy()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            
            logger.info(f"DEBUG clone data: {data}")
            logger.info(f"DEBUG clone URL: {url}")
            
            resp = requests.post(
                url,
                headers=headers,
                data=data,
                verify=False,
                timeout=60,
            )
            
            # Log the response status and body for debugging
            logger.info(f"Clone response status: {resp.status_code}")
            logger.info(f"Clone response body: {resp.text}")
            
            resp.raise_for_status()
            
            # Extract UPID from JSON response - API returns {"data": "UPID:..."}
            upid_text = resp.text.strip()
            try:
                upid_data = resp.json()
                upid = upid_data.get("data", "")
            except:
                upid = upid_text
            
            logger.info(f"Clone task started: {upid}")
            
            # Wait for clone task to complete before configuring network
            if upid and "UPID:" in str(upid):
                self._wait_for_task(upid, timeout=300)
            
            logger.info(f"VM {new_vm_id} cloned, waiting for lock...")
            time.sleep(5)  # Wait for Proxmox to release the lock
            
            # Configure NICs after clone completes - with retry for lock timeout
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    self._api("PUT", f"nodes/{self._node}/qemu/{new_vm_id}/config", {
                        "net0": "virtio,bridge=vmbr0,firewall=0",
                        "net1": f"virtio,bridge={lan_bridge},firewall=0",
                    })
                    logger.info(f"Network configured for VM {new_vm_id}")
                    break
                except requests.HTTPError as e:
                    if "lock" in str(e).lower() and attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 5
                        logger.warning(f"Lock timeout, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    raise
            
            # Start VM
            start_upid = self._api("POST", f"nodes/{self._node}/qemu/{new_vm_id}/status/start", timeout=60)
            if start_upid and str(start_upid).startswith("UPID:"):
                self._wait_for_task(start_upid, timeout=60)
            logger.info(f"VM {new_vm_id} started")
            
            return VMResult(vm_id=new_vm_id, node=self._node)
            
        except requests.HTTPError as e:
            logger.error(f"HTTP error during clone: {e}")
            raise RuntimeError(f"Failed to clone VM: {e}")
        except RuntimeError:
            raise
        except Exception as e:
            logger.error(f"Error cloning VM: {e}")
            raise RuntimeError(f"Failed to clone VM: {e}")

    def clone_vm_with_cloudinit(
        self,
        template_id: int,
        new_vm_id: int,
        name: str,
        vm_config: Optional[dict] = None,
        lan_bridge: Optional[str] = None,
        username: str = "ubuntu",
        password: Optional[str] = None,
        ssh_public_key: Optional[str] = None,
        ip_mode: str = "dhcp",
        ip_address: Optional[str] = None,
        gateway: str = "10.0.0.1",
        dns_nameservers: Optional[List[str]] = None,
        dns_search: Optional[str] = None,
        cpu: int = 1,
        ram: int = 1024,
        auto_start: bool = True,
        skip_cloudinit: bool = False,
    ) -> VMResult:
        """
        Clone a VM template with cloud-init configuration.
        
        Args:
            template_id: The VM ID to clone from
            new_vm_id: The target VM ID
            name: Name for the new VM
            lan_bridge: The tenant's LAN bridge (e.g., vmbr105)
            username: Cloud-init username
            password: Cloud-init password (will be hashed with SHA-512)
            ssh_public_key: SSH public key for cloud-init
            ip_mode: "dhcp" or "static"
            ip_address: Static IP (required if ip_mode is static)
            gateway: Gateway IP
            dns_nameservers: DNS nameservers list
            dns_search: DNS search domain
            cpu: Number of CPU cores
            ram: RAM in MB
            auto_start: Whether to start the VM after provisioning
            
        Returns:
            VMResult with vm_id and node
        """
        logger.info(f"Cloning VM {template_id} to {new_vm_id} ({name}) with cloud-init")
        
        try:
            sanitized_name = re.sub(r'[^a-zA-Z0-9-]', '-', name)
            sanitized_name = sanitized_name.strip('-')
            if not sanitized_name:
                sanitized_name = f"vm-{new_vm_id}"
            logger.info(f"Original name: {name}, sanitized to: {sanitized_name}")
            
            # Clone the VM (async operation)
            data = f"newid={new_vm_id}&name={sanitized_name}&full=1&target={self._node}&storage={self._storage}"
            url = f"{self._base_url}api2/json/nodes/{self._node}/qemu/{template_id}/clone"
            
            headers = self._auth_header.copy()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            
            logger.info(f"Clone request: {url}")
            
            resp = requests.post(
                url,
                headers=headers,
                data=data,
                verify=False,
                timeout=60,
            )
            
            logger.info(f"Clone response status: {resp.status_code}")
            logger.debug(f"Clone response body: {resp.text}")
            
            resp.raise_for_status()
            
            # Extract UPID and wait for clone to complete
            upid_data = resp.json()
            upid = upid_data.get("data", "")
            
            logger.info(f"Clone task started: {upid}")
            
            if upid and "UPID:" in str(upid):
                self._wait_for_task(upid, timeout=300)
            
            logger.info(f"VM {new_vm_id} cloned, waiting for lock...")
            time.sleep(2)
            
            # Build VM config
            config = {}
            
            # NIC - use vm_config if provided, otherwise fall back to lan_bridge
            if vm_config and "net0" in vm_config:
                config["net0"] = vm_config["net0"]
            elif lan_bridge:
                config["net0"] = f"virtio,bridge={lan_bridge},firewall=1"
            else:
                raise ValueError("Either vm_config with net0 or lan_bridge must be provided")
            
            if skip_cloudinit:
                logger.info(f"VM {new_vm_id} is Windows — skipping cloud-init config")
            else:
                # User account
                config["ciuser"] = username
                if password:
                    config["cipassword"] = sha512_crypt.hash(password)
                
                # SSH - URL-encode the key for Proxmox API
                if ssh_public_key:
                    config["sshkeys"] = quote(ssh_public_key, safe="")
                
                # Network - Proxmox expects format: ip=dhcp or ip=x.x.x.x/24,gw=x.x.x.x
                if ip_mode == "dhcp":
                    config["ipconfig0"] = "ip=dhcp"
                else:
                    config["ipconfig0"] = f"ip={ip_address}/24,gw={gateway}"
            
            # Resources (always set regardless of OS)
            config["cores"] = str(cpu)
            config["memory"] = str(ram)
            
            logger.info(f"Applying VM config for VM {new_vm_id}: {config}")
            
            # Apply config with retry for lock timeout
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    self._api("PUT", f"nodes/{self._node}/qemu/{new_vm_id}/config", config)
                    logger.info(f"VM config applied for VM {new_vm_id}")
                    break
                except requests.HTTPError as e:
                    if "lock" in str(e).lower() and attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 5
                        logger.warning(f"Lock timeout, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    raise
            
            # Start VM if requested
            if auto_start:
                logger.info(f"Starting VM {new_vm_id}")
                start_upid = self._api("POST", f"nodes/{self._node}/qemu/{new_vm_id}/status/start", timeout=60)
                if start_upid and str(start_upid).startswith("UPID:"):
                    self._wait_for_task(start_upid, timeout=60)
                logger.info(f"VM {new_vm_id} started")
            
            return VMResult(vm_id=new_vm_id, node=self._node)
            
        except requests.HTTPError as e:
            logger.error(f"HTTP error during cloud-init clone: {e}")
            raise RuntimeError(f"Failed to clone VM with cloud-init: {e}")
        except RuntimeError:
            raise
        except Exception as e:
            logger.error(f"Error during cloud-init clone: {e}")
            raise RuntimeError(f"Failed to clone VM with cloud-init: {e}")

    def delete_vm(self, vm_id: int) -> None:
        logger.info(f"Deleting VM {vm_id}")
        
        try:
            # Stop VM first — ignore error if already stopped
            try:
                self._api("POST", f"nodes/{self._node}/qemu/{vm_id}/status/stop", timeout=60)
                time.sleep(3)
            except Exception:
                pass  # already stopped or doesn't exist

            # DELETE with purge as query param, NO body
            self._api_delete(f"nodes/{self._node}/qemu/{vm_id}", params={"purge": 1, "destroy-unreferenced-disks": 1})
            logger.info(f"VM {vm_id} deleted")
        except Exception as e:
            logger.warning(f"Error deleting VM {vm_id}: {e}")

    def start_vm(self, vm_id: int) -> bool:
        try:
            self._api("POST", f"nodes/{self._node}/qemu/{vm_id}/status/start", timeout=60)
            return True
        except Exception as e:
            logger.error(f"Failed to start VM {vm_id}: {e}")
            return False

    def stop_vm(self, vm_id: int) -> bool:
        try:
            self._api("POST", f"nodes/{self._node}/qemu/{vm_id}/status/stop", timeout=60)
            return True
        except Exception as e:
            logger.error(f"Failed to stop VM {vm_id}: {e}")
            return False

    def get_vm_interfaces(self, vm_id: int) -> List[InterfaceInfo]:
        try:
            data = self._api("GET", f"nodes/{self._node}/qemu/{vm_id}/agent/network-get-interfaces")
            result = []
            for iface in data.get("result", []):
                ip = None
                for addr in iface.get("ip-addresses", []):
                    if addr.get("ip-address-type") == "ipv4":
                        ip = addr.get("ip-address")
                        break
                result.append(InterfaceInfo(name=iface.get("name", ""), ip=ip))
            return result
        except Exception as e:
            logger.debug(f"Could not get interfaces for VM {vm_id}: {e}")
            return []

    def get_node_status(self) -> NodeStatus:
        try:
            data = self._api("GET", f"nodes/{self._node}/status")
            mem = data.get("memory", {})
            
            # Get VM count
            vms_data = self._api("GET", f"nodes/{self._node}/qemu")
            vm_count = len(vms_data) if isinstance(vms_data, list) else 0
            
            return NodeStatus(
                total_memory_mb=mem.get("total", 0) // 1024 // 1024,
                free_memory_mb=mem.get("free", 0) // 1024 // 1024,
                cpu_usage=data.get("cpu", 0.0),
                vm_count=vm_count,
            )
        except Exception as e:
            logger.error(f"Failed to get node status: {e}")
            raise RuntimeError(f"Failed to get node status: {e}")

    def list_templates(self) -> List[dict]:
        """
        List all VMs marked as templates in Proxmox.
        
        Returns:
            List of dicts with vmid, name, and os info
        """
        try:
            data = self._api("GET", "cluster/resources")
            templates = []
            
            # Proxmox returns resources as a list
            if isinstance(data, dict):
                resources = data.get("data", [])
            elif isinstance(data, list):
                resources = data
            else:
                resources = []
            
            for resource in resources:
                if resource.get("type") == "qemu" and resource.get("template") == 1:
                    # Derive OS from template name
                    template_name = resource.get("name", "").lower()
                    os_type = "Template"
                    if "ubuntu" in template_name:
                        os_type = "Ubuntu"
                    elif "debian" in template_name:
                        os_type = "Debian"
                    elif "centos" in template_name:
                        os_type = "CentOS"
                    elif "rocky" in template_name:
                        os_type = "Rocky Linux"
                    elif "alma" in template_name:
                        os_type = "AlmaLinux"
                    elif "windows" in template_name:
                        os_type = "Windows"
                    
                    templates.append({
                        "vmid": resource.get("vmid"),
                        "name": resource.get("name"),
                        "os": os_type,
                        "cores": resource.get("cores", 1),
                        "memory": resource.get("maxmem", 0) // 1024 // 1024,
                        "disk": resource.get("maxdisk", 0) // 1024 // 1024 // 1024,
                    })
            
            logger.info(f"Found {len(templates)} templates")
            return templates
            
        except Exception as e:
            logger.error(f"Failed to list templates: {e}")
            raise RuntimeError(f"Failed to list templates: {e}")

    def list_all_vms(self) -> List[dict]:
        """List all VMs (not just templates) in Proxmox cluster."""
        try:
            data = self._api("GET", "cluster/resources")
            if isinstance(data, dict):
                resources = data.get("data", [])
            elif isinstance(data, list):
                resources = data
            else:
                resources = []

            vms = []
            for resource in resources:
                if resource.get("type") == "qemu":
                    vms.append({
                        "vmid": resource.get("vmid"),
                        "name": resource.get("name"),
                        "status": resource.get("status"),
                        "template": resource.get("template", 0),
                    })
            return vms
        except Exception as e:
            logger.error(f"Failed to list all VMs: {e}")
            return []

    def _wait_for_guest_agent(self, vm_id: int, timeout: int = 120):
        """Wait until QEMU guest agent responds to ping before executing commands."""
        if vm_id in self._stable_agents:
            return

        deadline = time.time() + timeout
        attempt = 0
        consecutive_ok = 0  # require 2 successful pings before proceeding
        
        while time.time() < deadline:
            try:
                self._api("POST", f"nodes/{self._node}/qemu/{vm_id}/agent/ping")
                consecutive_ok += 1
                if consecutive_ok >= 2:
                    logger.info(f"Guest agent is stable on VM {vm_id}")
                    self._stable_agents.add(vm_id)
                    return
                logger.debug(f"Guest agent ping {consecutive_ok}/2 on VM {vm_id}")
                time.sleep(3)  # wait between confirmation pings
            except Exception as e:
                consecutive_ok = 0  # reset on any failure
                attempt += 1
                logger.debug(f"Guest agent not ready on VM {vm_id} (attempt {attempt}): {e}")
                time.sleep(3)
        
        raise TimeoutError(f"Guest agent on VM {vm_id} did not respond within {timeout}s")

    def exec_in_vm(self, vm_id: int, command: str, timeout: int = 60, node: str = None, skip_agent_check: bool = False) -> dict:
        """Execute a command inside a VM via QEMU guest agent."""
        target_node = node or self._node
        
        if not skip_agent_check:
            self._wait_for_guest_agent(vm_id, timeout=120)
        
        # Brief pause after ping — agent needs a moment before it can exec
        # Use configurable setting instead of hardcoded 5 seconds
        settle_delay = getattr(settings, 'GUEST_AGENT_SETTLE_DELAY', 1)
        time.sleep(settle_delay)
        
        max_retries = 3
        effective_timeout = timeout if timeout else 60
        
        for attempt in range(max_retries):
            try:
                # Command MUST be sent as JSON with array format
                result = self._api(
                    "POST",
                    f"nodes/{target_node}/qemu/{vm_id}/agent/exec",
                    json={"command": ["/bin/sh", "-c", command]},
                    timeout=effective_timeout,
                )
                
                pid = result.get("pid")
                if not pid:
                    raise RuntimeError("No PID returned from exec command")
                
                return self._wait_for_exec_output(vm_id, pid, target_node=target_node)
                
            except Exception as e:
                err_str = str(e).lower()
                retryable = any(x in err_str for x in ["broken pipe", "not running", "500", "596"])
                
                if retryable and attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 15
                    logger.warning(f"Exec failed on VM {vm_id}, retrying in {wait_time}s (attempt {attempt+1}/{max_retries}): {e}")
                    time.sleep(wait_time)
                    continue
                
                logger.error(f"Failed to execute command in VM {vm_id}: {e}")
                raise RuntimeError(f"Failed to execute command: {e}")
        
        raise RuntimeError(f"Exec failed after {max_retries} attempts")

    def _wait_for_task(self, upid: str, timeout: int = 300):
        """Poll Proxmox task until complete. UPID looks like 'UPID:pve:...'"""
        if not upid or not str(upid).startswith("UPID:"):
            logger.debug(f"Not a UPID, skipping wait: {upid}")
            return
        
        # URL-encode the full UPID (it contains colons that need encoding)
        encoded_upid = urllib.parse.quote(upid, safe="")
        
        deadline = time.time() + timeout
        
        while time.time() < deadline:
            try:
                # Pass the FULL upid (url-encoded) - NOT split
                status = self._api("GET", f"nodes/{self._node}/tasks/{encoded_upid}/status")
                
                if status.get("status") == "stopped":
                    exit_status = status.get("exitstatus", "")
                    if exit_status != "OK":
                        raise RuntimeError(f"Proxmox task failed: {exit_status}")
                    logger.debug(f"Task {upid} completed OK")
                    return
            except RuntimeError:
                raise
            except Exception as e:
                logger.debug(f"Waiting for task {upid}: {e}")
            
            time.sleep(3)
        
        raise TimeoutError(f"Task {upid} timed out after {timeout}s")

    def _wait_for_exec_output(self, vm_id: int, pid: int, timeout: int = 60, target_node: str = None) -> dict:
        """Wait for command to complete and return output as dict."""
        target_node = target_node or self._node
        deadline = time.time() + timeout
        
        while time.time() < deadline:
            try:
                result = self._api("GET", f"nodes/{target_node}/qemu/{vm_id}/agent/exec-status", {"pid": pid})
                
                if result.get("exited"):
                    exitcode = result.get("exitcode", 0)
                    stdout = result.get("out-data", "")
                    stderr = result.get("err-data", "")
                    
                    return {
                        "out": stdout,
                        "err": stderr,
                        "exitcode": exitcode,
                    }
                    
            except RuntimeError:
                raise
            except Exception as e:
                logger.debug(f"Exec status for PID {pid}: {e}")
            
            time.sleep(2)
        
        raise TimeoutError(f"Command execution timed out after {timeout}s")

    # ── Network abstraction methods ──

    def create_network(self, network) -> str:
        """
        Default (untagged) LAN: creates a dedicated bridge and returns its name.
        Tagged VLAN networks: creates a VLAN interface on the tenant's bridge.
        """
        # Create the default bridge for untagged traffic
        if network.is_default:
            bridge_id = int(network.provider_ref.replace("vmbr", ""))
            self.create_bridge(bridge_id, network.tenant_id)
        
        # Get the tenant's default bridge
        bridge = network.provider_ref
        if not bridge:
            bridge = self._get_default_bridge_for_tenant(network.tenant_id)
        
        # VLAN management is now handled by OPNsense, not Proxmox
        # No need to create VLAN interfaces on Proxmox bridges
        
        return bridge

    def attach_vm_to_network(self, vm_config: dict, network) -> dict:
        """
        Builds the Proxmox NIC string and injects it into vm_config['net0'].
        VLAN tags are set for non-default networks. Firewall disabled because
        OPNsense handles all firewalling.
        """
        bridge = network.provider_ref
        if not bridge:
            bridge = self._get_default_bridge_for_tenant(network.tenant_id)

        nic = f"virtio,bridge={bridge},firewall=0"
        if network.vlan_id is not None:
            nic += f",tag={network.vlan_id}"

        logger.info(f"Attaching VM to network: {nic}")
        vm_config["net0"] = nic
        return vm_config

    def delete_network(self, network) -> None:
        """
        Deletes the bridge only for the default (untagged) network.
        Tagged networks have no dedicated bridge — nothing to tear down.
        """
        if network.is_default and network.provider_ref:
            node = self._get_node_for_pod(network.pod_id)
            self._delete_bridge(network.provider_ref, node=node)

    def _get_default_bridge_for_tenant(self, tenant_id: int) -> str:
        """Looks up the tenant's default network and returns its provider_ref."""
        from app.models.network import TenantNetwork
        network = (
            self.db.query(TenantNetwork)
            .filter_by(tenant_id=tenant_id, is_default=True)
            .first()
        )
        if not network or not network.provider_ref:
            raise ValueError(f"No default bridge found for tenant {tenant_id}")
        return network.provider_ref

    def _get_node_for_pod(self, pod_id: int) -> str:
        """Returns the first Proxmox node name for a pod."""
        from app.models.network import Pod
        pod = self.db.query(Pod).get(pod_id)
        if not pod:
            raise ValueError(f"Pod {pod_id} not found")
        return pod.node_names.split(",")[0]

    def _delete_bridge(self, bridge_name: str, node: str) -> None:
        """Delete a Proxmox bridge."""
        try:
            self._api("DELETE", f"nodes/{node}/network/{bridge_name}")
            self._api("PUT", f"nodes/{node}/network")
            logger.info(f"Bridge {bridge_name} deleted")
        except Exception as e:
            logger.warning(f"Error deleting bridge {bridge_name}: {e}")

    def get_vnc_proxy(self, vm_id: int) -> dict:
        """
        Get VNC proxy connection details from Proxmox.
        Returns port, ticket, upid, and host info for websocket connection.
        """
        logger.info(f"Getting VNC proxy for VM {vm_id}")
        
        response = self._api(
            "POST",
            f"nodes/{self._node}/qemu/{vm_id}/vncproxy",
            data={"websocket": 1}
        )
        
        host = self._base_url.replace("https://", "").replace(":8006", "").rstrip("/")
        
        return {
            "port": response["port"],
            "host": host,
            "ticket": response["ticket"],
            "upid": response.get("upid", ""),
            "node": self._node,
            "vmid": vm_id,
            "console_type": "vnc"
        }

    def get_serial_console(self, vm_id: int) -> dict:
        """
        Get serial console connection details from Proxmox.
        Returns port, ticket, upid, and host info for websocket connection.
        """
        logger.info(f"Getting serial console for VM {vm_id}")
        
        response = self._api(
            "POST",
            f"nodes/{self._node}/qemu/{vm_id}/serialterminal",
            data={"websocket": 1, "console": "serial0"}
        )
        
        host = self._base_url.replace("https://", "").replace(":8006", "").rstrip("/")
        
        return {
            "port": response["port"],
            "host": host,
            "ticket": response["ticket"],
            "upid": response.get("upid", ""),
            "node": self._node,
            "vmid": vm_id,
            "console_type": "serial",
            "desktop_name": "Serial Console"
        }

    def get_vm_config(self, vm_id: int) -> dict:
        """
        Get VM configuration to check for serial console settings.
        """
        logger.info(f"Getting VM config for VM {vm_id}")
        
        response = self._api(
            "GET",
            f"nodes/{self._node}/qemu/{vm_id}/config"
        )
        
        return response

    def stop_console_session(self, node: str, upid: str) -> bool:
        """
        Stop an active console session by its UPID.
        Calls DELETE on the task endpoint to terminate the VNC/serial proxy.
        """
        if not upid:
            logger.warning("Cannot stop console session: no UPID provided")
            return False
        
        logger.info(f"Stopping console session with UPID: {upid}")
        
        try:
            self._api("DELETE", f"nodes/{node}/tasks/{upid}")
            logger.info(f"Console session {upid} stopped successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to stop console session {upid}: {e}")
            return False

    def resize_disk(self, vm_id: int, disk: str = "scsi0", size: str = "+1G", node: str = None) -> bool:
        """
        Resize a VM disk using Proxmox resize API.
        Only accepts relative sizes (+XG format).
        
        Args:
            vm_id: VM ID
            disk: Disk identifier (scsi0, virtio0, sata0, etc.)
            size: Relative size (e.g., "+1G", "+10G"). Must start with +.
            node: Optional node name (auto-detected if not provided)
        
        Returns:
            True if successful
        
        Raises:
            ValueError: If size format is invalid
            RuntimeError: If resize fails
        """
        if not size.startswith('+'):
            raise ValueError(f"Size must be relative format (+XG). Got: {size}")
        
        target_node = node or self._get_node_for_vm(vm_id)
        logger.info(f"Resizing VM {vm_id} disk {disk} by {size} on node {target_node}")
        
        try:
            self._api("PUT", f"nodes/{target_node}/qemu/{vm_id}/resize", {
                "disk": disk,
                "size": size
            })
            logger.info(f"Disk {disk} resized by {size} on VM {vm_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to resize disk on VM {vm_id}: {e}")
            raise RuntimeError(f"Failed to resize disk: {e}")

    def get_vm_resources(self, vm_id: int) -> dict:
        """
        Get current VM resource configuration (CPU, RAM, Disk).
        
        Args:
            vm_id: The Proxmox VM ID
        
        Returns:
            Dict with current resources: cpu_cores, memory_mb, disks, digest
        """
        logger.info(f"Getting VM resources for VM {vm_id}")
        
        try:
            target_node = self._get_node_for_vm(vm_id)
            response = self._api("GET", f"nodes/{target_node}/qemu/{vm_id}/config")
            
            config = response
            
            # Parse CPU cores
            cores = config.get("cores", 1)
            
            # Parse RAM in MB
            memory = int(config.get("memory", 1024))
            
            # Parse disks
            disks = {}
            for key, value in config.items():
                if key in ["scsi0", "virtio0", "sata0", "ide0"]:
                    # Parse disk config: "local-lvm:vm-100-disk-0,iothread=1,size=2252M"
                    if "size=" in value:
                        size_str = value.split("size=")[1].split(",")[0]
                        disks[key] = {"config": value, "size": size_str}
            
            return {
                "cpu_cores": int(cores),
                "memory_mb": memory,
                "disks": disks,
                "digest": config.get("digest"),
                "name": config.get("name"),
                "status": config.get("status", "running")
            }
        except Exception as e:
            logger.error(f"Failed to get VM resources for VM {vm_id}: {e}")
            raise RuntimeError(f"Failed to get VM resources: {e}")

    def update_vm_resources(self, vm_id: int, cpu_cores: int = None, memory_mb: int = None) -> dict:
        """
        Update VM CPU and/or RAM resources.
        
        Args:
            vm_id: The Proxmox VM ID
            cpu_cores: New CPU core count (None to keep current)
            memory_mb: New RAM in MB (None to keep current)
        
        Returns:
            Dict with success status and updated values
        """
        logger.info(f"Updating VM {vm_id} resources - CPU: {cpu_cores}, RAM: {memory_mb}MB")
        
        try:
            target_node = self._get_node_for_vm(vm_id)
            
            # First get current config to get digest
            current_config = self._api("GET", f"nodes/{target_node}/qemu/{vm_id}/config")
            digest = current_config.get("digest")
            
            if not digest:
                raise RuntimeError("Failed to get VM config digest")
            
            if not cpu_cores and not memory_mb:
                raise ValueError("No resources to update")
            
            # Send separate requests for CPU and RAM
            if cpu_cores is not None:
                # CPU params
                cpu_params = [
                    "sockets=1",
                    f"cores={cpu_cores}",
                    "numa=0",
                    "cpu=host",
                    "delete=vcpus,cpuunits,cpulimit",
                    f"digest={digest}"
                ]
                cpu_params_str = "&".join(cpu_params)
                self._api("PUT", f"nodes/{target_node}/qemu/{vm_id}/config", data=cpu_params_str)
                logger.info(f"VM {vm_id} CPU updated to {cpu_cores} cores")
            
            if memory_mb is not None:
                # Get new digest after CPU update (if CPU was also updated)
                if cpu_cores is not None:
                    current_config = self._api("GET", f"nodes/{target_node}/qemu/{vm_id}/config")
                    digest = current_config.get("digest")
                
                # RAM params
                ram_params = [
                    f"memory={memory_mb}",
                    "delete=allow-ksm,balloon,shares",
                    f"digest={digest}"
                ]
                ram_params_str = "&".join(ram_params)
                self._api("PUT", f"nodes/{target_node}/qemu/{vm_id}/config", data=ram_params_str)
                logger.info(f"VM {vm_id} RAM updated to {memory_mb}MB")
            
            # Restart VM after resource changes
            logger.info(f"Restarting VM {vm_id} to apply resource changes")
            self.stop_vm(vm_id)
            self.start_vm(vm_id)
            
            logger.info(f"VM {vm_id} resources updated and restarted successfully")
            return {
                "success": True,
                "cpu_cores": cpu_cores,
                "memory_mb": memory_mb
            }
        except Exception as e:
            logger.error(f"Failed to update VM resources for VM {vm_id}: {e}")
            raise RuntimeError(f"Failed to update VM resources: {e}")

    def get_storage_info(self) -> dict:
        """
        Get storage utilization using per-storage status API.
        
        Returns:
            Dict with storage names as keys and {total_gb, free_gb, used_gb, content} as values
        """
        storage_info = {}
        try:
            resources = self._api("GET", "cluster/resources")
            storage_names = set()
            storage_content = {}
            
            for item in resources:
                if item.get('type') == 'storage' and item.get('storage'):
                    name = item['storage']
                    storage_names.add(name)
                    storage_content[name] = item.get('content', '')
            
            for storage in storage_names:
                try:
                    status = self._api("GET", f"storage/{storage}/status")
                    total = status.get('total', 0)
                    used = status.get('used', 0)
                    free = status.get('free', 0)
                    storage_info[storage] = {
                        'total': total,
                        'used': used,
                        'free': free,
                        'total_gb': round(total / (1024**3), 1),
                        'free_gb': round(free / (1024**3), 1),
                        'used_gb': round(used / (1024**3), 1),
                        'content': storage_content.get(storage, ''),
                    }
                except Exception as e:
                    logger.warning(f"Could not get status for storage {storage}: {e}")
            
            return storage_info
        except Exception as e:
            logger.error(f"Failed to get storage info: {e}")
            return storage_info

    def get_vm_disk_info(self, vm_id: int) -> list:
        """
        Parse VM config to extract disk information.
        
        Args:
            vm_id: The VM ID
        
        Returns:
            List of dicts with {id, storage, volume, size_mib, size_gb, options}
        """
        try:
            config = self._api("GET", f"nodes/{self._node}/qemu/{vm_id}/config")
            
            disks = []
            for key, value in config.items():
                if not key.startswith(('scsi', 'virtio', 'sata', 'ide')):
                    continue
                if not isinstance(value, str) or '=' not in value:
                    continue
                
                parts = value.split(',')
                storage = parts[0].split(':')[0] if ':' in parts[0] else parts[0]
                volume = parts[0].split(':')[1] if ':' in parts[0] else parts[0]

                size_mib = self._parse_config_disk_size(value)
                options = ','.join([p for p in parts if not p.startswith('size=')])
                
                disks.append({
                    'id': key,
                    'storage': storage,
                    'volume': volume,
                    'size_mib': size_mib,
                    'size_gb': round(size_mib / 1024, 1),
                    'options': options,
                })
            
            return disks
        except Exception as e:
            logger.error(f"Failed to get disk info for VM {vm_id}: {e}")
            return []

    def get_vm_status(self, vm_id: int) -> dict:
        """
        Get current VM status including lock state.
        
        Args:
            vm_id: The VM ID
        
        Returns:
            Dict with VM status info including 'lock' field if VM is locked
        """
        try:
            return self._api("GET", f"nodes/{self._node}/qemu/{vm_id}/status/current")
        except Exception as e:
            logger.debug(f"Failed to get VM status for {vm_id}: {e}")
            return {}

    def _get_node_for_vm(self, vm_id: int) -> str:
        """
        Look up which node a VM resides on.
        
        Args:
            vm_id: The VM ID
        
        Returns:
            Node name
        """
        try:
            resources = self._api("GET", "cluster/resources")
            for r in resources:
                if r.get('vmid') == vm_id and r.get('type') == 'qemu':
                    return r['node']
        except Exception as e:
            logger.warning(f"Could not find node for VM {vm_id}, using default: {e}")
        
        return self._node

    def _detect_primary_disk_type(self, vm_id: int) -> str:
        """
        Detect the primary disk type from VM config.
        
        Args:
            vm_id: The VM ID
        
        Returns:
            Disk type string (e.g., 'scsi0', 'virtio0')
        """
        try:
            config = self._api("GET", f"nodes/{self._node}/qemu/{vm_id}/config")
            
            for key in ['scsi0', 'virtio0', 'sata0', 'ide0']:
                if key in config:
                    return key
            
            return "scsi0"
        except Exception as e:
            logger.warning(f"Could not detect disk type for VM {vm_id}: {e}")
            return "scsi0"

    def _parse_size_to_mib(self, size: str) -> int:
        """
        Parse a size string to MiB.
        
        Args:
            size: Size string (e.g., "+10G", "+512M", "10240")
        
        Returns:
            Size in MiB
        """
        size = size.strip().upper()
        if size.endswith('M'):
            return int(size[:-1])
        elif size.endswith('G'):
            return int(size[:-1]) * 1024
        elif size.endswith('T'):
            return int(size[:-1]) * 1024 * 1024
        else:
            return int(size)

    def _parse_config_disk_size(self, config_value: str) -> int:
        """
        Parse disk size from Proxmox VM config string.
        
        Args:
            config_value: E.g., "local-lvm:vm-100-disk-0,iothread=1,size=3276M"
        
        Returns:
            Size in MiB
        """
        if not isinstance(config_value, str):
            return 0
        
        size_str = next((p for p in config_value.split(',') if p.startswith('size=')), 'size=0')
        size = size_str.replace('size=', '').upper()
        try:
            if size.endswith('G'):
                return int(size[:-1]) * 1024
            elif size.endswith('M'):
                return int(size[:-1])
            elif size.endswith('T'):
                return int(size[:-1]) * 1024 * 1024
            else:
                return int(size)
        except ValueError:
            return 0

    # ─── Image Template Builder Methods ───

    def download_iso_url(self, node: str, storage: str, url: str) -> dict:
        """
        Download a file (ISO or image) from a URL to Proxmox storage.
        Proxmox stores all downloads in the ISO directory regardless of extension.
        Returns the task UPID for async tracking.
        """
        logger.info(f"Downloading file from {url} to {storage}")
        filename = url.split("/")[-1]
        result = self._api(
            "POST",
            f"nodes/{node}/storage/{storage}/download-url",
            data={"url": url, "filename": filename, "content": "iso", "verify-certificates": 1},
            timeout=30,
        )
        upid = result.get("data", result) if isinstance(result, dict) else result
        return {
            "upid": upid if isinstance(upid, str) else upid.get("upid", ""),
            "volid": f"{storage}:iso/{filename}",
        }

    def query_url_metadata(self, node: str, url: str) -> dict:
        """
        Query Proxmox for URL metadata (filename, size, mimetype).
        Useful for validating URLs before downloading.
        """
        logger.info(f"Querying URL metadata: {url}")
        result = self._api(
            "GET",
            f"nodes/{node}/query-url-metadata",
            data={"url": url, "verify-certificates": 1},
            timeout=30,
        )
        return result.get("data", result) if isinstance(result, dict) else result

    def import_disk(self, node: str, vmid: int, source_volid: str, target_storage: str,
                    format: str = "qcow2", bus: str = "scsi") -> dict:
        """
        Import a disk image into an existing VM using qm importdisk.
        The disk is attached as unused; caller must move it to the VM config.
        """
        logger.info(f"Importing disk {source_volid} into VM {vmid} on {target_storage}")
        result = self._api(
            "POST",
            f"nodes/{node}/qemu/{vmid}/move_disk",
            data={
                "disk": "scsi0",
                "storage": target_storage,
                "format": format,
            },
            timeout=300,
        )
        return result

    def _execute_on_node(self, node: str, command: str, timeout: int = 300) -> tuple:
        """Execute a shell command on the Proxmox node via API and wait for completion."""
        result = self._api(
            "POST",
            f"nodes/{node}/execute",
            json={"command": ["/bin/sh", "-c", command]},
        )
        pid = result.get("pid")
        if not pid:
            raise RuntimeError(f"No PID returned from execute command")

        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(2)
            status = self._api("GET", f"nodes/{node}/tasks/{pid}/status")
            if status.get("status") == "stopped":
                exitcode = status.get("exitcode")
                log_lines = self._api("GET", f"nodes/{node}/tasks/{pid}/log", data={"start": 0})
                if isinstance(log_lines, list):
                    output = "\n".join(line.get("t", "") for line in log_lines)
                else:
                    output = str(log_lines)
                return (exitcode, output)

        raise RuntimeError(f"Command execution timed out after {timeout}s")

    def create_build_vm_from_image(self, node: str, vmid: int, name: str, image_volid: str,
                                    cpu: int = 2, ram_mb: int = 4096, disk_gb: int = 20,
                                    bus: str = "scsi", target_storage: str = None) -> dict:
        """
        Create a build VM from a pre-built disk image (.img/.qcow2).
        Uses qm importdisk executed on the Proxmox host via the nodes/{node}/execute API.
        """
        target_storage = target_storage or self._storage
        logger.info(f"Creating build VM {vmid} ({name}) from image {image_volid} on storage {target_storage}")

        # Step 1: Create VM without disk
        logger.info(f"Creating VM {vmid} (no disk)")
        self._api(
            "POST",
            f"nodes/{node}/qemu",
            data={
                "vmid": vmid,
                "name": name,
                "cores": cpu,
                "memory": ram_mb,
                "ide2": "none,media=cdrom",
                "scsihw": "virtio-scsi-single",
                "net0": "virtio,bridge=vmbr0",
                "ostype": "l26",
                "agent": "enabled=1",
                "onboot": 0,
                "tablet": 0,
            },
            timeout=60,
        )

        # Step 2: Resolve volid to filesystem path on Proxmox host
        logger.info(f"Resolving filesystem path for {image_volid}")
        exitcode, pvesm_output = self._execute_on_node(node, f"pvesm path '{image_volid}'", timeout=30)
        if exitcode != 0:
            raise RuntimeError(f"Failed to resolve path for {image_volid}: {pvesm_output}")
        source_path = pvesm_output.strip()
        logger.info(f"Image filesystem path: {source_path}")

        # Step 3: Import disk via qm importdisk on the Proxmox host
        import_cmd = f"qm importdisk {vmid} '{source_path}' {target_storage} --format qcow2"
        logger.info(f"Running qm importdisk on node")
        exitcode, output = self._execute_on_node(node, import_cmd, timeout=600)
        if exitcode != 0:
            raise RuntimeError(f"qm importdisk failed (exit {exitcode}): {output[:2000]}")
        logger.info(f"qm importdisk succeeded")

        # Step 4: Find the imported (unused) disk in VM config
        vm_config = self._api("GET", f"nodes/{node}/qemu/{vmid}/config")
        config_data = vm_config if isinstance(vm_config, dict) else {}

        unused_volid = None
        for key, value in config_data.items():
            if key.startswith("unused"):
                unused_volid = value
                break

        if not unused_volid:
            disk_volid = config_data.get(f"{bus}0")
            if disk_volid:
                logger.info(f"Disk already at {bus}0: {disk_volid}")
                return {"vmid": vmid, "status": "created_with_import"}
            raise RuntimeError(f"No unused disk found after import on VM {vmid}")

        logger.info(f"Found unused disk: {unused_volid}")
        digest = config_data.get("digest", "")

        # Step 5: Attach disk as {bus}0 with iothread=on
        self._api("PUT", f"nodes/{node}/qemu/{vmid}/config", data={
            f"{bus}0": f"{unused_volid},iothread=on",
            "digest": digest,
        }, timeout=30)
        logger.info(f"Disk {bus}0 attached")

        # Step 6: Add Cloud-Init drive
        cvm_config = self._api("GET", f"nodes/{node}/qemu/{vmid}/config")
        cdigest = cvm_config.get("digest", "") if isinstance(cvm_config, dict) else ""
        self._api("PUT", f"nodes/{node}/qemu/{vmid}/config", data={
            "ide0": f"{target_storage}:cloudinit",
            "digest": cdigest,
        }, timeout=30)
        logger.info(f"Cloud-Init drive added")

        # Step 7: Set boot order
        bvm_config = self._api("GET", f"nodes/{node}/qemu/{vmid}/config")
        bdigest = bvm_config.get("digest", "") if isinstance(bvm_config, dict) else ""
        self._api("PUT", f"nodes/{node}/qemu/{vmid}/config", data={
            "boot": f"order={bus}0;net0",
            "digest": bdigest,
        }, timeout=30)
        logger.info(f"Boot order set")

        # Step 8: Set cloud-init user/password
        uvm_config = self._api("GET", f"nodes/{node}/qemu/{vmid}/config")
        udigest = uvm_config.get("digest", "") if isinstance(uvm_config, dict) else ""
        self._api("PUT", f"nodes/{node}/qemu/{vmid}/config", data={
            "ciuser": "root",
            "cipassword": "root",
            "digest": udigest,
        }, timeout=30)
        logger.info(f"Cloud-Init credentials set")

        return {"vmid": vmid, "status": "created_with_import"}

    def list_storage_content(self, node: str, storage: str, content_type: str = None) -> list:
        """List storage contents, optionally filtered by type (iso, vztmpl, images)."""
        logger.info(f"Listing storage content for {storage}")
        result = self._api("GET", f"nodes/{node}/storage/{storage}/content")
        data = result.get("data", result) if isinstance(result, dict) else result
        if content_type:
            return [item for item in data if item.get("content") == content_type]
        return data

    def delete_storage_content(self, node: str, storage: str, volid: str, delay: int = 5) -> None:
        """Delete a file (ISO / IMG / vztmpl) from Proxmox storage.
        `volid` is the volume identifier returned by list_storage_content, e.g.
        ``local:iso/alpine-virt-3.23.0-x86_64.iso``. The `delay` query param
        gives a grace period (seconds) before Proxmox actually unlinks the
        file; defaults to 5s as a safety net."""
        logger.info(f"Deleting storage content {volid} on {node}/{storage} (delay={delay}s)")
        self._api_delete(
            f"nodes/{node}/storage/{storage}/content/{volid}",
            params={"delay": delay},
        )

    def get_task_status(self, node: str, upid: str) -> dict:
        """Get Proxmox task status by UPID."""
        result = self._api("GET", f"nodes/{node}/tasks/{upid}/status")
        return result.get("data", result) if isinstance(result, dict) else result

    def get_task_log(self, node: str, upid: str, start: int = 0, limit: int = 100) -> dict:
        """Get task log lines."""
        result = self._api("GET", f"nodes/{node}/tasks/{upid}/log", data={"start": start, "limit": limit})
        return result.get("data", result) if isinstance(result, dict) else result

    def get_task_log_tail(self, node: str, upid: str, tail: int = 200, header: int = 20) -> list:
        """Get the last `tail` log lines for a task, plus the first `header`
        lines (so the wget header containing ``Length: <bytes>`` is always
        available even when the log has thousands of progress ticks).

        The Proxmox log API only supports positive ``start`` offsets (no
        "from end" flag), so we do up to three calls:
            1. ``start=0, limit=1``  — discover the total line count.
            2. ``start=0, limit=header`` — fetch the header.
            3. ``start=max(0, total-tail), limit=tail`` — fetch the tail.

        The two slices are merged and deduplicated by line number ``n``, then
        returned in chronological order. The shape is the same as
        ``get_task_log``: a list of ``{n, t}`` entries."""
        try:
            head_resp = self._api_full(
                "GET", f"nodes/{node}/tasks/{upid}/log",
                params={"start": 0, "limit": 1},
            )
        except Exception as e:
            logger.debug(f"get_task_log_tail: could not read total for {upid}: {e}")
            head_resp = {}
        total = 0
        if isinstance(head_resp, dict):
            total = int(head_resp.get("total", 0) or 0)
        if total <= 0:
            return []

        def _fetch_slice(start: int, limit: int) -> list:
            try:
                resp = self._api_full(
                    "GET", f"nodes/{node}/tasks/{upid}/log",
                    params={"start": start, "limit": limit},
                )
            except Exception as e:
                logger.debug(f"get_task_log_tail: slice start={start} failed: {e}")
                return []
            if not isinstance(resp, dict):
                return []
            data = resp.get("data", [])
            return data if isinstance(data, list) else []

        header_lines = _fetch_slice(0, header) if header > 0 else []
        start = max(0, total - tail)
        tail_lines = _fetch_slice(start, tail) if tail > 0 else []

        merged: dict = {}
        for entry in (*header_lines, *tail_lines):
            if isinstance(entry, dict) and "n" in entry:
                merged[entry["n"]] = entry
        return [merged[k] for k in sorted(merged.keys())]

    def create_build_vm(self, node: str, vmid: int, name: str, iso_volid: str,
                        cpu: int = 2, ram_mb: int = 4096, disk_gb: int = 20,
                        storage: str = None) -> dict:
        """
        Create a build VM from an ISO for template preparation.
        The VM boots from the ISO CDROM with minimal resources.
        """
        target_storage = storage or self._storage
        logger.info(f"Creating build VM {vmid} ({name}) from {iso_volid} on storage {target_storage}")
        result = self._api(
            "POST",
            f"nodes/{node}/qemu",
            data={
                "vmid": vmid,
                "name": name,
                "cores": cpu,
                "memory": ram_mb,
                "scsi0": f"{target_storage}:{disk_gb},discard=on",
                "scsihw": "virtio-scsi-pci",
                "ide2": f"{iso_volid},media=cdrom",
                "boot": "order=ide2;scsi0",
                "net0": "virtio,bridge=vmbr0",
                "ostype": "l26",
                "agent": "enabled=1",
                "onboot": 0,
                "tablet": 0,
            },
            timeout=60,
        )
        return result

    def convert_to_template(self, node: str, vmid: int) -> dict:
        """Convert a VM to a template."""
        logger.info(f"Converting VM {vmid} to template")
        result = self._api(
            "POST",
            f"nodes/{node}/qemu/{vmid}/template",
            timeout=120,
        )
        return result
