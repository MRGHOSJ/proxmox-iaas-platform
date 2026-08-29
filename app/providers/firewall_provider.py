import logging
from abc import ABC, abstractmethod
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from app.models.tenant import Tenant
from app.models.firewall_provider_config import FirewallProviderConfig
from app.core.config import settings

logger = logging.getLogger(__name__)


class FirewallProvider(ABC):
    """Abstract base class for firewall providers."""

    @abstractmethod
    def list_rules(self) -> list[dict]:
        """Fetch all firewall rules from the provider."""
        pass

    @abstractmethod
    def list_interfaces(self) -> list[dict]:
        """Fetch available network interfaces."""
        pass

    @abstractmethod
    def add_rule(self, rule_payload: dict) -> Tuple[str, dict]:
        """Add a rule. Returns (uuid, full_response)."""
        pass

    @abstractmethod
    def set_rule(self, uuid: str, rule_payload: dict) -> dict:
        """Update an existing rule."""
        pass

    @abstractmethod
    def del_rule(self, uuid: str) -> dict:
        """Delete a rule."""
        pass

    @abstractmethod
    def apply_rules(self) -> dict:
        """Apply pending configuration changes."""
        pass

    @abstractmethod
    def get_interface_list(self) -> list[dict]:
        """Get firewall interface list (value, label, type)."""
        pass


