from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_tenant, get_current_user
from app.models.tenant import Tenant
from app.models.network import TenantNetwork
from app.models.user import User
from app.services.ipam import allocate_subnet, allocate_vlan, release_subnet, release_vlan
from app.schemas.network import TenantNetworkCreate, TenantNetworkResponse, TenantNetworkListResponse
from app.workers.task_scheduler import create_opnsense_vlan
from app.providers import get_hypervisor_provider
from app.core.audit import log_audit_event, AUDIT_ACTIONS
from app.api.auth import get_client_ip

router = APIRouter(prefix="/networks", tags=["networks"])


@router.get("/", response_model=TenantNetworkListResponse)
def list_networks(
    current_tenant: Tenant = Depends(get_current_tenant),
    db: Session = Depends(get_db),
):
    networks = (
        db.query(TenantNetwork)
        .filter_by(tenant_id=current_tenant.id)
        .all()
    )
    return {"total": len(networks), "networks": networks}


@router.get("/{network_id}", response_model=TenantNetworkResponse)
def get_network(
    network_id: int,
    current_tenant: Tenant = Depends(get_current_tenant),
    db: Session = Depends(get_db),
):
    network = db.query(TenantNetwork).filter_by(
        id=network_id,
        tenant_id=current_tenant.id,
    ).first()
    if not network:
        raise HTTPException(status_code=404, detail="Network not found")
    return network


@router.get("/{network_id}/logs")
def get_network_logs(
    network_id: int,
    current_tenant: Tenant = Depends(get_current_tenant),
    db: Session = Depends(get_db),
):
    network = db.query(TenantNetwork).filter_by(
        id=network_id,
        tenant_id=current_tenant.id,
    ).first()
    if not network:
        raise HTTPException(status_code=404, detail="Network not found")
    return {"logs": [], "network_id": network_id}


@router.post("/", response_model=TenantNetworkResponse, status_code=201)
def create_network(
    network_request: TenantNetworkCreate,
    request: Request,
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Creates an additional isolated network for the tenant.
    Each additional network gets its own /24 subnet and a VLAN tag.
    VMs on different networks can only communicate through OPNsense.
    """
    if not current_tenant.pod_id:
        raise HTTPException(status_code=400, detail="Tenant not fully provisioned — no pod assigned")

    # Allocate subnet and VLAN (VLAN only allocated here, never for default network)
    subnet = allocate_subnet(db)
    vlan = allocate_vlan(db, current_tenant.pod_id)

    # Get the default network's bridge — additional networks share it
    default_network = db.query(TenantNetwork).filter_by(
        tenant_id=current_tenant.id,
        is_default=True,
    ).first()
    if not default_network:
        raise HTTPException(status_code=400, detail="Tenant has no default network")

    network = TenantNetwork(
        tenant_id=current_tenant.id,
        pod_id=current_tenant.pod_id,
        ip_pool_id=subnet.id,
        cidr=subnet.cidr,
        gateway_ip=subnet.gateway_ip,
        vlan_id=vlan.vlan_id,
        name=network_request.name,
        is_default=False,
        status="pending",
        provider_ref=default_network.provider_ref,
    )
    subnet.tenant_network_id = network.id
    vlan.tenant_network_id = network.id
    db.add(network)
    db.commit()
    db.refresh(network)

    # Create the VLAN interface on the hypervisor if this network has a VLAN
    try:
        provider = get_hypervisor_provider()
        provider.create_network(network)
    except Exception as e:
        # Log but don't fail - the network record exists, VLAN can be created later
        import logging
        logging.getLogger(__name__).warning(f"Failed to create network on hypervisor: {e}")

    # Tell OPNsense to create the VLAN sub-interface
    create_opnsense_vlan.delay(
        tenant_id=current_tenant.id,
        vlan_tag=vlan.vlan_id,
        ip_address=subnet.gateway_ip,
        subnet=24,
    )

    # Audit log
    request_id = request.headers.get("X-Request-ID", "unknown")
    client_ip = get_client_ip(request)
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["NETWORK_CREATE"],
        target_type="network",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=network.id,
        target_name=network.name,
        new_value=f"cidr={network.cidr},vlan_id={network.vlan_id},gateway={network.gateway_ip}",
        request_id=request_id,
        ip_address=client_ip,
        tenant_id=current_tenant.id
    )

    return network


@router.delete("/{network_id}", status_code=204)
def delete_network(
    network_id: int,
    request: Request,
    current_tenant: Tenant = Depends(get_current_tenant),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Deletes a non-default network and returns its IP and VLAN to the pool.
    The default network cannot be deleted (it is tied to OPNsense and the bridge).
    """
    network = db.query(TenantNetwork).filter_by(
        id=network_id,
        tenant_id=current_tenant.id,
    ).first()
    if not network:
        raise HTTPException(status_code=404, detail="Network not found")
    if network.is_default:
        raise HTTPException(status_code=400, detail="Cannot delete the default network")

    network_name = network.name
    network_cidr = network.cidr

    # Return IP and VLAN to their pools
    if network.ip_pool_id:
        release_subnet(db, network.ip_pool_id)
    if network.vlan_id:
        release_vlan(db, network.pod_id, network.vlan_id)

    network.status = "deleted"
    db.commit()

    # Audit log
    request_id = request.headers.get("X-Request-ID", "unknown")
    client_ip = get_client_ip(request)
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["NETWORK_DELETE"],
        target_type="network",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=network_id,
        target_name=network_name,
        old_value=f"cidr={network_cidr}",
        request_id=request_id,
        ip_address=client_ip,
        tenant_id=current_tenant.id
    )
