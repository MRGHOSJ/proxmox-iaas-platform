import logging
from fastapi import APIRouter, Depends, HTTPException, Request, Query, status
from sqlalchemy.orm import Session
from typing import Optional, List
from datetime import datetime

from app.core.database import get_db
from app.api.auth import get_current_user, get_client_ip
from app.core.dependencies import get_current_tenant
from app.core.iam import is_super_admin, has_permission
from app.core.audit import log_audit_event, AUDIT_ACTIONS
from app.services.reconciler import Reconciler
from app.services.provisioning import approve_tenant as provision_tenant, destroy_tenant
from app.models.vm import VM
from app.models.user import User
from app.models.tenant import Tenant, TenantStatus
from app.models.audit_log import AuditLog
from app.schemas.vm import VMResponse, VMStatusUpdate
from app.schemas.tenant import TenantApproveRequest, TenantApproveResponse, TenantResponse
from app.services.vm import validate_status_transition
from app.core.rate_limit import check_rate_limit
from app.core.exceptions import ResourceNotFoundError
from app.core.websocket import broadcast_status_change
from app.models.network import TenantNetwork
from app.providers import get_network_provider


logger = logging.getLogger(__name__)

reconciler = Reconciler()

router = APIRouter(prefix="/admin", tags=["Admin"])


def require_admin(current_user: User, db: Session) -> None:
    """Helper to check admin access with consistent error handling."""
    from app.core.iam import is_super_admin, is_tenant_admin
    
    # Super admins always have access
    if is_super_admin(current_user, db):
        return
    
    # Tenant admins have access to tenant-level admin functions
    if is_tenant_admin(current_user, current_user.tenant_id, db):
        return
    
    logger.warning(f"Admin access denied for user {current_user.id} ({current_user.username})")
    raise HTTPException(
        status_code=403, 
        detail="Admin access required"
    )