class OPNsenseFirewallProvider(FirewallProvider):
    """OPNsense firewall provider using Proxmox exec_in_vm proxy."""

    def __init__(self, tenant: Tenant):
        self.tenant = tenant

    def _exec(self, method: str, path: str, json_data: dict = None, timeout: int = 30) -> dict:
        from app.workers.tasks.helpers import exec_opnsense_api
        return exec_opnsense_api(
            vm_id=self.tenant.opnsense_vm_id,
            node=settings.PROXMOX_NODE,
            method=method,
            path=path,
            api_key=self.tenant.opnsense_api_key,
            api_secret=self.tenant.opnsense_api_secret,
            json_data=json_data,
            timeout=timeout,
        )

    def _wg_exec(self, method: str, path: str, json_data: dict = None, timeout: int = 30) -> dict:
        return self._exec(method, f"wireguard/{path}", json_data, timeout=timeout)

    def generate_keypair(self) -> dict:
        """Generate a WireGuard key pair on OPNsense. Returns {privkey, pubkey}."""
        data = self._wg_exec("GET", "server/key_pair", timeout=15)
        if not data or not data.get("privkey") or not data.get("pubkey"):
            raise RuntimeError(f"OPNsense key_pair returned empty: {data!r}")
        return {"privkey": data["privkey"], "pubkey": data["pubkey"]}

    def wg_general_enable(self, enabled: bool = True) -> dict:
        return self._wg_exec("POST", "general/set", {"general": {"enabled": "1" if enabled else "0"}})

    def wg_service_reconfigure(self) -> dict:
        return self._wg_exec("POST", "service/reconfigure", {})

    def add_wg_server(
        self,
        name: str,
        pubkey: str,
        privkey: str,
        listen_port: int,
        tunnel_address: str,
        mtu: int = 1420,
        dns: str = "",
        peer_keepalive: int = 25,
    ) -> str:
        """
        Create a WireGuard server in OPNsense. Returns the new server UUID.
        `tunnel_address` is the server's IP inside the tunnel (e.g. 10.200.0.1/24).

        Note: `dns` and `peer_keepalive` are accepted for API compatibility but
        intentionally NOT sent to OPNsense — OPNsense validates `server.dns`
        strictly as valid IPv4/IPv6 (comma-separated or spaced values are
        rejected) and the server endpoint already pushes DNS to clients via
        the .conf we generate locally.
        """
        payload = {
            "server": {
                "enabled": "1",
                "name": name,
                "pubkey": pubkey,
                "privkey": privkey,
                "port": str(listen_port),
                "mtu": str(mtu),
                "dns": "",
                "tunneladdress": tunnel_address,
                "carp_depend_on": "",
                "peers": "",
                "disableroutes": "0",
                "gateway": "",
                "debug": "0",
            }
        }
        result = self._wg_exec("POST", "server/add_server/", payload, timeout=30)
        if result.get("result") != "saved" or not result.get("uuid"):
            raise RuntimeError(
                f"OPNsense add_server rejected: result={result.get('result')!r} "
                f"validations={result.get('validations', {})}"
            )
        return result["uuid"]

    def set_wg_server_endpoint(self, server_uuid: str, endpoint: str) -> dict:
        """Update the server's public endpoint (e.g. 1.2.3.4:51820)."""
        return self._wg_exec(
            "POST",
            f"server/set_server/{server_uuid}",
            {"server": {"endpoint": endpoint, "peer_dns": ""}},
        )

    def get_wg_device_name(self, server_uuid: str) -> str:
        """Return the OPNsense kernel device name (e.g. "wg0") for a server UUID."""
        result = self._wg_exec(
            "POST",
            "server/search_server",
            {"current": 1, "rowCount": 50, "sort": {}},
            timeout=15,
        )
        for row in result.get("rows", []):
            if row.get("uuid") == server_uuid:
                iface = row.get("interface", "")
                if iface:
                    return iface
        raise RuntimeError(
            f"WireGuard server {server_uuid} not found in search_server response"
        )

    def del_wg_server(self, server_uuid: str) -> dict:
        result = self._wg_exec("POST", f"server/del_server/{server_uuid}", {})
        if result.get("result") != "deleted":
            raise RuntimeError(f"OPNsense del_server {server_uuid} rejected: {result!r}")
        return result

    def add_wg_client(
        self,
        server_uuid: str,
        name: str,
        pubkey: str,
        psk: str,
        tunnel_address: str,
        keepalive: int = 25,
        endpoint: str = "",
    ) -> str:
        """Create a WireGuard client (peer) under the given server. Returns the client UUID.

        Uses the `add_client_builder` endpoint with a `configbuilder` payload
        (the wizard-style form). This endpoint does NOT return the new client
        UUID on success — it just returns `{"result": "saved"}`. We follow up
        with `client/get` and look up the newly created client by its public
        key, which is the most reliable unique identifier.
        """
        payload = {
            "configbuilder": {
                "enabled": "1",
                "name": name,
                "pubkey": pubkey,
                "psk": psk,
                "tunneladdress": tunnel_address,
                "keepalive": str(keepalive),
                "server": server_uuid,
                "endpoint": endpoint,
            }
        }
        result = self._wg_exec("POST", "client/add_client_builder/", payload, timeout=30)
        if result.get("result") != "saved":
            raise RuntimeError(
                f"OPNsense add_client_builder rejected: result={result.get('result')!r} "
                f"validations={result.get('validations', {})}"
            )

        clients_response = self._wg_exec(
            "POST",
            "client/search_client/",
            {"current": 1, "sort": {}, "rowCount": 100},
            timeout=15,
        )
        for row in clients_response.get("rows", []):
            if isinstance(row, dict) and row.get("pubkey") == pubkey:
                return row["uuid"]

        raise RuntimeError(
            f"OPNsense add_client_builder saved the client but the UUID could not be "
            f"located via client/get (looked for pubkey={pubkey[:12]}…). "
            f"Known clients: {list(clients.keys())}"
        )

    def del_wg_client(self, client_uuid: str) -> dict:
        result = self._wg_exec("POST", f"client/del_client/{client_uuid}", {})
        if result.get("result") != "deleted":
            raise RuntimeError(f"OPNsense del_client {client_uuid} rejected: {result!r}")
        return result

    def get_wg_server_info(self, server_uuid: str) -> dict:
        """Return server metadata: pubkey, endpoint, peer_dns, mtu, address (server /24)."""
        return self._wg_exec("GET", f"client/get_server_info/{server_uuid}")

    def list_rules(self) -> list[dict]:
        data = self._exec("POST", "firewall/filter/search_rule/", {"current": 1, "rowCount": 1000, "sort": {}})
        return data.get("rows", [])

    def get_interface_list(self) -> list[dict]:
        data = self._exec("GET", "firewall/filter/get_interface_list")
        interfaces_section = data.get("interfaces", {})
        items = interfaces_section.get("items", [])
        return items

    def list_interfaces(self) -> list[dict]:
        return self.get_interface_list()

    def add_rule(self, rule_payload: dict) -> Tuple[str, dict]:
        result = self._exec("POST", "firewall/filter/add_rule/", {"rule": rule_payload})
        if result.get("result") != "saved" or not result.get("uuid"):
            raise RuntimeError(
                f"OPNsense add_rule rejected: result={result.get('result')!r} "
                f"validations={result.get('validations', {})}"
            )
        return result["uuid"], result

    def set_rule(self, uuid: str, rule_payload: dict) -> dict:
        result = self._exec("POST", f"firewall/filter/set_rule/{uuid}", {"rule": rule_payload})
        if result.get("result") != "saved":
            raise RuntimeError(
                f"OPNsense set_rule {uuid} rejected: result={result.get('result')!r} "
                f"validations={result.get('validations', {})}"
            )
        return result

    def del_rule(self, uuid: str) -> dict:
        result = self._exec("POST", f"firewall/filter/del_rule/{uuid}", {})
        if result.get("result") != "deleted":
            raise RuntimeError(
                f"OPNsense del_rule {uuid} rejected: result={result.get('result')!r} "
                f"validations={result.get('validations', {})}"
            )
        return result

    def move_rule_before(self, rule_uuid: str, reference_uuid: str) -> dict:
        result = self._exec("POST", f"firewall/filter/move_rule_before/{rule_uuid}/{reference_uuid}", {})
        if result.get("status") != "ok":
            raise RuntimeError(f"OPNsense move_rule_before failed: {result!r}")
        return result

    def apply_rules(self) -> dict:
        return self._exec("POST", "firewall/filter/apply", {}, timeout=60)


