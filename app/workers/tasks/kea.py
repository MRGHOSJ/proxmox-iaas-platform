"""
Kea DHCP configuration tasks for OPNsense.
"""
import logging
import ipaddress

from app.workers.celery_app import celery_app
from app.workers.tasks.helpers import exec_opnsense_api
from app.core.database import SessionLocal
from app.models.tenant import Tenant
from app.core.websocket import publish_tenant_log_update


logger = logging.getLogger(__name__)


def configure_kea_dhcp(
    vm_id: int,
    node: str,
    api_key: str,
    api_secret: str,
    lan_cidr: str,
    lan_gateway: str,
    pool_start: str = None,
    pool_end: str = None,
    tenant_id: int = None,
):
    """
    Configure Kea DHCPv4 for LAN interface.
    
    Routes through Proxmox: Celery → Proxmox API → exec_in_vm() → OPNsense localhost:443
    
    Steps:
    1. Stop Dnsmasq service
    2. Set initial Kea config (disabled)
    3. Apply config
    4. Add subnet
    5. Set config again
    6. Apply
    7. Enable Kea
    8. Apply
    9. Stop Kea service
    10. Start Kea service
    """
    if not all([vm_id, node, api_key, api_secret, lan_cidr]):
        raise ValueError("Missing required parameters for Kea configuration")

    if not pool_start or not pool_end:
        network = ipaddress.ip_network(lan_cidr, strict=False)
        pool_start = str(network[10])
        pool_end = str(network[200])
        logger.info(f"Using default DHCP pool: {pool_start} - {pool_end}")
        publish_tenant_log_update(tenant_id, f"Using default DHCP pool: {pool_start} - {pool_end}")
        

    # Step 1: Stop Dnsmasq
    logger.info("Stopping dnsmasq service...")
    publish_tenant_log_update(tenant_id, "Stopping dnsmasq service...")
    exec_opnsense_api(
        vm_id=vm_id,
        node=node,
        method="POST",
        path="core/service/stop/dnsmasq",
        api_key=api_key,
        api_secret=api_secret,
    )

    # Step 4: Add subnet
    logger.info(f"Adding subnet {lan_cidr} with pool {pool_start} - {pool_end}...")
    publish_tenant_log_update(tenant_id, f"Adding subnet {lan_cidr} with pool {pool_start} - {pool_end}...")
    exec_opnsense_api(
        vm_id=vm_id,
        node=node,
        method="POST",
        path="kea/dhcpv4/add_subnet/",
        api_key=api_key,
        api_secret=api_secret,
        json_data={
            "subnet4": {
                "subnet": lan_cidr,
                "description": "LAN",
                "pools": f"{pool_start} - {pool_end}",
                "match-client-id": "0",
                "option_data_autocollect": "1",
                "option_data": {
                    "routers": lan_gateway,
                    "domain_name_servers": "",
                    "domain_name": "",
                    "domain_search": "",
                    "ntp_servers": "",
                },
                "next_server": ""
            }
        },
    )


    # Step 7: Enable Kea (set enabled=1)
    logger.info("Enabling Kea DHCP...")
    publish_tenant_log_update(tenant_id, "Enabling Kea DHCP...")
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
                    "interfaces": "lan",
                    "valid_lifetime": "4000",
                    "fwrules": "1",
                    "dhcp_socket_type": "raw"
                },
                "lexpire": {},
                "ha": {"enabled": "0", "max_unacked_clients": "2"}
            }
        },
    )

    # Step 8: Apply config
    logger.info("Applying enabled Kea config...")
    publish_tenant_log_update(tenant_id, "Applying enabled Kea config...")
    exec_opnsense_api(
        vm_id=vm_id,
        node=node,
        method="POST",
        path="kea/service/reconfigure",
        api_key=api_key,
        api_secret=api_secret,
    )

    # Step 10: Restart Kea service
    logger.info("Restarting Kea service...")
    publish_tenant_log_update(tenant_id, "Restarting Kea service...")
    exec_opnsense_api(
        vm_id=vm_id,
        node=node,
        method="POST",
        path="core/service/restart/kea-dhcp/v4",
        api_key=api_key,
        api_secret=api_secret,
    )

    logger.info(f"Kea DHCP configuration complete for VM {vm_id}")


def configure_kea_dhcp_for_tenant(tenant_id: int):
    """Celery task to configure Kea DHCP for a tenant."""
    db = SessionLocal()
    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            raise ValueError(f"Tenant {tenant_id} not found")

        if not tenant.opnsense_vm_id:
            raise ValueError(f"Tenant {tenant_id} has no OPNsense VM")

        if not tenant.opnsense_api_key or not tenant.opnsense_api_secret:
            raise ValueError(f"Tenant {tenant_id} has no API credentials")

        if not tenant.lan_ip:
            raise ValueError(f"Tenant {tenant_id} has no LAN IP configured")

        import ipaddress as ipmod
        if tenant.lan_ip and hasattr(tenant, 'lan_subnet') and tenant.lan_subnet:
            lan_cidr = f"{tenant.lan_ip}/{tenant.lan_subnet}"
        else:
            gateway_ip = tenant.lan_ip
            lan_cidr = f"{gateway_ip}/24"

        network = ipaddress.ip_network(lan_cidr, strict=False)
        lan_gateway = str(network[1])

        pool_start = tenant.dhcp_pool_start
        pool_end = tenant.dhcp_pool_end

        node = "pve"

        configure_kea_dhcp(
            vm_id=tenant.opnsense_vm_id,
            node=node,
            api_key=tenant.opnsense_api_key,
            api_secret=tenant.opnsense_api_secret,
            lan_cidr=lan_cidr,
            lan_gateway=lan_gateway,
            pool_start=pool_start,
            pool_end=pool_end,
        )

        return {"status": "success", "tenant_id": tenant_id}

    except Exception as e:
        logger.error(f"Failed to configure Kea for tenant {tenant_id}: {e}")
        raise

    finally:
        db.close()


@celery_app.task(name="tasks.configure_kea_dhcp", bind=True, max_retries=3, default_retry_delay=30)
def configure_kea_dhcp_task(self, tenant_id: int):
    """Celery task to configure Kea DHCP for a tenant."""
    return configure_kea_dhcp_for_tenant(tenant_id)