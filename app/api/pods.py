from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.models.user import User
from app.models.network import Pod
from app.schemas.pod import PodCreate, PodUpdate, PodResponse, PodListResponse
from app.services.seed import seed_vlan_pool
from app.core.audit import log_audit_event, AUDIT_ACTIONS

router = APIRouter(prefix="/admin/pods", tags=["Admin"])


def require_super_admin(current_user: User, db: Session) -> None:
    """Check if user is super admin."""
    from app.core.iam import is_super_admin
    if not is_super_admin(current_user, db):
        raise HTTPException(status_code=403, detail="Super admin access required")


@router.get("/", response_model=PodListResponse)
def list_pods(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all pods."""
    require_super_admin(current_user, db)
    
    pods = db.query(Pod).all()
    return {"total": len(pods), "pods": pods}


@router.get("/{pod_id}", response_model=PodResponse)
def get_pod(
    pod_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get a specific pod."""
    require_super_admin(current_user, db)
    
    pod = db.query(Pod).filter(Pod.id == pod_id).first()
    if not pod:
        raise HTTPException(status_code=404, detail="Pod not found")
    return pod


@router.post("/", response_model=PodResponse, status_code=201)
def create_pod(
    request: PodCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new pod."""
    require_super_admin(current_user, db)
    
    # Check if pod name already exists
    existing = db.query(Pod).filter(Pod.name == request.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Pod with this name already exists")
    
    # Validate provider_type
    valid_providers = ["proxmox", "vsphere", "kvm", "hyperv"]
    if request.provider_type not in valid_providers:
        raise HTTPException(status_code=400, detail=f"Invalid provider_type. Must be one of: {valid_providers}")
    
    pod = Pod(
        name=request.name,
        provider_type=request.provider_type,
        node_names=request.node_names,
        max_tenants=request.max_tenants,
        tenant_count=0,
        status="active",
    )
    db.add(pod)
    db.flush()
    
    # Auto-seed VLAN pool for this pod
    seed_vlan_pool(db, pod_id=pod.id)
    
    db.commit()
    db.refresh(pod)
    
    # Audit log
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS.get("ADMIN_ACTION", "admin_action"),
        target_type="pod",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=pod.id,
        target_name=pod.name,
        details=f"Created pod: {pod.provider_type} ({pod.node_names})",
    )
    
    return pod


@router.patch("/{pod_id}", response_model=PodResponse)
def update_pod(
    pod_id: int,
    request: PodUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update a pod."""
    require_super_admin(current_user, db)
    
    pod = db.query(Pod).filter(Pod.id == pod_id).first()
    if not pod:
        raise HTTPException(status_code=404, detail="Pod not found")
    
    if request.name is not None:
        # Check for duplicate name
        existing = db.query(Pod).filter(Pod.name == request.name, Pod.id != pod_id).first()
        if existing:
            raise HTTPException(status_code=400, detail="Pod name already exists")
        pod.name = request.name
    
    if request.max_tenants is not None:
        if request.max_tenants < pod.tenant_count:
            raise HTTPException(status_code=400, detail="max_tenants cannot be less than current tenant_count")
        pod.max_tenants = request.max_tenants
    
    if request.status is not None:
        valid_statuses = ["active", "maintenance", "full"]
        if request.status not in valid_statuses:
            raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {valid_statuses}")
        pod.status = request.status
    
    db.commit()
    db.refresh(pod)
    
    # Audit log
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS.get("ADMIN_ACTION", "admin_action"),
        target_type="pod",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=pod.id,
        target_name=pod.name,
        details=f"Updated pod: status={pod.status}, max_tenants={pod.max_tenants}",
    )
    
    return pod


@router.delete("/{pod_id}", status_code=204)
def delete_pod(
    pod_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a pod (only if it has no tenants)."""
    require_super_admin(current_user, db)
    
    pod = db.query(Pod).filter(Pod.id == pod_id).first()
    if not pod:
        raise HTTPException(status_code=404, detail="Pod not found")
    
    if pod.tenant_count > 0:
        raise HTTPException(status_code=400, detail="Cannot delete pod with existing tenants")
    
    pod_name = pod.name
    db.delete(pod)
    db.commit()
    
    # Audit log
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS.get("ADMIN_ACTION", "admin_action"),
        target_type="pod",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=pod_id,
        target_name=pod_name,
        details="Deleted pod",
    )
    
    return None
