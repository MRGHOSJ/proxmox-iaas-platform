"""
VLAN provisioning via in-VM PHP scripts and OPNsense REST API.

config.xml is modified locally inside the OPNsense VM via PHP scripts.
vm_id is a runtime parameter - each tenant gets a cloned OPNsense VM with its own ID.
Kea DHCP uses OPNsense REST API (add_subnet, set interfaces, reconfigure) for 
proper state management.

Idempotency
-----------
- If VLAN already exists: reuse its vlanif and find its exact opt via get_opt_for_vlanif.
- If existing opt's IP differs from requested: call set_interface_ip to update it.
- Always proceed to DHCP setup even if VLAN already existed.

Locking
-------
Handled inside OPNsenseConfigInVM._run_php() via flock (PHP's native flock()).
Celery tasks do not need additional locking.
"""

import logging
import re
import ipaddress

from celery import shared_task

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.websocket import publish_log_update, publish_status_update
from app.models.tenant import Tenant
from app.models.network import TenantNetwork
from app.providers import get_hypervisor_provider
from app.workers.modules.opnsense_config_invm import OPNsenseConfigInVM
from app.workers.tasks.helpers import exec_opnsense_api

logger = logging.getLogger(__name__)


def _next_vlanif(existing_vlanifs: list) -> str:
    """Sequential vlanif name - never based on VLAN tag number."""
    nums = [
        int(m.group(1))
        for name in existing_vlanifs
        if (m := re.match(r"vlan(\d+)$", name))
    ]
    return f"vlan{(max(nums) + 1 if nums else 1):02d}"


def _next_opt(existing_iface_names: list) -> str:
    """Sequential opt# name - never based on VLAN tag number."""
    nums = [
        int(m.group(1))
        for name in existing_iface_names
        if (m := re.match(r"opt(\d+)$", name))
    ]
    return f"opt{max(nums) + 1 if nums else 1}"


