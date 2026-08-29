from sqlalchemy import func
from app.models.network import GlobalIPPool, VlanAllocation


def allocate_subnet(db):
    """
    Allocates the next free /24 from the global pool.
    Prefers 172.16.x.x (safe), falls back to 10.x.x.x (overflow).
    SELECT FOR UPDATE SKIP LOCKED is mandatory — prevents race conditions
    when multiple tenants sign up concurrently.
    """
    for pool_name in ["safe", "overflow"]:
        subnet = (
            db.query(GlobalIPPool)
            .filter_by(pool=pool_name, status="free")
            .with_for_update(skip_locked=True)
            .first()
        )
        if subnet:
            subnet.status = "allocated"
            subnet.allocated_at = func.now()
            return subnet

    raise ValueError("IP space exhausted across all pools")


def allocate_vlan(db, pod_id: int):
    """
    Allocates the next free VLAN ID for a pod.
    VLAN IDs are pod-local — reuse across pods is intentional.
    Only call this for additional (non-default) networks.
    The default LAN is always untagged (vlan_id=None) and does NOT consume a VLAN ID.
    """
    vlan = (
        db.query(VlanAllocation)
        .filter_by(pod_id=pod_id, status="free")
        .with_for_update(skip_locked=True)
        .first()
    )
    if not vlan:
        raise ValueError(f"Pod {pod_id} has no free VLAN IDs")

    vlan.status = "allocated"
    return vlan


def release_subnet(db, ip_pool_id: int):
    """Returns a /24 to the free pool. Call when a TenantNetwork is deleted."""
    subnet = db.query(GlobalIPPool).get(ip_pool_id)
    if subnet:
        subnet.status = "free"
        subnet.tenant_network_id = None
        subnet.allocated_at = None


def release_vlan(db, pod_id: int, vlan_id: int):
    """Returns a VLAN ID to the pod pool. Call when a non-default TenantNetwork is deleted."""
    vlan = db.query(VlanAllocation).filter_by(
        pod_id=pod_id, vlan_id=vlan_id
    ).first()
    if vlan:
        vlan.status = "free"
        vlan.tenant_network_id = None