@router.get("/audit")
def audit_infrastructure(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Endpoint to audit the state of infrastructure.
    Requires: admin role
    """
    check_rate_limit(request, endpoint="admin_audit")
    require_admin(current_user, db)
    
    request_id = request.headers.get("X-Request-ID", "unknown")
    logger.info(f"Admin audit requested by {current_user.username} (request_id={request_id})")

    report = reconciler.audit(db)
    
    total_vms = db.query(VM).count()
    total_networks = db.query(TenantNetwork).filter(TenantNetwork.status == "active").count()
    
    orphaned_networks = []
    try:
        network_provider = get_network_provider("docker")
        infra_networks = network_provider.list_networks()
        db_network_names = {n.name for n in db.query(TenantNetwork).filter(TenantNetwork.status == "active").all()}
        infra_network_names = {net.name for net in infra_networks}
        orphaned_networks = list(infra_network_names - db_network_names)
    except Exception as e:
        logger.warning(f"Failed to audit networks: {e}")
    
    status_mismatches = [
        {
            "vm_id": item["vm_id"],
            "vm_name": item["name"],
            "db_status": item["db_status"],
            "actual_status": item["real_status"]
        }
        for item in report.get("drift", [])
    ]
    
    orphaned_vms = [item["name"] for item in report.get("ghosts", [])]
    
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "total_vms": total_vms,
        "total_networks": total_networks,
        "orphaned_vms": orphaned_vms,
        "orphaned_networks": orphaned_networks,
        "status_mismatches": status_mismatches
    }


@router.get("/audit-logs")
def get_audit_logs(
    request: Request,
    action: Optional[str] = Query(None, description="Filter by action type"),
    target_type: Optional[str] = Query(None, description="Filter by target type (user, vm, network)"),
    actor_id: Optional[int] = Query(None, description="Filter by actor user ID"),
    target_id: Optional[int] = Query(None, description="Filter by target ID"),
    start_date: Optional[datetime] = Query(None, description="Filter by start date"),
    end_date: Optional[datetime] = Query(None, description="Filter by end date"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum records to return"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Query audit logs with filtering.
    - Super admins see ALL audit logs (system-wide)
    - Users with audit:read permission see their current tenant's audit logs
    """
    # Get tenant from header manually (optional for super admins)
    tenant_id_header = request.headers.get("X-Tenant-ID")
    current_tenant_id = None
    if tenant_id_header:
        try:
            current_tenant_id = int(tenant_id_header)
        except ValueError:
            pass
    
    # Check permission: super_admin OR audit:read for the CURRENT tenant
    if not is_super_admin(current_user, db):
        if current_tenant_id and not has_permission(current_user, current_tenant_id, "audit:read", db):
            raise HTTPException(
                status_code=403, 
                detail="audit:read permission required for current tenant"
            )
    
    query = db.query(AuditLog)
    
    # Filter by tenant if not super_admin - use X-Tenant-ID header or user's primary tenant
    if not is_super_admin(current_user, db):
        if current_tenant_id:
            query = query.filter(AuditLog.tenant_id == current_tenant_id)
        else:
            # Fallback to user's primary tenant if no tenant header
            query = query.filter(AuditLog.tenant_id == current_user.tenant_id)
    
    if action:
        query = query.filter(AuditLog.action == action)
    if target_type:
        query = query.filter(AuditLog.target_type == target_type)
    if actor_id:
        query = query.filter(AuditLog.actor_id == actor_id)
    if target_id:
        query = query.filter(AuditLog.target_id == target_id)
    if start_date:
        query = query.filter(AuditLog.timestamp >= start_date)
    if end_date:
        query = query.filter(AuditLog.timestamp <= end_date)
    
    total = query.count()
    logs = query.order_by(AuditLog.timestamp.desc()).offset(skip).limit(limit).all()
    
    return {
        "total": total,
        "logs": [
            {
                "id": log.id,
                "timestamp": log.timestamp.isoformat() if log.timestamp else None,
                "action": log.action,
                "target_type": log.target_type,
                "target_id": log.target_id,
                "target_name": log.target_name,
                "actor_id": log.actor_id,
                "actor_username": log.actor_username,
                "old_value": log.old_value,
                "new_value": log.new_value,
                "details": log.details,
                "ip_address": log.ip_address,
                "request_id": log.request_id,
                "tenant_id": log.tenant_id,
                "impersonated_by": _extract_impersonated_by(log.details),
            }
            for log in logs
        ],
        "skip": skip,
        "limit": limit
    }


def _extract_impersonated_by(details: Optional[str]) -> Optional[str]:
    """Extract impersonated_by username from details string if present."""
    if not details:
        return None
    import re
    match = re.search(r'impersonated_by=(\w+)', details)
    return match.group(1) if match else None


@router.get("/audit-logs/{log_id}")
def get_audit_log(
    log_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get a specific audit log entry by ID.
    - Super admins can view any audit log
    - Tenant admins can only view logs for their tenant
    """
    if not is_super_admin(current_user, db):
        if not has_permission(current_user, current_user.tenant_id, "audit:read", db):
            raise HTTPException(
                status_code=403,
                detail="audit:read permission required"
            )
    
    log = db.query(AuditLog).filter(AuditLog.id == log_id).first()
    if not log:
        raise HTTPException(status_code=404, detail="Audit log not found")
    
    if not is_super_admin(current_user, db):
        if log.tenant_id != current_user.tenant_id:
            raise HTTPException(
                status_code=403,
                detail="Access denied to view audit log from different tenant"
            )
    
    return {
        "id": log.id,
        "timestamp": log.timestamp.isoformat() if log.timestamp else None,
        "action": log.action,
        "target_type": log.target_type,
        "target_id": log.target_id,
        "target_name": log.target_name,
        "actor_id": log.actor_id,
        "actor_username": log.actor_username,
        "old_value": log.old_value,
        "new_value": log.new_value,
        "details": log.details,
        "ip_address": log.ip_address,
        "request_id": log.request_id,
        "tenant_id": log.tenant_id,
        "impersonated_by": _extract_impersonated_by(log.details),
    }


@router.post("/impersonate/start")
def start_impersonation(
    request: Request,
    tenant_id: int = Query(..., description="Target tenant ID to impersonate"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Log the start of a super admin impersonation session.
    Only super admins can use this endpoint.
    """
    if not is_super_admin(current_user, db):
        raise HTTPException(status_code=403, detail="Super admin access required")

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    client_ip = get_client_ip(request)

    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["IMPERSONATION_START"],
        target_type="tenant",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=tenant.id,
        target_name=tenant.name,
        details=f"Super admin {current_user.username} started impersonating tenant {tenant.name}",
        ip_address=client_ip,
        tenant_id=tenant.id,
    )

    logger.info(f"Impersonation started: admin={current_user.username} tenant={tenant.name} (ID={tenant.id})")

    return {
        "status": "success",
        "message": f"Impersonation of tenant '{tenant.name}' started",
        "tenant_id": tenant.id,
        "tenant_name": tenant.name,
        "admin_username": current_user.username,
    }


@router.post("/impersonate/end")
def end_impersonation(
    request: Request,
    tenant_id: int = Query(..., description="Target tenant ID that was impersonated"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Log the end of a super admin impersonation session.
    Only super admins can use this endpoint.
    """
    if not is_super_admin(current_user, db):
        raise HTTPException(status_code=403, detail="Super admin access required")

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    client_ip = get_client_ip(request)

    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["IMPERSONATION_END"],
        target_type="tenant",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=tenant.id,
        target_name=tenant.name,
        details=f"Super admin {current_user.username} ended impersonation of tenant {tenant.name}",
        ip_address=client_ip,
        tenant_id=tenant.id,
    )

    logger.info(f"Impersonation ended: admin={current_user.username} tenant={tenant.name} (ID={tenant.id})")

    return {
        "status": "success",
        "message": f"Impersonation of tenant '{tenant.name}' ended",
        "tenant_id": tenant.id,
        "tenant_name": tenant.name,
        "admin_username": current_user.username,
    }


@router.post("/fix/{vm_id}")
def fix_vm(
    vm_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Endpoint to fix a specific VM that is in a 'ghost' or error state.
    Requires: admin role
    """
    check_rate_limit(request, endpoint="admin_fix")
    require_admin(current_user, db)

    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        raise HTTPException(status_code=404, detail="VM not found")

    request_id = request.headers.get("X-Request-ID", "unknown")
    logger.warning(
        f"Admin fix requested for VM {vm_id} by {current_user.username} "
        f"(request_id={request_id})"
    )

    success, message = reconciler.fix_ghost_vm(db, vm_id)
    
    if success:
        logger.info(f"VM {vm_id} fix succeeded: {message}")
        
        from app.api.auth import get_client_ip
        client_ip = get_client_ip(request)
        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["ADMIN_ACTION"],
            target_type="vm",
            actor_id=current_user.id,
            actor_username=current_user.username,
            target_id=vm_id,
            target_name=vm.name,
            details=f"Admin fix: {message}",
            request_id=request_id,
            ip_address=client_ip,
            tenant_id=vm.tenant_id
        )
        
        return {
            "status": "success",
            "message": message,
            "vm_id": vm_id,
            "vm_name": vm.name
        }
    else:
        logger.error(f"VM {vm_id} fix failed: {message}")
        raise HTTPException(
            status_code=400, 
            detail=f"Failed to fix VM: {message}"
        )


@router.post("/reconcile")
def reconcile_infrastructure(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Performs automatic repair:
    - Deletes orphaned containers (GC).
    - Removes ghost VM records from DB.
    - Corrects status drift in DB.
    Requires: admin role
    """
    check_rate_limit(request, endpoint="admin_reconcile")
    require_admin(current_user, db)
    
    request_id = request.headers.get("X-Request-ID", "unknown")
    logger.warning(
        f"Admin reconcile initiated by {current_user.username} "
        f"(request_id={request_id})"
    )
    
    results = reconciler.reconcile_all(db)
    
    logger.info(
        f"Reconciliation complete: orphan_purged={len(results.get('orphan_purged', []))}, "
        f"ghost_purged={len(results.get('ghost_purged', []))}, "
        f"drift_corrected={len(results.get('drift_corrected', []))}"
    )
    
    from app.api.auth import get_client_ip
    client_ip = get_client_ip(request)
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["RECONCILE"],
        target_type="infrastructure",
        actor_id=current_user.id,
        actor_username=current_user.username,
        details=f"orphan_purged={len(results.get('orphan_purged', []))}, ghost_purged={len(results.get('ghost_purged', []))}, drift_corrected={len(results.get('drift_corrected', []))}",
        request_id=request_id,
        ip_address=client_ip
    )
    
    return {
        "status": "reconciliation_complete",
        "actions_taken": results
    }


@router.patch("/vm/{vm_id}/status", response_model=VMResponse)
def override_vm_status(
    vm_id: int,
    status_update: VMStatusUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant = Depends(get_current_tenant)
):
    """
    Admin-only endpoint to override VM status.
    
    SECURITY: This bypasses normal state machine validation.
    Should only be used for fixing broken states.
    
    Requirements:
    - Admin role required
    - Reason field is mandatory (for audit trail)
    - Use force=True to bypass state transition validation
    
    All status overrides are logged for audit purposes.
    """
    check_rate_limit(request, endpoint="admin_status_override")
    require_admin(current_user, db)
    
    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        raise HTTPException(status_code=404, detail="VM not found")
    
    if not status_update.force:
        if not validate_status_transition(vm.status, status_update.status):
            allowed = VM.VALID_STATUS_TRANSITIONS.get(vm.status, [])
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status transition from '{vm.status}' to '{status_update.status}'. "
                       f"Allowed transitions from '{vm.status}': {allowed}. "
                       f"Use force=true to override."
            )
    
    request_id = request.headers.get("X-Request-ID", "unknown")
    logger.warning(
        f"ADMIN STATUS OVERRIDE: User={current_user.username} (ID={current_user.id}), "
        f"VM={vm.name} (ID={vm_id}), "
        f"OldStatus={vm.status}, NewStatus={status_update.status}, "
        f"Reason={status_update.reason}, Force={status_update.force}, "
        f"RequestID={request_id}"
    )
    
    old_status = vm.status
    vm.status = status_update.status
    db.commit()
    db.refresh(vm)
    
    try:
        import asyncio
        asyncio.run(broadcast_status_change("vm", vm.id, old_status, status_update.status))
    except Exception as ws_err:
        logger.warning(f"Failed to broadcast status change: {ws_err}")
    
    client_ip = get_client_ip(request)
    
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["VM_STATUS_OVERRIDE"],
        target_type="vm",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=vm.id,
        target_name=vm.name,
        old_value=old_status,
        new_value=status_update.status,
        details=f"Reason: {status_update.reason}, Force: {status_update.force}",
        request_id=request_id,
        ip_address=client_ip,
        tenant_id=current_tenant.id if current_tenant else None
    )
    
    return vm


@router.post("/tenant/{tenant_id}/approve", response_model=TenantApproveResponse)
def approve_tenant(
    tenant_id: int,
    request: Request,
    approve_request: TenantApproveRequest = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Approve a tenant and trigger provisioning.
    This allocates a bridge and OPNsense VM via Terraform.
    """
    check_rate_limit(request, endpoint="admin_tenant_approve")
    require_admin(current_user, db)

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if tenant.status != TenantStatus.PENDING_APPROVAL:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot approve tenant with status '{tenant.status}'. "
                   f"Tenant must be in 'pending_approval' state."
        )

    template_vm_id = approve_request.template_vm_id if approve_request else 9000
    dhcp_pool_start = None
    dhcp_pool_end = None
    
    if approve_request:
        dhcp_pool_start = approve_request.dhcp_pool_start
        dhcp_pool_end = approve_request.dhcp_pool_end

    try:
        result = provision_tenant(db, tenant_id, template_vm_id, dhcp_pool_start, dhcp_pool_end)

        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["TENANT_APPROVED"],
            target_type="tenant",
            actor_id=current_user.id,
            actor_username=current_user.username,
            target_id=tenant.id,
            target_name=tenant.name,
            new_value=f"bridge_id={result.get('bridge_id')}, vm_id={result.get('opnsense_vm_id')}",
            details=f"Tenant approved for provisioning"
        )

        from fastapi import Response
        return Response(
            content=TenantApproveResponse(
                tenant_id=tenant.id,
                status="provisioning_started",
                bridge_id=result.get("bridge_id"),
                opnsense_vm_id=result.get("opnsense_vm_id"),
                opnsense_vm_name=result.get("opnsense_vm_name"),
                bridge_name=result.get("bridge_name"),
                lan_ip=result.get("lan_ip"),
                message="Tenant provisioning started"
            ).model_dump_json(),
            media_type="application/json",
            status_code=status.HTTP_202_ACCEPTED
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to approve tenant {tenant_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to approve tenant: {str(e)}")


@router.delete("/tenant/{tenant_id}")
def delete_tenant(
    tenant_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Delete a tenant and destroy their OPNsense VM and bridge.
    """
    check_rate_limit(request, endpoint="admin_tenant_delete")
    require_admin(current_user, db)

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    try:
        result = destroy_tenant(db, tenant_id)

        log_audit_event(
            db=db,
            action="tenant_deleted",
            target_type="tenant",
            actor_id=current_user.id,
            actor_username=current_user.username,
            target_id=tenant.id,
            target_name=tenant.name,
            old_value=f"bridge_id={tenant.bridge_id}, vm_id={tenant.opnsense_vm_id}",
            details="Tenant deleted and resources destroyed"
        )

        return result

    except Exception as e:
        logger.error(f"Failed to delete tenant {tenant_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete tenant: {str(e)}")


@router.get("/tenant/{tenant_id}", response_model=TenantResponse)
def get_tenant(
    tenant_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get tenant details including provisioning info.
    """
    require_admin(current_user, db)

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    return tenant


@router.get("/resources")
def get_system_resources(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get system-wide resource usage statistics.
    Returns total and per-tenant resource usage for CPU, RAM, and disk.
    """
    from app.providers import get_hypervisor_provider
    from app.models.vm import VM
    from sqlalchemy import func

    require_admin(current_user, db)

    try:
        provider = get_hypervisor_provider()
        node_status = provider.get_node_status()
    except Exception as e:
        logger.error(f"Failed to get node status: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get node status: {str(e)}")

    total_memory_mb = getattr(node_status, 'total_memory_mb', None) or 0
    free_memory_mb = getattr(node_status, 'free_memory_mb', None) or 0

    cpu_count = getattr(node_status, 'cpu_count', None)
    if cpu_count is None:
        cpus = getattr(node_status, 'cpus', None)
        cpu_count = cpus if cpus else None

    if cpu_count is not None:
        total_cpu_cores = cpu_count
    else:
        total_cpu_cores = max(total_memory_mb // 1024 // 4, 8) if total_memory_mb else 2

    total_ram_gb = max(total_memory_mb // 1024, 4) if total_memory_mb else 0
    used_ram_gb = max((total_memory_mb - free_memory_mb) // 1024, 0) if total_memory_mb and free_memory_mb is not None else 0

    disk_total_gb = getattr(node_status, 'total_disk_gb', None)
    if disk_total_gb is None:
        disk_total_gb = getattr(node_status, 'disk_total_gb', None)

    disk_used_gb = getattr(node_status, 'disk_used_gb', None)
    if disk_used_gb is None:
        used_disks = db.query(func.coalesce(func.sum(VM.disk_size_mb), 0)).scalar() or 0
        disk_used_gb = round(used_disks / 1024, 2)

    if disk_total_gb is None:
        disk_total_gb = max(disk_used_gb * 2, 100) if disk_used_gb else 100

    node_name = getattr(node_status, 'node', None)
    if node_name is None:
        node_name = getattr(node_status, 'name', 'pve')
    vm_count = getattr(node_status, 'vm_count', 0) or 0

    tenant_stats = db.query(
        Tenant.id,
        Tenant.name,
        func.count(VM.id).label('vm_count'),
        func.coalesce(func.sum(VM.cpu), 0).label('cpu_used'),
        func.coalesce(func.sum(VM.ram), 0).label('ram_used_mb'),
        func.coalesce(func.sum(VM.disk_size_mb), 0).label('disk_used_mb')
    ).outerjoin(VM, VM.tenant_id == Tenant.id).group_by(Tenant.id, Tenant.name).all()

    by_tenant = []
    total_cpu_used = 0
    total_ram_used_mb = 0
    total_disk_used_mb = 0
    total_vms = 0

    for ts in tenant_stats:
        cpu_used = ts.cpu_used or 0
        ram_used_mb = ts.ram_used_mb or 0
        disk_used_mb = ts.disk_used_mb or 0
        total_cpu_used += cpu_used
        total_ram_used_mb += ram_used_mb
        total_disk_used_mb += disk_used_mb
        total_vms += ts.vm_count or 0

        by_tenant.append({
            "tenant_id": ts.id,
            "tenant_name": ts.name,
            "vm_count": ts.vm_count or 0,
            "cpu_cores_used": cpu_used,
            "ram_gb_used": round(ram_used_mb / 1024, 2),
            "disk_gb_used": round(disk_used_mb / 1024, 2)
        })

    return {
        "node": {
            "name": node_name,
            "cpu_cores_total": total_cpu_cores,
            "cpu_cores_used": total_cpu_used,
            "cpu_usage_percent": round((getattr(node_status, 'cpu_usage', None) or 0) * 100, 1),
            "ram_gb_total": total_ram_gb,
            "ram_gb_used": used_ram_gb,
            "disk_gb_total": disk_total_gb,
            "disk_gb_used": disk_used_gb,
            "vm_count": vm_count
        },
        "totals": {
            "cpu_cores_total": total_cpu_cores,
            "cpu_cores_used": total_cpu_used,
            "ram_gb_total": total_ram_gb,
            "ram_gb_used": used_ram_gb,
            "disk_gb_total": disk_total_gb,
            "disk_gb_used": round(disk_used_gb, 2),
            "vm_count_total": total_vms
        },
        "by_tenant": by_tenant
    }


@router.get("/health")
def get_system_health(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get system health status for all infrastructure components.
    """
    from app.core.database import engine
    from app.providers import get_hypervisor_provider
    import redis
    
    require_admin(current_user, db)
    
    health = {
        "database": {"status": "unknown", "message": ""},
        "redis": {"status": "unknown", "message": ""},
        "proxmox": {"status": "unknown", "message": ""}
    }
    
    # Check database
    try:
        with engine.connect() as conn:
            conn.execute(db.text("SELECT 1"))
        health["database"] = {"status": "healthy", "message": "Connected"}
    except Exception as e:
        health["database"] = {"status": "unhealthy", "message": str(e)}
    
    # Check Redis
    try:
        from app.core.config import settings
        r = redis.Redis(
            host=settings.REDIS_HOST or "localhost",
            port=settings.REDIS_PORT or 6379,
            db=settings.REDIS_DB or 0,
            decode_responses=True
        )
        r.ping()
        health["redis"] = {"status": "healthy", "message": "Connected"}
    except Exception as e:
        health["redis"] = {"status": "unhealthy", "message": str(e)}
    
    # Check Proxmox
    try:
        provider = get_hypervisor_provider()
        node_status = provider.get_node_status()
        health["proxmox"] = {"status": "healthy", "message": f"{node_status.vm_count} VMs"}
    except Exception as e:
        health["proxmox"] = {"status": "unhealthy", "message": str(e)}
    
    overall = "healthy" if all(h["status"] == "healthy" for h in health.values()) else "degraded"
    
    return {
        "overall": overall,
        "components": health
    }


@router.get("/activity")
def get_recent_activity(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = Query(10, ge=1, le=50)
):
    """
    Get recent system activity.
    Returns recent VMs created, tenant events, and errors.
    """
    from app.models.audit_log import AuditLog
    
    require_admin(current_user, db)
    
    # Get recent audit logs
    recent_logs = db.query(AuditLog).order_by(
        AuditLog.timestamp.desc()
    ).limit(limit).all()
    
    # Get recent VMs
    recent_vms = db.query(VM).order_by(
        VM.created_at.desc()
    ).limit(limit).all()
    
    # Get recent tenants
    recent_tenants = db.query(Tenant).order_by(
        Tenant.created_at.desc()
    ).limit(limit).all()
    
    activity = []
    
    for log in recent_logs:
        activity.append({
            "type": "audit",
            "timestamp": log.timestamp.isoformat() if log.timestamp else None,
            "action": log.action,
            "target_type": log.target_type,
            "target_name": log.target_name,
            "actor_username": log.actor_username
        })
    
    for vm in recent_vms:
        activity.append({
            "type": "vm",
            "timestamp": vm.created_at.isoformat() if vm.created_at else None,
            "action": "created",
            "target_type": "vm",
            "target_name": vm.name,
            "status": vm.status,
            "provider": vm.provider
        })
    
    for tenant in recent_tenants:
        activity.append({
            "type": "tenant",
            "timestamp": tenant.created_at.isoformat() if tenant.created_at else None,
            "action": "created",
            "target_type": "tenant",
            "target_name": tenant.name,
            "status": "active" if tenant.is_active else "inactive"
        })
    
    # Sort by timestamp and limit
    activity.sort(key=lambda x: x["timestamp"] or "", reverse=True)
    activity = activity[:limit]
    
    return {
        "activities": activity,
        "count": len(activity)
    }