def _setup_kea_dhcp(
    opt_name: str,
    ip_gateway: str,
    subnet_cidr: str,
    dhcp_start: str,
    dhcp_end: str,
    vm_id: int,
    node: str,
    api_key: str,
    api_secret: str,
    network_id: int = None,
):
    """
    Configure Kea DHCP subnet via OPNsense REST API.
    
    Routes through Proxmox: Celery → Proxmox API → exec_in_vm() → OPNsense localhost:443
    
    Steps:
    1. Get current Kea config (to preserve existing interfaces)
    2. Add DHCP subnet pool via add_subnet API
    3. Set interfaces list (include existing + new opt interface)
    4. Reconfigure Kea service
    """
    logger.info("VM %d: configuring Kea DHCP via OPNsense API", vm_id)
    
    if network_id:
        publish_log_update(network_id, "Getting current Kea DHCP configuration...")
    
    current_data = exec_opnsense_api(
        vm_id=vm_id,
        node=node,
        method="GET",
        path="kea/dhcpv4/get",
        api_key=api_key,
        api_secret=api_secret,
    )
    
    current_interfaces = current_data.get("dhcpv4", {}).get("general", {}).get("interfaces", {})
    enabled_interfaces = []
    if isinstance(current_interfaces, dict):
        for iface_name, iface_data in current_interfaces.items():
            if isinstance(iface_data, dict) and iface_data.get("selected") == 1:
                enabled_interfaces.append(iface_name)
    elif isinstance(current_interfaces, str):
        enabled_interfaces = [i.strip() for i in current_interfaces.split(",") if i.strip()]
    
    network = ipaddress.ip_network(subnet_cidr, strict=False)
    subnet_str = str(network.network_address) + "/" + str(network.prefixlen)
    
    logger.info("VM %d: adding Kea subnet %s with pool %s-%s",
                vm_id, subnet_str, dhcp_start, dhcp_end)
    
    if network_id:
        publish_log_update(network_id, f"Adding Kea subnet {subnet_str} with pool {dhcp_start}-{dhcp_end}...")
    
    exec_opnsense_api(
        vm_id=vm_id,
        node=node,
        method="POST",
        path="kea/dhcpv4/add_subnet/",
        api_key=api_key,
        api_secret=api_secret,
        json_data={
            "subnet4": {
                "subnet": subnet_str,
                "description": opt_name.upper(),
                "pools": f"{dhcp_start} - {dhcp_end}",
                "match-client-id": "0",
                "option_data_autocollect": "1",
                "option_data": {
                    "routers": ip_gateway,
                    "static_routes": "",
                    "classless_static_route": "",
                    "domain_name_servers": "",
                    "domain_name": "",
                    "domain_search": "",
                    "ntp_servers": "",
                    "time_servers": "",
                    "tftp_server_name": "",
                    "boot_file_name": "",
                    "v6_only_preferred": "",
                    "v4_dnr": "",
                },
                "next_server": "",
            }
        },
    )
    
    if opt_name not in enabled_interfaces:
        enabled_interfaces.append(opt_name)
    interfaces_str = ",".join(enabled_interfaces)
    
    logger.info("VM %d: setting Kea interfaces to %s", vm_id, interfaces_str)
    
    if network_id:
        publish_log_update(network_id, f"Setting Kea interfaces to {interfaces_str}...")
    
    exec_opnsense_api(
        vm_id=vm_id,
        node=node,
        method="POST",
        path="kea/dhcpv4/set",
        api_key=api_key,
        api_secret=api_secret,
        json_data={
            "dhcpv4": {
                "general": {
                    "enabled": "1",
                    "manual_config": "0",
                    "interfaces": interfaces_str,
                    "valid_lifetime": "4000",
                    "fwrules": "1",
                    "dhcp_socket_type": "raw",
                },
                "lexpire": {
                    "hold_reclaimed_time": "",
                    "reclaim_timer_wait_time": "",
                    "flush_reclaimed_timer_wait_time": "",
                    "max_reclaim_time": "",
                    "max_reclaim_leases": "",
                    "unwarned_reclaim_cycles": "",
                },
                "ha": {
                    "enabled": "0",
                    "this_server_name": "",
                    "max_unacked_clients": "2",
                },
            }
        },
    )
    
    logger.info("VM %d: reconfiguring Kea service", vm_id)
    
    if network_id:
        publish_log_update(network_id, "Reconfiguring Kea DHCP service...")
    
    exec_opnsense_api(
        vm_id=vm_id,
        node=node,
        method="POST",
        path="kea/service/reconfigure",
        api_key=api_key,
        api_secret=api_secret,
    )
    
    logger.info("VM %d: Kea subnet added via API for %s", vm_id, subnet_cidr)
    
    if network_id:
        publish_log_update(network_id, f"Kea DHCP configured for subnet {subnet_str}")
    
    return {"result": "saved", "interfaces": interfaces_str}