class PFSenseFirewallProvider(FirewallProvider):
    """pfSense provider - stub implementation."""

    def __init__(self, config: FirewallProviderConfig):
        self.config = config

    def list_rules(self) -> list[dict]:
        logger.warning("PFSenseFirewallProvider: list_rules not implemented")
        return []

    def list_interfaces(self) -> list[dict]:
        logger.warning("PFSenseFirewallProvider: list_interfaces not implemented")
        return []

    def get_interface_list(self) -> list[dict]:
        logger.warning("PFSenseFirewallProvider: get_interface_list not implemented")
        return []

    def add_rule(self, rule_payload: dict) -> Tuple[str, dict]:
        logger.warning("PFSenseFirewallProvider: add_rule not implemented")
        return "", {}

    def set_rule(self, uuid: str, rule_payload: dict) -> dict:
        logger.warning("PFSenseFirewallProvider: set_rule not implemented")
        return {}

    def del_rule(self, uuid: str) -> dict:
        logger.warning("PFSenseFirewallProvider: del_rule not implemented")
        return {}

    def apply_rules(self) -> dict:
        logger.warning("PFSenseFirewallProvider: apply_rules not implemented")
        return {}


class FortinetFirewallProvider(FirewallProvider):
    """Fortinet provider - stub implementation."""

    def __init__(self, config: FirewallProviderConfig):
        self.config = config

    def list_rules(self) -> list[dict]:
        logger.warning("FortinetFirewallProvider: list_rules not implemented")
        return []

    def list_interfaces(self) -> list[dict]:
        logger.warning("FortinetFirewallProvider: list_interfaces not implemented")
        return []

    def get_interface_list(self) -> list[dict]:
        logger.warning("FortinetFirewallProvider: get_interface_list not implemented")
        return []

    def add_rule(self, rule_payload: dict) -> Tuple[str, dict]:
        logger.warning("FortinetFirewallProvider: add_rule not implemented")
        return "", {}

    def set_rule(self, uuid: str, rule_payload: dict) -> dict:
        logger.warning("FortinetFirewallProvider: set_rule not implemented")
        return {}

    def del_rule(self, uuid: str) -> dict:
        logger.warning("FortinetFirewallProvider: del_rule not implemented")
        return {}

    def apply_rules(self) -> dict:
        logger.warning("FortinetFirewallProvider: apply_rules not implemented")
        return {}


