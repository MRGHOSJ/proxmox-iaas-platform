from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel

from app.core.database import get_db
from app.core.iam import get_current_user
from app.models.user import User
from app.models.bridge_pool import BridgePool
from app.models.tenant import Tenant
from app.api.tenant import is_super_admin
from app.core.audit import log_audit_event, AUDIT_ACTIONS
from app.api.auth import get_client_ip


router = APIRouter(prefix="/bridges", tags=["Bridge Pool"])


class BridgePoolEntry(BaseModel):
    bridge_id: int
    status: str
    tenant_id: Optional[int] = None
    tenant_name: Optional[str] = None
    allocated_at: Optional[str] = None


class BridgePoolStats(BaseModel):
    total: int
    available: int
    in_use: int


class BridgePoolResponse(BaseModel):
    total: int
    available: int
    in_use: int
    bridges: List[BridgePoolEntry]
    skip: int = 0
    limit: int = 100
    total_filtered: int = 0


@router.get("", response_model=BridgePoolResponse)
def list_bridges(
    status_filter: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """List all bridges with optional status filter and pagination. Super admin only."""
    if not is_super_admin(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required"
        )
    
    # Get counts for stats (before pagination)
    total_count = db.query(BridgePool).count()
    available_count = db.query(BridgePool).filter(BridgePool.status == "available").count()
    in_use_count = db.query(BridgePool).filter(BridgePool.status == "in_use").count()
    
    # Apply filters and pagination
    query = db.query(BridgePool).order_by(BridgePool.bridge_id)
    
    if status_filter:
        query = query.filter(BridgePool.status == status_filter)
    
    total_filtered = query.count()
    bridges = query.offset(skip).limit(limit).all()
    
    # Get tenant names for allocated bridges
    results = []
    
    for bridge in bridges:
        tenant_name = None
        if bridge.tenant_id:
            tenant = db.query(Tenant).filter(Tenant.id == bridge.tenant_id).first()
            if tenant:
                tenant_name = tenant.name
        
        allocated_at = bridge.allocated_at.isoformat() if bridge.allocated_at else None
        
        results.append(BridgePoolEntry(
            bridge_id=bridge.bridge_id,
            status=bridge.status,
            tenant_id=bridge.tenant_id,
            tenant_name=tenant_name,
            allocated_at=allocated_at,
        ))
        
        if bridge.status == "available":
            available_count += 1
        else:
            in_use_count += 1
    
    return BridgePoolResponse(
        total=total_count,
        available=available_count,
        in_use=in_use_count,
        bridges=results,
        skip=skip,
        limit=limit,
        total_filtered=total_filtered
    )


@router.get("/stats", response_model=BridgePoolStats)
def get_bridge_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get bridge pool statistics. Super admin only."""
    if not is_super_admin(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required"
        )
    
    total = db.query(BridgePool).count()
    available = db.query(BridgePool).filter(BridgePool.status == "available").count()
    in_use = db.query(BridgePool).filter(BridgePool.status == "in_use").count()
    
    return BridgePoolStats(
        total=total,
        available=available,
        in_use=in_use
    )


@router.post("/{bridge_id}/release", response_model=BridgePoolEntry)
def release_bridge(
    bridge_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Manually release a bridge from a tenant. Super admin only."""
    if not is_super_admin(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required"
        )
    
    bridge = db.query(BridgePool).filter(BridgePool.bridge_id == bridge_id).first()
    
    if not bridge:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Bridge {bridge_id} not found"
        )
    
    if bridge.status == "available":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Bridge {bridge_id} is already available"
        )
    
    previous_tenant_id = bridge.tenant_id
    
    # Update tenant if exists
    if bridge.tenant_id:
        tenant = db.query(Tenant).filter(Tenant.id == bridge.tenant_id).first()
        if tenant:
            tenant.bridge_id = None
            tenant.opnsense_vm_id = None
            tenant.status = "error"
            tenant.error = "Bridge manually released by admin"
    
    # Release bridge
    bridge.status = "available"
    bridge.tenant_id = None
    bridge.allocated_at = None
    
    db.commit()
    
    # Audit log
    request_id = request.headers.get("X-Request-ID", "unknown")
    client_ip = get_client_ip(request)
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS.get("ADMIN_ACTION", "admin_action"),
        target_type="bridge",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=bridge_id,
        target_name=f"vmbr{bridge_id}",
        old_value=f"tenant_id={previous_tenant_id},status=in_use",
        new_value="tenant_id=None,status=available",
        details="Bridge manually released by super admin",
        request_id=request_id,
        ip_address=client_ip
    )
    
    return BridgePoolEntry(
        bridge_id=bridge.bridge_id,
        status=bridge.status,
        tenant_id=None,
        tenant_name=None,
        allocated_at=None,
    )