def _build_cfg(vm_id: int, node: str) -> OPNsenseConfigInVM:
    """Instantiate OPNsenseConfigInVM with the correct provider and settings."""
    provider = get_hypervisor_provider()
    return OPNsenseConfigInVM(
        provider,
        vm_id=vm_id,
        node=node,
        config_path=settings.OPNSENSE_CONFIG_PATH,
    )


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def create_opnsense_vlan(
    self,
    tenant_id: int,
    vlan_tag: int,
    ip_address: str,
    subnet: int = 24,
    dhcp_start: str = None,
    dhcp_end: str = None,
    vm_id: int = None,
    node: str = None,
    parent_if: str = None,
    description: str = "",
    wan_ip: str = None,
    api_key: str = None,
    api_secret: str = None,
):
    """Provision a VLAN on a specific OPNsense VM end-to-end."""
    try:
        vm_id = vm_id or settings.DEFAULT_OPNSENSE_VM_ID
        node = node or settings.DEFAULT_OPNSENSE_NODE
        parent_if = parent_if or settings.DEFAULT_OPNSENSE_PARENT_IF

        network_exists = False
        network_id = vlan_tag
        db = SessionLocal()
        try:
            tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
            if not tenant:
                raise ValueError(f"Tenant {tenant_id} not found")

            network = db.query(TenantNetwork).filter(
                TenantNetwork.tenant_id == tenant_id,
                TenantNetwork.vlan_id == vlan_tag,
                TenantNetwork.status.in_(["active", "pending"]),
            ).first()
            if network:
                network_id = network.id
                network_exists = True

            if not wan_ip:
                wan_ip = tenant.wan_ip
            if not api_key:
                api_key = tenant.opnsense_api_key
            if not api_secret:
                api_secret = tenant.opnsense_api_secret

            if not wan_ip:
                raise ValueError(f"Tenant {tenant_id} has no WAN IP")
            if not api_key or not api_secret:
                raise ValueError(f"Tenant {tenant_id} has no OPNsense API credentials")
        finally:
            db.close()

        if network_exists:
            db = SessionLocal()
            try:
                network = db.query(TenantNetwork).filter_by(id=network_id).first()
                if network:
                    network.status = "provisioning"
                    db.commit()
                    logger.info(f"Network {network_id} status updated to provisioning in database")
            finally:
                db.close()
            publish_status_update("network", network_id, "pending", "provisioning")

        publish_log_update(network_id, f"Starting VLAN {vlan_tag} provisioning...")

        cfg = _build_cfg(vm_id=vm_id, node=node)

        existing_vlans = cfg.get_vlan_list()
        existing_vlanifs = [v["vlanif"] for v in existing_vlans]
        existing_iface_names = cfg.get_interface_names()

        already = next(
            (v for v in existing_vlans
             if v["tag"] == vlan_tag and v["parent_if"] == parent_if),
            None,
        )

        if already:
            vlanif = already["vlanif"]
            logger.info(
                "VM %d: VLAN %d already exists as %s - checking opt and IP",
                vm_id, vlan_tag, vlanif,
            )
            publish_log_update(network_id, f"VLAN {vlan_tag} already exists — checking configuration...")

            opt_name = (
                cfg.get_opt_for_vlanif(vlanif)
                or _next_opt(existing_iface_names)
            )

            current_ip, current_subnet = cfg.get_interface_ip_and_subnet(opt_name)
            if current_ip != ip_address or current_subnet != subnet:
                logger.info(
                    "VM %d: %s IP/subnet mismatch (current=%s/%s, requested=%s/%d) - updating",
                    vm_id, opt_name, current_ip, current_subnet, ip_address, subnet,
                )
                publish_log_update(network_id, f"IP/subnet mismatch - updating {opt_name} to {ip_address}/{subnet}...")
                cfg.set_interface_ip(opt_name=opt_name, ip=ip_address, subnet=subnet)
                cfg.reload_config()
                publish_log_update(network_id, f"Interface updated and config reloaded")
            else:
                logger.info(
                    "VM %d: %s IP/subnet matches (%s/%d) - no config change needed",
                    vm_id, opt_name, ip_address, subnet,
                )

        else:
            vlanif = _next_vlanif(existing_vlanifs)
            opt_name = _next_opt(existing_iface_names)
            descr = description or f"tenant{tenant_id}-vlan{vlan_tag}"

            logger.info(
                "VM %d: provisioning new VLAN %d as %s/%s",
                vm_id, vlan_tag, vlanif, opt_name,
            )
            publish_log_update(network_id, f"Provisioning new VLAN {vlan_tag} as {vlanif}/{opt_name}...")
            
            publish_log_update(network_id, f"Adding VLAN device {vlanif} (tag={vlan_tag}) on {parent_if}...")
            cfg.add_vlan_device(tag=vlan_tag, parent_if=parent_if, vlanif=vlanif, descr=descr)
            publish_log_update(network_id, f"VLAN device added: {vlanif}")
            
            publish_log_update(network_id, f"Adding OPT interface {opt_name} -> {vlanif} ({ip_address}/{subnet})...")
            cfg.add_opt_interface(
                opt_name=opt_name, vlanif=vlanif, ip=ip_address,
                subnet=subnet, descr=descr.upper(),
            )
            publish_log_update(network_id, f"OPT interface added: {opt_name}")
            
            publish_log_update(network_id, f"Creating VLAN interface with IP {ip_address}/{subnet}...")
            cfg.create_vlan_iface_with_ip(
                vlanif=vlanif, tag=vlan_tag, parent_if=parent_if,
                ip=ip_address, subnet=subnet,
            )
            publish_log_update(network_id, f"VLAN interface created and UP")
            
            publish_log_update(network_id, f"Reloading OPNsense configuration...")
            cfg.reload_config()
            publish_log_update(network_id, f"OPNsense configuration reloaded")

        network = ".".join(ip_address.split(".")[:3])
        dhcp_start = dhcp_start or f"{network}.10"
        dhcp_end = dhcp_end or f"{network}.200"

        publish_log_update(network_id, f"Configuring DHCP server on {ip_address}/{subnet}...")
        kea_result = _setup_kea_dhcp(
            opt_name=opt_name,
            ip_gateway=ip_address,
            subnet_cidr=f"{ip_address}/{subnet}",
            dhcp_start=dhcp_start,
            dhcp_end=dhcp_end,
            vm_id=vm_id,
            node=node,
            api_key=api_key,
            api_secret=api_secret,
            network_id=network_id,
        )

        result = {
            "tenant_id": tenant_id,
            "vm_id": vm_id,
            "vlan_tag": vlan_tag,
            "vlanif": vlanif,
            "opt_name": opt_name,
            "ip": ip_address,
            "subnet": subnet,
            "dhcp_start": dhcp_start,
            "dhcp_end": dhcp_end,
            "kea_uuid": kea_result.get("uuid"),
        }
        logger.info("VM %d: VLAN %d complete for tenant %d: %s",
                    vm_id, vlan_tag, tenant_id, result)
        publish_log_update(network_id, f"VLAN {vlan_tag} provisioned successfully — IP {ip_address}/{subnet}")
        
        db = SessionLocal()
        try:
            network = db.query(TenantNetwork).filter_by(id=network_id).first()
            if network:
                network.status = "active"
                network.opnsense_interface = opt_name
                db.commit()
                logger.info(f"Network {network_id} status updated to active, interface={opt_name}")
        finally:
            db.close()
        
        publish_status_update("network", network_id, "provisioning", "active")
        return result

    except Exception as exc:
        logger.exception("VM %d: VLAN %d provisioning failed: %s",
                         vm_id or settings.DEFAULT_OPNSENSE_VM_ID, vlan_tag, exc)
        publish_log_update(network_id, f"VLAN provisioning failed: {exc}")
        if network_exists:
            db = SessionLocal()
            try:
                network = db.query(TenantNetwork).filter_by(id=network_id).first()
                if network:
                    network.status = "error"
                    db.commit()
                    logger.info(f"Network {network_id} status updated to error in database")
            finally:
                db.close()
            publish_status_update("network", network_id, "provisioning", "error")
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=2, default_retry_delay=15)
def remove_opnsense_vlan(
    self,
    tenant_id: int,
    vlan_tag: int,
    vm_id: int = None,
    node: str = None,
    parent_if: str = None,
):
    """Remove a VLAN from a specific OPNsense VM."""
    try:
        vm_id = vm_id or settings.DEFAULT_OPNSENSE_VM_ID
        node = node or settings.DEFAULT_OPNSENSE_NODE
        parent_if = parent_if or settings.DEFAULT_OPNSENSE_PARENT_IF

        cfg = _build_cfg(vm_id=vm_id, node=node)

        cfg.remove_vlan(vlan_tag=vlan_tag, parent_if=parent_if)
        cfg.reload_config()

        logger.info("VM %d: removed VLAN %d (tenant %d)", vm_id, vlan_tag, tenant_id)
        return {"removed": True, "vlan_tag": vlan_tag, "tenant_id": tenant_id, "vm_id": vm_id}

    except Exception as exc:
        logger.exception("VM %d: failed to remove VLAN %d: %s",
                         vm_id or settings.DEFAULT_OPNSENSE_VM_ID, vlan_tag, exc)
        raise self.retry(exc=exc)