def get_firewall_provider(db: Session, tenant: Tenant, provider_type: str = "opnsense") -> FirewallProvider:
    """
    Factory function to get the appropriate firewall provider.
    
    For opnsense: uses tenant's own OPNsense credentials.
    For pfsense/fortinet: looks up FirewallProviderConfig for the tenant.
    """
    if provider_type == "opnsense":
        if not tenant.opnsense_vm_id:
            raise ValueError(f"Tenant {tenant.id} has no OPNsense VM configured")
        return OPNsenseFirewallProvider(tenant)

    config = db.query(FirewallProviderConfig).filter(
        FirewallProviderConfig.tenant_id == tenant.id,
        FirewallProviderConfig.provider_type == provider_type,
        FirewallProviderConfig.is_active == "1",
    ).first()

    if not config:
        raise ValueError(f"No active {provider_type} firewall configured for tenant {tenant.id}")

    if provider_type == "pfsense":
        return PFSenseFirewallProvider(config)
    elif provider_type == "fortinet":
        return FortinetFirewallProvider(config)
    else:
        raise ValueError(f"Unknown firewall provider type: {provider_type}")


def get_available_providers(db: Session, tenant: Tenant) -> list[dict]:
    """
    Get list of available firewall providers for a tenant.
    Returns provider info including whether it's enabled/connected.
    """
    providers = []

    # OPNsense - always check
    opnsense_info = {
        "type": "opnsense",
        "label": "OPNsense",
        "icon": "shield",
        "is_active": False,
        "is_connected": False,
        "rule_count": 0,
        "last_sync": None,
        "is_enabled": True,
    }
    if tenant.opnsense_vm_id and tenant.opnsense_api_key and tenant.opnsense_api_secret:
        opnsense_info["is_connected"] = True
        config = db.query(FirewallProviderConfig).filter(
            FirewallProviderConfig.tenant_id == tenant.id,
            FirewallProviderConfig.provider_type == "opnsense",
        ).first()
        if config and config.is_active == "1":
            opnsense_info["is_active"] = True
        from app.models.opnsense_firewall_rule import OPNsenseFirewallRule
        rule_count = db.query(OPNsenseFirewallRule).filter(
            OPNsenseFirewallRule.tenant_id == tenant.id
        ).count()
        opnsense_info["rule_count"] = rule_count
        latest = db.query(OPNsenseFirewallRule).filter(
            OPNsenseFirewallRule.tenant_id == tenant.id,
            OPNsenseFirewallRule.synced_at.isnot(None),
        ).order_by(OPNsenseFirewallRule.synced_at.desc()).first()
        if latest and latest.synced_at:
            opnsense_info["last_sync"] = latest.synced_at.isoformat()
    providers.append(opnsense_info)

    # pfSense
    pfsense_config = db.query(FirewallProviderConfig).filter(
        FirewallProviderConfig.tenant_id == tenant.id,
        FirewallProviderConfig.provider_type == "pfsense",
    ).first()
    pfsense_info = {
        "type": "pfsense",
        "label": "pfSense",
        "icon": "shield",
        "is_active": bool(pfsense_config and pfsense_config.is_active == "1"),
        "is_connected": bool(pfsense_config and pfsense_config.api_key),
        "rule_count": 0,
        "last_sync": None,
        "is_enabled": bool(pfsense_config and pfsense_config.api_key),
    }
    providers.append(pfsense_info)

    # Fortinet
    fortinet_config = db.query(FirewallProviderConfig).filter(
        FirewallProviderConfig.tenant_id == tenant.id,
        FirewallProviderConfig.provider_type == "fortinet",
    ).first()
    fortinet_info = {
        "type": "fortinet",
        "label": "Fortinet",
        "icon": "shield",
        "is_active": bool(fortinet_config and fortinet_config.is_active == "1"),
        "is_connected": bool(fortinet_config and fortinet_config.api_key),
        "rule_count": 0,
        "last_sync": None,
        "is_enabled": bool(fortinet_config and fortinet_config.api_key),
    }
    providers.append(fortinet_info)

    return providers