import logging
from sqlalchemy.orm import Session
from app.models.tenant import Tenant, TenantStatus
from app.models.bridge_pool import BridgePool
from app.core.config import settings
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


def allocate_bridge(db: Session, tenant_id: int) -> Optional[int]:
    bridge_entry = db.query(BridgePool).filter(
        BridgePool.status == "available"
    ).with_for_update().first()

    if not bridge_entry:
        return None

    bridge_entry.status = "in_use"
    bridge_entry.tenant_id = tenant_id
    bridge_entry.allocated_at = datetime.utcnow()

    return bridge_entry.bridge_id


def release_bridge(db: Session, tenant_id: int) -> bool:
    bridge_entry = db.query(BridgePool).filter(
        BridgePool.tenant_id == tenant_id
    ).first()

    if not bridge_entry:
        return False

    bridge_entry.status = "available"
    bridge_entry.tenant_id = None
    bridge_entry.allocated_at = None

    return True


def create_proxmox_bridge(bridge_id: int, tenant_id: int) -> str:
    from app.providers import get_hypervisor_provider
    
    provider = get_hypervisor_provider()
    result = provider.create_bridge(bridge_id, tenant_id)
    return result.bridge_name


def delete_proxmox_bridge(bridge_id: int) -> bool:
    from app.providers import get_hypervisor_provider
    
    try:
        provider = get_hypervisor_provider()
        provider.delete_bridge(bridge_id)
        return True
    except Exception as e:
        logger.warning(f"Failed to delete bridge {bridge_id}: {e}")
        return False


def assign_pod(db: Session):
    """
    Picks the pod with the most tenants (fill before opening new pods).
    SKIP LOCKED ensures concurrent signups don't race on the same pod row.
    """
    from app.models.network import Pod
    return (
        db.query(Pod)
        .filter(
            Pod.status == "active",
            Pod.tenant_count < Pod.max_tenants
        )
        .order_by(Pod.tenant_count.desc())
        .with_for_update(skip_locked=True)
        .first()
    )


def approve_tenant(db: Session, tenant_id: int, template_vm_id: int = 9000,
                   dhcp_pool_start: str = None, dhcp_pool_end: str = None) -> dict:
    from app.models.network import Pod, TenantNetwork
    from app.services.ipam import allocate_subnet

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()

    if not tenant:
        raise ValueError("Tenant not found")

    if tenant.status not in [TenantStatus.PENDING_APPROVAL, TenantStatus.VERIFIED, TenantStatus.ERROR]:
        raise ValueError(f"Tenant is not in pending_approval, verified, or error status. Current: {tenant.status}")

    if tenant.bridge_id:
        raise ValueError("Tenant already has a bridge allocated")

    # 1. Assign pod
    pod = assign_pod(db)
    if not pod:
        raise ValueError("No pod capacity available")
    pod.tenant_count += 1
    tenant.pod_id = pod.id

    # 2. Allocate bridge via existing BridgePool logic
    bridge_id = allocate_bridge(db, tenant_id)
    if not bridge_id:
        raise ValueError("No bridge capacity available")

# 3. Allocate IP subnet
    # NOTE: allocate_vlan() is NOT called here.
    # The default LAN is untagged (vlan_id=None) and does not consume a VLAN ID.
    # VLANs are only allocated in create_network() for additional networks.
    subnet = allocate_subnet(db)

    # 4. Create TenantNetwork record for the default LAN
    network = TenantNetwork(
        tenant_id=tenant.id,
        pod_id=pod.id,
        ip_pool_id=subnet.id,
        cidr=subnet.cidr,
        gateway_ip=subnet.gateway_ip,
        vlan_id=None,
        name="default",
        is_default=True,
        status="active",
        provider_ref=f"vmbr{bridge_id}",
    )
    subnet.tenant_network_id = network.id
    db.add(network)

    # Set tenant fields
    vm_id = 10000 + (bridge_id - 100)
    vm_name = f"VM_OPNsense_{tenant.id}"
    bridge_name = f"vmbr{bridge_id}"
    lan_ip = subnet.gateway_ip  # Use real gateway from IPAM

    try:
        create_proxmox_bridge(bridge_id, tenant.id)
        tenant.bridge_id = bridge_id
        tenant.opnsense_vm_id = vm_id
        tenant.opnsense_vm_name = vm_name
        tenant.lan_ip = lan_ip
        tenant.dhcp_pool_start = dhcp_pool_start
        tenant.dhcp_pool_end = dhcp_pool_end
        tenant.status = TenantStatus.PROVISIONING
    except Exception as e:
        release_bridge(db, tenant_id)
        raise ValueError(f"Failed to create bridge: {str(e)}")

    db.commit()

    from app.workers.task_scheduler import provision_tenant_task
    provision_tenant_task.delay(
        tenant_id=tenant.id,
        pod_id=pod.id,
        bridge_id=bridge_id,
        gateway_ip=subnet.gateway_ip,
        cidr=subnet.cidr,
    )

    return {
        "tenant_id": tenant.id,
        "status": "provisioning_started",
        "bridge_id": bridge_id,
        "opnsense_vm_id": vm_id,
        "opnsense_vm_name": vm_name,
        "bridge_name": bridge_name,
        "lan_ip": lan_ip,
        "cidr": subnet.cidr,
    }


def destroy_tenant(db: Session, tenant_id: int) -> dict:
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()

    if not tenant:
        raise ValueError("Tenant not found")

    from app.workers.task_scheduler import destroy_tenant_task
    destroy_tenant_task.delay(tenant_id=tenant.id)

    return {
        "tenant_id": tenant.id,
        "status": "deprovisioning_started"
    }
