import logging
import hashlib
from datetime import datetime, timedelta
import math
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request, WebSocket
from fastapi.websockets import WebSocketDisconnect
from sqlalchemy.orm import Session, Query as QueryType
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text, func
from app.models.vm import VM, VMDiskResize
from typing import Optional, NoReturn

from app.core.database import get_db
from app.api.auth import get_current_user, get_client_ip
from app.core.dependencies import get_current_user as get_user, get_current_tenant
from app.models.tenant import Tenant
from app.core.iam import (
    has_permission,
    has_any_permission,
    is_tenant_admin,
    is_super_admin,
    RequirePermission,
)
from app.schemas.vm import (
    VMCreate, VMResponse, VMUpdate,
    VMStatsResponse, VMLogsResponse, VMListResponse,
    VMSnapshotCreate, VMSnapshotResponse,
    VMProvisionRequest, DiskResizeRequest, DiskResizeResponse,
    VMDiskInfo, StorageInfoResponse,
    VMResourcesResponse, CPUResizeRequest, RAMResizeRequest, ResourceResizeResponse,
    SshInfoResponse, SshKeyRegenerateResponse,
)
from app.services import vm as vm_service
from app.workers.task_scheduler import deploy_vm_task, provision_vm_task
from app.models.user import User
from app.providers import get_hypervisor_provider
from app.core.exceptions import (
    ResourceConflictError,
    ResourceNotFoundError,
    InvalidStateTransitionError,
    ProviderUnavailableError,
)
from app.core.rate_limit import check_rate_limit
from app.core.websocket import broadcast_status_change
from app.services.quota import check_vm_quota, check_disk_resize_quota, QuotaExceededError
from app.core.audit import log_audit_event, AUDIT_ACTIONS


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vm", tags=["Virtual Machines"])


def raise_http_error(status_code: int, detail: str, log_msg: Optional[str] = None) -> NoReturn:
    """Standardized error response with logging."""
    if log_msg:
        logger.warning(log_msg)
    raise HTTPException(status_code=status_code, detail=detail)


def handle_service_exception(exc: Exception, vm_id: Optional[int] = None) -> NoReturn:
    """Converts Service exceptions to standardized HTTPExceptions."""
    exc_str = str(exc).lower()
    context = f" for VM {vm_id}" if vm_id else ""
    
    if isinstance(exc, ResourceNotFoundError):
        raise_http_error(404, str(exc), f"Resource not found{context}: {exc}")
    elif isinstance(exc, ResourceConflictError):
        raise_http_error(409, str(exc), f"Conflict{context}: {exc}")
    elif isinstance(exc, InvalidStateTransitionError):
        raise_http_error(400, str(exc), f"Invalid state transition{context}: {exc}")
    elif isinstance(exc, ProviderUnavailableError):
        raise_http_error(503, str(exc), f"Provider unavailable{context}: {exc}")
    elif "already exists" in exc_str:
        raise_http_error(409, str(exc), f"Duplicate resource{context}")
    elif "not found" in exc_str:
        raise_http_error(404, str(exc), f"Not found{context}")
    elif "invalid" in exc_str or "cannot" in exc_str:
        raise_http_error(400, str(exc), f"Invalid operation{context}: {exc}")
    else:
        logger.error(f"Unexpected service error{context}: {exc}", exc_info=True)
        raise_http_error(500, "An internal error occurred", f"Unexpected error{context}: {exc}")


def acquire_vm_name_lock(db: Session, vm_name: str, timeout: int = 5) -> bool:
    """
    Acquire an advisory lock based on VM name hash to prevent race conditions.
    Uses PostgreSQL advisory locks for distributed safety.
    """
    name_hash = int(hashlib.md5(vm_name.encode()).hexdigest()[:8], 16)
    try:
        result = db.execute(
            text("SELECT pg_try_advisory_xact_lock(:lock_key)"),
            {"lock_key": name_hash}
        )
        acquired = result.scalar()
        if acquired:
            logger.debug(f"Acquired advisory lock for VM name: {vm_name}")
            return True
        else:
            logger.warning(f"Could not acquire lock for VM name: {vm_name}")
            return False
    except Exception as e:
        error_msg = str(e).lower()
        if "sqlite" in error_msg:
            logger.error(
                f"CRITICAL: PostgreSQL advisory locks required for VM creation but SQLite detected. "
                f"Use PostgreSQL for production."
            )
            raise RuntimeError(
                "PostgreSQL is required for safe VM operations. SQLite is not supported in production."
            )
        logger.error(f"Advisory lock failed: {e}")
        return False


@router.post("/create", response_model=VMResponse, status_code=status.HTTP_201_CREATED)
def create_vm(
    vm_data: VMCreate, 
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Endpoint to provision a new VM.
    Requires: vm_operator, vm_admin, or admin role
    
    Transaction Flow (Improved for Integrity):
    1. Acquire advisory lock on VM name
    2. Allocate IP (if network specified)
    3. Create VM record in 'creating' status and COMMIT
    4. Dispatch Celery task (after VM exists in DB)
    5. Update VM with task_id and set status to 'pending'
    6. On Celery failure, mark VM as 'error' (not orphaned)
    7. On any early failure, rollback IP allocation
    
    This ensures:
    - VM always exists in DB before task runs
    - Task can always find the VM to update
    - Failures leave VM in recoverable 'error' state
    """
    check_rate_limit(request, endpoint="vm_create")
    
    if not has_permission(current_user, current_tenant.id, "vm:create", db):
        raise_http_error(
            403,
            "You do not have permission to create VMs",
            f"User {current_user.id} denied VM creation - insufficient permissions"
        )
    
    try:
        check_vm_quota(
            tenant_id=current_tenant.id,
            db=db,
            cpu=vm_data.cpu,
            ram=vm_data.ram,
            disk_size_gb=vm_data.disk_size_gb
        )
    except QuotaExceededError as e:
        raise_http_error(
            400,
            f"Quota exceeded: {e.resource} limit of {e.limit} reached ({e.current} used, requested {e.requested} more)",
            f"Quota exceeded for tenant {current_tenant.id}: {e.resource}"
        )
    
    if not acquire_vm_name_lock(db, vm_data.name):
        raise_http_error(
            409,
            "VM is currently being created. Please try a different name or wait.",
            f"VM name lock contention: {vm_data.name}"
        )
    
    new_vm = None
    task = None
    
    try:
        # Docker network support removed - using TenantNetwork for Proxmox VMs
        # network_id on VMCreate is deprecated
        if vm_data.network_id and vm_data.provider == "docker":
            raise_http_error(
                400,
                "Docker network provisioning is no longer supported. Use Proxmox provider with TenantNetwork.",
                "Docker network provisioning deprecated"
            )
        
        vm_data_dict = vm_data.model_dump()
        
        new_vm = VM(
            **vm_data_dict,
            owner_id=current_user.id,
            tenant_id=current_tenant.id,
            status="creating",
            ip_address=None
        )
        
        db.add(new_vm)
        db.commit()
        db.refresh(new_vm)
        
        logger.info(f"VM {new_vm.id} created in 'creating' state, now dispatching Celery task")
        
        try:
            task = deploy_vm_task.delay(
                vm_id=new_vm.id, 
                vm_data_dict=vm_data.model_dump(), 
                terraform_context_dict={"provider": vm_data.provider}
            )
            
            if not task or not task.id:
                raise ProviderUnavailableError("Celery", "No task ID returned")
            
            new_vm.celery_task_id = task.id
            new_vm.status = "pending"
            db.commit()
            db.refresh(new_vm)
            
            try:
                import asyncio
                asyncio.run(broadcast_status_change("vm", new_vm.id, None, "pending"))
            except Exception as ws_err:
                logger.warning(f"Failed to broadcast status change: {ws_err}")
            
            from app.core.audit import log_audit_event, AUDIT_ACTIONS
            request_id = request.headers.get("X-Request-ID", "unknown")
            client_ip = get_client_ip(request)
            log_audit_event(
                db=db,
                action=AUDIT_ACTIONS["VM_CREATE"],
                target_type="vm",
                actor_id=current_user.id,
                actor_username=current_user.username,
                target_id=new_vm.id,
                target_name=new_vm.name,
                new_value=f"provider={vm_data.provider},cpu={vm_data.cpu},ram={vm_data.ram},network_id={vm_data.network_id}",
                request_id=request_id,
                ip_address=client_ip,
                tenant_id=current_tenant.id if current_tenant else None
            )
            
            logger.info(f"VM {new_vm.id} ({new_vm.name}) created by user {current_user.id}, task {task.id}")
            return new_vm
            
        except ProviderUnavailableError:
            raise
        except Exception as celery_error:
            logger.error(f"Celery task dispatch failed for VM {new_vm.id}: {celery_error}")
            
            new_vm.status = "error"
            db.commit()
            
            try:
                import asyncio
                asyncio.run(broadcast_status_change("vm", new_vm.id, "pending", "error"))
            except Exception as ws_err:
                logger.warning(f"Failed to broadcast status change: {ws_err}")
            
            raise_http_error(
                503,
                "Task queue is temporarily unavailable. VM creation failed - please retry.",
                f"Celery unavailable after VM creation: {celery_error}"
            )
        
    except HTTPException:
        raise
    except IntegrityError as e:
        db.rollback()
        logger.warning(f"VM creation race condition detected: {e}")
        
        raise_http_error(
            409,
            "VM name already exists. Please choose a different name.",
            f"Duplicate VM name: {vm_data.name}"
        )
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to create VM {vm_data.name}: {e}", exc_info=True)
        
        if new_vm and new_vm.id:
            try:
                new_vm.status = "error"
                db.commit()
                
                try:
                    import asyncio
                    asyncio.run(broadcast_status_change("vm", new_vm.id, "creating", "error"))
                except Exception as ws_err:
                    logger.warning(f"Failed to broadcast status change: {ws_err}")
                    
            except Exception:
                pass
        
        error_msg = str(e)
        raise_http_error(
            500,
            "An internal error occurred while creating the VM.",
            f"VM creation failed: {e}"
        )


@router.post("/provision", response_model=VMResponse, status_code=status.HTTP_201_CREATED)
def provision_vm(
    vm_data: VMProvisionRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Provision a Proxmox VM with cloud-init configuration.
    
    The VM will be connected to the tenant's LAN bridge (vmbr{bridge_id}).
    Supports DHCP or static IP assignment.
    """
    check_rate_limit(request, endpoint="vm_create")
    
    # 1. Check permissions
    if not has_permission(current_user, current_tenant.id, "vm:create", db):
        raise_http_error(
            403,
            "You do not have permission to create VMs",
            f"User {current_user.id} denied VM provision - insufficient permissions"
        )
    
    # 2. Validate tenant has a network (OPNsense provisioned)
    if not current_tenant.pod_id:
        raise_http_error(
            400,
            "Tenant not provisioned - no pod assigned. Please wait for tenant approval.",
            f"Tenant {current_tenant.id} has no pod_id"
        )
    
    # 3. Resolve which network to use
    from app.models.network import TenantNetwork, Pod
    from app.providers import get_provider_for_pod
    
    if vm_data.network_id:
        network = db.query(TenantNetwork).filter_by(
            id=vm_data.network_id,
            tenant_id=current_tenant.id,
            status="active",
        ).first()
        if not network:
            raise_http_error(404, "Network not found or does not belong to this tenant")
    else:
        network = db.query(TenantNetwork).filter_by(
            tenant_id=current_tenant.id,
            is_default=True,
            status="active",
        ).first()
        if not network:
            raise_http_error(400, "Tenant has no default network — not fully provisioned")
    
    # Get provider and build NIC config
    pod = db.query(Pod).get(network.pod_id)
    provider = get_provider_for_pod(pod, db=db)
    vm_config = provider.attach_vm_to_network({}, network)
    
    # 4. Basic static IP validation (use network's CIDR)
    if vm_data.ip_mode == "static" and vm_data.ip_address:
        import ipaddress
        try:
            ip = ipaddress.ip_address(vm_data.ip_address)
            net = ipaddress.ip_network(network.cidr, strict=False)
            
            if ip not in net:
                raise_http_error(400, f"Static IP must be in {network.cidr} range")
            
            if str(ip) == network.gateway_ip:
                raise_http_error(400, f"Cannot use gateway IP ({network.gateway_ip})")
            
            if ip == net.network_address or ip == net.broadcast_address:
                raise_http_error(400, "Cannot use network or broadcast address")
        except ValueError as e:
            raise_http_error(400, f"Invalid IP address: {e}")
    
    # 4. Quota check
    try:
        check_vm_quota(
            tenant_id=current_tenant.id,
            db=db,
            cpu=vm_data.cpu,
            ram=vm_data.ram,
            disk_size=vm_data.disk_size_gb or 0
        )
    except QuotaExceededError as e:
        raise_http_error(
            400,
            f"Quota exceeded: {e.resource} limit of {e.limit} reached ({e.current} used, requested {e.requested} more)",
            f"Quota exceeded for tenant {current_tenant.id}: {e.resource}"
        )
    
    # 5. Acquire VM name lock
    if not acquire_vm_name_lock(db, vm_data.name):
        raise_http_error(
            409,
            "VM is currently being created. Please try a different name or wait.",
            f"VM name lock contention: {vm_data.name}"
        )
    
    # 6. Gateway from network (already resolved above)
    gateway = network.gateway_ip
    
    new_vm = None
    
    try:
        # 7. Get template disk size from Proxmox (use existing provider)
        template_disk_size = 20  # default fallback
        try:
            templates = provider.list_templates()
            for t in templates:
                if t.get("vmid") == vm_data.template_id:
                    template_disk_size = t.get("disk", 20)
                    break
            logger.info(f"Template {vm_data.template_id} disk size: {template_disk_size}GB")
        except Exception as e:
            logger.warning(f"Could not get template disk size, using default: {e}")
        
        # 8. Create DB record (status = provisioning) - don't set proxmox_vm_id yet
        new_vm = VM(
            name=vm_data.name,
            tenant_id=current_tenant.id,
            owner_id=current_user.id,
            provider="proxmox",
            template_id=vm_data.template_id,
            cpu=vm_data.cpu,
            ram=vm_data.ram,
            disk_size_mb=(vm_data.disk_size_gb or template_disk_size) * 1024,
            status="provisioning",
            ip_address=vm_data.ip_address if vm_data.ip_mode == "static" else None,
            description=vm_data.description,
        )
        db.add(new_vm)
        db.commit()
        db.refresh(new_vm)
        
        # 9. Start async Celery task (returns immediately)
        provision_data = {
            "template_id": vm_data.template_id,
            "name": vm_data.name,
            "vm_config": vm_config,
            "username": vm_data.username,
            "password": vm_data.password,
            "ssh_public_key": vm_data.ssh_public_key,
            "ip_mode": vm_data.ip_mode,
            "ip_address": vm_data.ip_address,
            "gateway": gateway,
            "dns_nameservers": vm_data.dns_nameservers,
            "dns_search": vm_data.dns_search,
            "cpu": vm_data.cpu,
            "ram": vm_data.ram,
            "auto_start": vm_data.auto_start,
            "disk_size_gb": vm_data.disk_size_gb,
            "template_disk_size": template_disk_size,
            "skip_cloudinit": vm_data.skip_cloudinit,
        }
        
        provision_vm_task.delay(vm_id=new_vm.id, provision_data=provision_data)
        
        from app.core.audit import log_audit_event, AUDIT_ACTIONS
        from app.api.auth import get_client_ip
        request_id = request.headers.get("X-Request-ID", "unknown")
        client_ip = get_client_ip(request)
        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["VM_CREATE"],
            target_type="vm",
            actor_id=current_user.id,
            actor_username=current_user.username,
            target_id=new_vm.id,
            target_name=new_vm.name,
            new_value=f"provider=proxmox,template_id={vm_data.template_id},cpu={vm_data.cpu},ram={vm_data.ram},disk_size_gb={vm_data.disk_size_gb or template_disk_size}",
            request_id=request_id,
            ip_address=client_ip,
            tenant_id=current_tenant.id if current_tenant else None
        )
        
        logger.info(f"VM provisioning task started for VM {new_vm.id} by user {current_user.id}")
        return new_vm
        
    except IntegrityError as e:
        db.rollback()
        logger.warning(f"VM provision race condition detected: {e}")
        raise_http_error(
            409,
            "VM name already exists. Please choose a different name.",
            f"Duplicate VM name: {vm_data.name}"
        )
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to provision VM {vm_data.name}: {e}", exc_info=True)
        
        if new_vm and new_vm.id:
            try:
                new_vm.status = "error"
                new_vm.error = str(e)[:500]
                db.commit()
            except Exception:
                pass
        
        raise_http_error(
            500,
            "An internal error occurred while provisioning the VM.",
            f"VM provision failed: {e}"
        )


def get_visible_vms_query(db: Session, current_user: User, tenant_id: int) -> Query:
    """
    Returns a query for VMs visible to the current user in the current tenant.
    Tenant admins can see all VMs in the tenant.
    Regular users can only see their own VMs.
    """
    from app.core.iam import has_permission, is_super_admin
    
    # Check if super admin (has super_admin IAM role)
    if is_super_admin(current_user, db):
        # Super admins can see all VMs across all tenants
        return db.query(VM)
    
    # Check if user has management permissions in this tenant
    if has_permission(current_user, tenant_id, "vm:delete", db) or has_permission(current_user, tenant_id, "vm:update", db):
        return db.query(VM).filter(VM.tenant_id == tenant_id)
    
    # Regular users can only see their own VMs in their tenant
    return db.query(VM).filter(VM.tenant_id == tenant_id, VM.owner_id == current_user.id)


@router.get("/list", response_model=VMListResponse)
def list_vms(
    status_filter: Optional[str] = Query(None),
    provider_filter: Optional[str] = Query(None),
    owner_filter: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    List VMs with optional filtering and pagination.
    Admin/vm_admin users can see all VMs in their tenant.
    vm_operator and viewer users can only see their own VMs.
    """
    query = get_visible_vms_query(db, current_user, current_tenant.id)
    
    if status_filter:
        query = query.filter(VM.status == status_filter)
    if provider_filter:
        query = query.filter(VM.provider == provider_filter)
    if owner_filter:
        from app.core.iam import has_permission, is_super_admin
        if is_super_admin(current_user, db) or has_permission(current_user, current_tenant.id, "vm:delete", db):
            query = query.filter(VM.owner_id == owner_filter)
        else:
            raise_http_error(403, "You can only filter by owner_id if you have admin or vm_admin role")
    
    total = query.count()
    vms = query.order_by(VM.created_at.desc()).offset(offset).limit(limit).all()
    
    return VMListResponse(total=total, vms=vms, offset=offset, limit=limit)


@router.get("/stats/summary", response_model=VMStatsResponse)
def get_vm_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Get VM statistics summary.
    Requires: vm_admin or admin role.
    """
    from app.core.iam import has_permission, is_super_admin
    if not is_super_admin(current_user, db) and not has_permission(current_user, current_tenant.id, "vm:delete", db):
        raise_http_error(
            403,
            "You do not have permission to view VM statistics",
            f"User {current_user.id} denied stats access - insufficient permissions"
        )
    
    total = db.query(func.count(VM.id)).filter(VM.tenant_id == current_tenant.id).scalar() or 0
    running = db.query(func.count(VM.id)).filter(VM.tenant_id == current_tenant.id, VM.status == "running").scalar() or 0
    stopped = db.query(func.count(VM.id)).filter(VM.tenant_id == current_tenant.id, VM.status == "stopped").scalar() or 0
    error = db.query(func.count(VM.id)).filter(VM.tenant_id == current_tenant.id, VM.status == "error").scalar() or 0
    pending = db.query(func.count(VM.id)).filter(VM.tenant_id == current_tenant.id, VM.status == "pending").scalar() or 0
    
    docker_count = db.query(func.count(VM.id)).filter(VM.tenant_id == current_tenant.id, VM.provider == "docker").scalar() or 0
    vsphere_count = db.query(func.count(VM.id)).filter(VM.tenant_id == current_tenant.id, VM.provider == "vsphere").scalar() or 0
    
    return VMStatsResponse(
        total_vms=total,
        status_breakdown={
            "running": running,
            "stopped": stopped,
            "error": error,
            "pending": pending
        },
        provider_breakdown={
            "docker": docker_count,
            "vsphere": vsphere_count
        },
        cpu_total=db.query(func.sum(VM.cpu)).scalar() or 0,
        ram_total_mb=db.query(func.sum(VM.ram)).scalar() or 0,
        disk_total_gb=round((db.query(func.sum(VM.disk_size_mb)).scalar() or 0) / 1024, 1)
    )


@router.get("/{vm_id}", response_model=VMResponse)
def get_vm(
    vm_id: int, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Get VM details.
    Admin/vm_admin can view all VMs in tenant.
    vm_operator/viewer can only view their own VMs.
    Super admins can view VMs across all tenants (read-only, no console/logs).
    """
    from app.core.iam import has_permission, is_super_admin
    
    # Super admin can access any VM across all tenants
    if is_super_admin(current_user, db):
        vm = db.query(VM).filter(VM.id == vm_id).first()
        if not vm:
            raise_http_error(404, "VM not found", f"VM {vm_id} not found")
        return vm
    
    # Regular access control
    vm = db.query(VM).filter(VM.id == vm_id, VM.tenant_id == current_tenant.id).first()
    if not vm:
        raise_http_error(404, "VM not found", f"VM {vm_id} not found")
    
    if not (has_permission(current_user, current_tenant.id, "vm:update", db) or vm.owner_id == current_user.id):
        raise_http_error(
            403,
            "You can only view your own VMs unless you have vm_admin or admin role",
            f"User {current_user.id} denied access to VM {vm_id} owned by {vm.owner_id}"
        )
    
    return vm


@router.patch("/{vm_id}", response_model=VMResponse)
def update_vm(
    vm_id: int,
    vm_update: VMUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Update VM metadata.
    Requires: Owner, vm_admin, or admin
    """
    vm = db.query(VM).filter(VM.id == vm_id, VM.tenant_id == current_tenant.id).first()
    if not vm:
        raise_http_error(404, "VM not found", f"VM {vm_id} not found for update")
    
    from app.core.iam import has_permission
    
    if not (has_permission(current_user, current_tenant.id, "vm:update", db) or vm.owner_id == current_user.id):
        raise_http_error(
            403,
            "You can only modify your own VMs unless you have vm_admin or admin role",
            f"User {current_user.id} denied update of VM {vm_id} owned by {vm.owner_id}"
        )
    
    update_data = vm_update.model_dump(exclude_unset=True)
    old_values = {field: getattr(vm, field) for field in update_data.keys()}
    for field, value in update_data.items():
        setattr(vm, field, value)
    
    db.commit()
    db.refresh(vm)
    
    from app.core.audit import log_audit_event, AUDIT_ACTIONS
    from app.api.auth import get_client_ip
    request_id = request.headers.get("X-Request-ID", "unknown") if request else "unknown"
    client_ip = get_client_ip(request) if request else None
    log_audit_event(
        db=db,
        action="vm_update",
        target_type="vm",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=vm_id,
        target_name=vm.name,
        old_value=str(old_values),
        new_value=str(update_data),
        request_id=request_id,
        ip_address=client_ip,
        tenant_id=current_tenant.id if current_tenant else None
    )
    
    logger.info(f"VM {vm_id} updated by user {current_user.id}")
    return vm



@router.post("/{vm_id}/start", response_model=VMResponse)
def start_vm(
    vm_id: int,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Start a VM.
    Requires: Owner, vm_admin, or admin
    """
    vm = db.query(VM).filter(VM.id == vm_id, VM.tenant_id == current_tenant.id).first()
    if not vm:
        raise_http_error(404, "VM not found", f"VM {vm_id} not found for start")
    
    from app.core.iam import has_permission, is_super_admin
    
    if not (has_permission(current_user, current_tenant.id, "vm:update", db) or vm.owner_id == current_user.id):
        raise_http_error(
            403,
            "You can only start your own VMs unless you have vm_admin or admin role",
            f"User {current_user.id} denied start of VM {vm_id}"
        )
    
    try:
        vm_service.start_vm_logic(db, vm_id)
        
        from app.core.audit import log_audit_event, AUDIT_ACTIONS
        request_id = request.headers.get("X-Request-ID", "unknown") if request else "unknown"
        client_ip = get_client_ip(request)
        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["VM_START"],
            target_type="vm",
            actor_id=current_user.id,
            actor_username=current_user.username,
            target_id=vm_id,
            target_name=vm.name,
            request_id=request_id,
            ip_address=client_ip,
            tenant_id=current_tenant.id if current_tenant else None
        )
        
        logger.info(f"VM {vm_id} started by user {current_user.id}")
        return db.query(VM).filter(VM.id == vm_id).first()
    except Exception as e:
        handle_service_exception(e, vm_id)


@router.post("/{vm_id}/stop", response_model=VMResponse)
def stop_vm(
    vm_id: int,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Stop a VM.
    Requires: Owner, vm_admin, or admin
    """
    vm = db.query(VM).filter(VM.id == vm_id, VM.tenant_id == current_tenant.id).first()
    if not vm:
        raise_http_error(404, "VM not found", f"VM {vm_id} not found for stop")
    
    from app.core.iam import has_permission, is_super_admin
    
    if not (has_permission(current_user, current_tenant.id, "vm:update", db) or vm.owner_id == current_user.id):
        raise_http_error(
            403,
            "You can only stop your own VMs unless you have vm_admin or admin role",
            f"User {current_user.id} denied stop of VM {vm_id}"
        )
    
    try:
        vm_service.stop_vm_logic(db, vm_id)
        
        from app.core.audit import log_audit_event, AUDIT_ACTIONS
        request_id = request.headers.get("X-Request-ID", "unknown") if request else "unknown"
        client_ip = get_client_ip(request)
        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["VM_STOP"],
            target_type="vm",
            actor_id=current_user.id,
            actor_username=current_user.username,
            target_id=vm_id,
            target_name=vm.name,
            request_id=request_id,
            ip_address=client_ip,
            tenant_id=current_tenant.id if current_tenant else None
        )
        
        logger.info(f"VM {vm_id} stopped by user {current_user.id}")
        return db.query(VM).filter(VM.id == vm_id).first()
    except Exception as e:
        handle_service_exception(e, vm_id)


@router.post("/{vm_id}/restart", response_model=VMResponse)
def restart_vm(
    vm_id: int,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Restart a VM.
    Requires: Owner, vm_admin, or admin
    """
    vm = db.query(VM).filter(VM.id == vm_id, VM.tenant_id == current_tenant.id).first()
    if not vm:
        raise_http_error(404, "VM not found", f"VM {vm_id} not found for restart")
    
    from app.core.iam import has_permission, is_super_admin
    
    if not (has_permission(current_user, current_tenant.id, "vm:update", db) or vm.owner_id == current_user.id):
        raise_http_error(
            403,
            "You can only restart your own VMs unless you have vm_admin or admin role",
            f"User {current_user.id} denied restart of VM {vm_id}"
        )
    
    try:
        vm_service.restart_vm_logic(db, vm_id)
        
        from app.core.audit import log_audit_event, AUDIT_ACTIONS
        request_id = request.headers.get("X-Request-ID", "unknown") if request else "unknown"
        client_ip = get_client_ip(request)
        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["VM_RESTART"],
            target_type="vm",
            actor_id=current_user.id,
            actor_username=current_user.username,
            target_id=vm_id,
            target_name=vm.name,
            request_id=request_id,
            ip_address=client_ip,
            tenant_id=current_tenant.id if current_tenant else None
        )
        
        logger.info(f"VM {vm_id} restarted by user {current_user.id}")
        return db.query(VM).filter(VM.id == vm_id).first()
    except Exception as e:
        handle_service_exception(e, vm_id)


@router.delete("/{vm_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vm(
    vm_id: int,
    force: bool = Query(False),
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Delete a VM.
    Requires: Owner, vm_admin, or admin
    """
    vm = db.query(VM).filter(VM.id == vm_id, VM.tenant_id == current_tenant.id).first()
    if not vm:
        raise_http_error(404, "VM not found", f"VM {vm_id} not found for deletion")
    
    from app.core.iam import has_permission, is_super_admin
    
    if not (has_permission(current_user, current_tenant.id, "vm:update", db) or vm.owner_id == current_user.id):
        raise_http_error(
            403,
            "You can only delete your own VMs unless you have vm_admin or admin role",
            f"User {current_user.id} denied deletion of VM {vm_id}"
        )
    
    try:
        vm_name = vm.name if vm else f"vm_{vm_id}"
        vm_service.delete_vm_logic(db, vm_id, force)
        
        from app.core.audit import log_audit_event, AUDIT_ACTIONS
        request_id = request.headers.get("X-Request-ID", "unknown") if request else "unknown"
        client_ip = get_client_ip(request)
        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["VM_DELETE"],
            target_type="vm",
            actor_id=current_user.id,
            actor_username=current_user.username,
            target_id=vm_id,
            target_name=vm_name,
            details=f"force={force}",
            request_id=request_id,
            ip_address=client_ip,
            tenant_id=current_tenant.id if current_tenant else None
        )
        
        logger.info(f"VM {vm_id} deleted by user {current_user.id} (force={force})")
    except Exception as e:
        handle_service_exception(e, vm_id)
    return None


@router.get("/{vm_id}/logs", response_model=VMLogsResponse)
def get_vm_logs(
    vm_id: int,
    tail: int = Query(100, ge=1, le=10000),
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Get VM logs.
    Requires: Owner, vm_admin, or admin (logs may contain sensitive data)
    Super admins are BLOCKED from accessing logs to protect tenant privacy.
    Rate limited to prevent resource exhaustion.
    """
    from app.core.iam import has_permission, is_super_admin
    
    # BLOCK super_admin from accessing logs (privacy protection)
    if is_super_admin(current_user, db) and not request.headers.get("X-Impersonate-Tenant-ID"):
        raise_http_error(
            403,
            "Super admins cannot access VM logs. This is to protect tenant privacy.",
            f"Super admin {current_user.id} denied logs access for VM {vm_id}"
        )
    
    if request:
        check_rate_limit(request, endpoint="vm_logs")
    
    vm = db.query(VM).filter(VM.id == vm_id, VM.tenant_id == current_tenant.id).first()
    if not vm:
        raise_http_error(404, "VM not found", f"VM {vm_id} not found for logs")
    
    if not (has_permission(current_user, current_tenant.id, "vm:update", db) or vm.owner_id == current_user.id):
        raise_http_error(
            403,
            "You can only view logs for your own VMs unless you have vm_admin or admin role",
            f"User {current_user.id} denied logs access for VM {vm_id}"
        )
    
    try:
        return vm_service.get_vm_logs_logic(db, vm_id, tail)
    except Exception as e:
        handle_service_exception(e, vm_id)


@router.post("/{vm_id}/console")
async def get_console_access(
    vm_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Get console access for a VM via WebSocket proxy.
    Only available to tenants (not super admins) when VM is running.
    """
    from app.core.iam import has_permission, is_super_admin
    
    if is_super_admin(current_user, db) and not request.headers.get("X-Impersonate-Tenant-ID"):
        raise_http_error(
            403,
            "Console access is not available for super admins",
            f"Super admin {current_user.id} denied console access for VM {vm_id}"
        )
    
    vm = db.query(VM).filter(VM.id == vm_id, VM.tenant_id == current_tenant.id).first()
    if not vm:
        raise_http_error(404, "VM not found", f"VM {vm_id} not found")
    
    if vm.status != "running":
        raise_http_error(
            400,
            "VM must be running to access console",
            f"VM {vm_id} is not running (status: {vm.status})"
        )
    
    if not (has_permission(current_user, current_tenant.id, "vm:console", db) or vm.owner_id == current_user.id):
        raise_http_error(
            403,
            "You do not have permission to access console for this VM",
            f"User {current_user.id} denied console access for VM {vm_id}"
        )
    
    try:
        from app.providers import get_hypervisor_provider
        from app.core.cache import get_active_console_session, remove_active_console_session
        provider = get_hypervisor_provider()
        
        existing_session = get_active_console_session(vm_id)
        if existing_session:
            try:
                old_upid = existing_session.get("upid", "")
                old_node = existing_session.get("node", "")
                if old_upid and old_node:
                    provider.stop_console_session(old_node, old_upid)
                    logger.info(f"Cleaned up existing console session for VM {vm_id}")
            except Exception as cleanup_err:
                logger.warning(f"Failed to clean up existing session: {cleanup_err}")
            finally:
                remove_active_console_session(vm_id)
        
        if vm.proxmox_vm_id is None:
            raise_http_error(
                400,
                "VM has no Proxmox VM ID — provisioning may have failed or is incomplete. "
                "Please delete and re-provision the VM.",
                f"VM {vm_id} has no proxmox_vm_id — console access denied"
            )
        
        console_type = "serial"
        vnc_info = None
        
        try:
            vnc_info = provider.get_serial_console(vm.proxmox_vm_id)
        except Exception as serial_err:
            logger.warning(f"Serial console not available for VM {vm_id}: {serial_err}")
            try:
                vnc_info = provider.get_vnc_proxy(vm.proxmox_vm_id)
                console_type = "vnc"
            except Exception as vnc_err:
                logger.error(f"VNC console also not available: {vnc_err}")
                raise_http_error(500, "No console available for this VM")
        
        if not vnc_info:
            raise_http_error(500, "Failed to get console info")
        
        import secrets
        ws_token = secrets.token_urlsafe(32)
        
        from app.core.cache import set_console_token, set_active_console_session
        set_console_token(ws_token, {
            "vm_id": vm_id,
            "proxmox_vm_id": vm.proxmox_vm_id,
            "user_id": current_user.id,
            "vnc_info": vnc_info,
            "console_type": vnc_info.get("console_type", "vnc"),
        })
        
        set_active_console_session(vm_id, {
            "upid": vnc_info.get("upid", ""),
            "node": vnc_info.get("node", ""),
            "proxmox_vm_id": vm.proxmox_vm_id,
            "user_id": current_user.id,
            "console_type": vnc_info.get("console_type", "vnc"),
        })
        
        from app.core.audit import log_audit_event, AUDIT_ACTIONS
        from app.api.auth import get_client_ip
        request_id = request.headers.get("X-Request-ID", "unknown") if request else "unknown"
        client_ip = get_client_ip(request) if request else None
        log_audit_event(
            db=db,
            action="vm_console_access",
            target_type="vm",
            actor_id=current_user.id,
            actor_username=current_user.username,
            target_id=vm_id,
            target_name=vm.name,
            new_value=f"console_type={console_type},vnc_proxy={vnc_info.get('console_type', 'vnc')}",
            request_id=request_id,
            ip_address=client_ip,
            tenant_id=current_tenant.id if current_tenant else None
        )
        
        return {
            "websocket_url": f"/v1/vm/ws/console/{ws_token}",
            "vm_id": vm_id,
            "vnc_password": vnc_info["ticket"],
            "console_type": vnc_info.get("console_type", "vnc"),
            "desktop_name": vnc_info.get("desktop_name", "VNC Desktop")
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get console access for VM {vm_id}: {e}")
        raise_http_error(500, f"Failed to get console access: {str(e)}")


@router.delete("/{vm_id}/console")
async def disconnect_console(
    vm_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Disconnect active console session for a VM.
    """
    from app.core.iam import has_permission, is_super_admin
    
    if is_super_admin(current_user, db) and not request.headers.get("X-Impersonate-Tenant-ID"):
        raise_http_error(
            403,
            "Console disconnect is not available for super admins",
            f"Super admin {current_user.id} attempted to disconnect console for VM {vm_id}"
        )
    
    vm = db.query(VM).filter(VM.id == vm_id, VM.tenant_id == current_tenant.id).first()
    if not vm:
        raise_http_error(404, "VM not found", f"VM {vm_id} not found")
    
    if not (has_permission(current_user, current_tenant.id, "vm:console", db) or vm.owner_id == current_user.id):
        raise_http_error(
            403,
            "You do not have permission to disconnect console for this VM",
            f"User {current_user.id} denied console disconnect for VM {vm_id}"
        )
    
    from app.core.cache import remove_active_console_session
    session = remove_active_console_session(vm_id)
    
    if not session:
        return {"status": "no_active_session", "message": "No active console session to disconnect"}
    
    try:
        from app.providers import get_hypervisor_provider
        provider = get_hypervisor_provider()
        
        upid = session.get("upid", "")
        node = session.get("node", "")
        
        if upid and node:
            success = provider.stop_console_session(node, upid)
            if success:
                logger.info(f"Console session for VM {vm_id} disconnected successfully")
                return {"status": "disconnected", "message": "Console session terminated"}
            else:
                logger.warning(f"Failed to disconnect console session for VM {vm_id}")
                return {"status": "disconnect_failed", "message": "Failed to terminate console session"}
        else:
            logger.warning(f"No UPID or node info for VM {vm_id} console session")
            return {"status": "no_session_info", "message": "No session information available"}
            
    except Exception as e:
        logger.error(f"Error disconnecting console for VM {vm_id}: {e}")
        raise_http_error(500, f"Failed to disconnect console: {str(e)}")


import asyncio
import websockets
import urllib.parse

async def proxy_vnc_websocket(websocket, vnc_info):
    """Proxy VNC traffic between tenant WebSocket and Proxmox VNC WebSocket."""
    encoded_ticket = urllib.parse.quote(vnc_info["ticket"], safe='')
    
    proxmox_ws_url = (
        f"wss://{vnc_info['host']}:8006/"
        f"api2/json/nodes/{vnc_info['node']}/qemu/{vnc_info['vmid']}/vncwebsocket"
        f"?port={vnc_info['port']}&vncticket={encoded_ticket}"
    )
    
    try:
        async with websockets.connect(
            proxmox_ws_url,
            ssl=False
        ) as proxmox_ws:
            async def tenant_to_proxmox():
                try:
                    while True:
                        data = await websocket.receive_bytes()
                        await proxmox_ws.send(data)
                except Exception:
                    pass
            
            async def proxmox_to_tenant():
                try:
                    while True:
                        data = await proxmox_ws.recv()
                        if isinstance(data, bytes):
                            await websocket.send_bytes(data)
                        else:
                            await websocket.send_text(data)
                except Exception:
                    pass
            
            await asyncio.gather(
                tenant_to_proxmox(),
                proxmox_to_tenant(),
                return_exceptions=True
            )
    except Exception as e:
        logger.error(f"Proxmox WebSocket proxy error: {e}")
        raise


@router.websocket("/ws/console/{token}")
async def console_websocket(websocket: WebSocket, token: str):
    await websocket.accept()
    
    from app.core.cache import pop_console_token
    token_data = pop_console_token(token)
    
    if not token_data:
        await websocket.close(code=4001, reason="Invalid or expired token")
        return
    
    vnc_info = token_data["vnc_info"]
    console_type = token_data.get("console_type", "vnc")
    
    import urllib.parse
    encoded_ticket = urllib.parse.quote(vnc_info["ticket"], safe='')
    
    if console_type == "serial":
        proxmox_ws_url = (
            f"wss://{vnc_info['host']}:8006/"
            f"api2/json/nodes/{vnc_info['node']}/qemu/{token_data['proxmox_vm_id']}/serialwebsocket"
            f"?port={vnc_info['port']}&vncticket={encoded_ticket}"
        )
    else:
        proxmox_ws_url = (
            f"wss://{vnc_info['host']}:8006/"
            f"api2/json/nodes/{vnc_info['node']}/qemu/{token_data['proxmox_vm_id']}/vncwebsocket"
            f"?port={vnc_info['port']}&vncticket={encoded_ticket}"
        )
    
    logger.info(f"[WS] Connecting to Proxmox ({console_type}): {proxmox_ws_url.replace(vnc_info['ticket'], '***')}")
    
    try:
        import ssl
        from app.core.config import settings
        
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        
        proxmox_auth = {
            "Authorization": f"PVEAPIToken={settings.PROXMOX_USERNAME}={settings.PROXMOX_TOKEN}"
        }
        
        async with websockets.connect(
            proxmox_ws_url,
            ssl=ssl_context,
            additional_headers=proxmox_auth,
        ) as proxmox_ws:
            logger.info("[WS] Connected to Proxmox VNC")
            
            async def tenant_to_proxmox():
                try:
                    while True:
                        try:
                            data = await websocket.receive_bytes()
                            logger.info(f"[WS] Tenant→Proxmox received {len(data)} bytes")
                            await proxmox_ws.send(data)
                        except Exception as inner_e:
                            logger.error(f"[WS] Error in tenant_to_proxmox inner loop: {inner_e}")
                            raise
                except Exception as e:
                    logger.error(f"[WS] Tenant→Proxmox error: {e}")
            
            async def proxmox_to_tenant():
                try:
                    while True:
                        try:
                            data = await proxmox_ws.recv()
                            logger.info(f"[WS] Proxmox→Tenant received {len(data) if isinstance(data, bytes) else len(str(data))} bytes")
                            
                            if isinstance(data, bytes):
                                logger.info(f"[WS] Sending {len(data)} bytes to browser")
                                await websocket.send_bytes(data)
                            elif isinstance(data, str):
                                logger.info(f"[WS] Sending {len(data)} chars text to browser")
                                await websocket.send_text(data)
                            else:
                                logger.warning(f"[WS] Unknown data type: {type(data)}")
                        except Exception as inner_e:
                            logger.error(f"[WS] Error in proxmox_to_tenant inner loop: {inner_e}")
                            raise
                except Exception as e:
                    logger.error(f"[WS] Proxmox→Tenant error: {e}")
            
            await asyncio.gather(
                tenant_to_proxmox(),
                proxmox_to_tenant(),
                return_exceptions=True,
            )
            
    except Exception as e:
        logger.error(f"[WS] Proxmox connection failed: {e}")
        await websocket.close(code=1011, reason=str(e))

@router.post("/{vm_id}/snapshots", response_model=VMSnapshotResponse, status_code=status.HTTP_201_CREATED)
def create_snapshot(
    vm_id: int,
    snapshot_data: VMSnapshotCreate,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Create a snapshot of a VM.
    The VM must be running to create a snapshot.
    """
    vm = db.query(VM).filter(VM.id == vm_id, VM.tenant_id == current_tenant.id).first()
    if not vm:
        raise_http_error(404, "VM not found", f"VM {vm_id} not found")
    
    from app.core.iam import has_permission
    
    if not (has_permission(current_user, current_tenant.id, "vm:update", db) or vm.owner_id == current_user.id):
        raise_http_error(
            403,
            "You can only snapshot your own VMs unless you have vm_admin or admin role",
            f"User {current_user.id} denied snapshot access for VM {vm_id}"
        )
    
    try:
        snapshot = vm_service.create_snapshot_logic(db, vm_id, snapshot_data, current_user.id)
        
        from app.core.audit import log_audit_event, AUDIT_ACTIONS
        request_id = request.headers.get("X-Request-ID", "unknown") if request else "unknown"
        client_ip = get_client_ip(request)
        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["VM_SNAPSHOT_CREATE"],
            target_type="vm_snapshot",
            actor_id=current_user.id,
            actor_username=current_user.username,
            target_id=snapshot.id,
            target_name=snapshot.name,
            new_value=f"vm_id={vm_id},vm_name={vm.name},image_tag={snapshot.image_tag}",
            request_id=request_id,
            ip_address=client_ip,
            tenant_id=current_tenant.id if current_tenant else None
        )
        
        return snapshot
    except Exception as e:
        handle_service_exception(e, vm_id)


@router.get("/{vm_id}/snapshots", response_model=list[VMSnapshotResponse])
def list_snapshots(
    vm_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    List all snapshots for a VM.
    """
    vm = db.query(VM).filter(VM.id == vm_id, VM.tenant_id == current_tenant.id).first()
    if not vm:
        raise_http_error(404, "VM not found", f"VM {vm_id} not found")
    
    from app.core.iam import has_permission, is_super_admin
    
    if not (is_super_admin(current_user, db) or has_permission(current_user, current_tenant.id, "vm:update", db) or vm.owner_id == current_user.id):
        raise_http_error(
            403,
            "You can only view snapshots for your own VMs unless you have vm_admin or admin role",
            f"User {current_user.id} denied snapshot access for VM {vm_id}"
        )
    
    try:
        return vm_service.list_snapshots_logic(db, vm_id)
    except Exception as e:
        handle_service_exception(e, vm_id)


@router.post("/{vm_id}/snapshots/{snapshot_id}/restore")
def restore_snapshot(
    vm_id: int,
    snapshot_id: int,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Restore a VM from a snapshot.
    The VM will be stopped, removed, and recreated from the snapshot.
    """
    vm = db.query(VM).filter(VM.id == vm_id, VM.tenant_id == current_tenant.id).first()
    if not vm:
        raise_http_error(404, "VM not found", f"VM {vm_id} not found")
    
    from app.core.iam import has_permission
    
    if not (has_permission(current_user, current_tenant.id, "vm:update", db) or vm.owner_id == current_user.id):
        raise_http_error(
            403,
            "You can only restore your own VMs unless you have vm_admin or admin role",
            f"User {current_user.id} denied restore access for VM {vm_id}"
        )
    
    try:
        from app.models.vm import VMSnapshot
        snapshot = db.query(VMSnapshot).filter(
            VMSnapshot.id == snapshot_id,
            VMSnapshot.vm_id == vm_id
        ).first()
        
        if not snapshot:
            raise_http_error(404, "Snapshot not found", f"Snapshot {snapshot_id} not found for VM {vm_id}")
        
        vm_service.restore_snapshot_logic(db, vm_id, snapshot_id)
        
        from app.core.audit import log_audit_event, AUDIT_ACTIONS
        request_id = request.headers.get("X-Request-ID", "unknown") if request else "unknown"
        client_ip = get_client_ip(request)
        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["VM_SNAPSHOT_RESTORE"],
            target_type="vm_snapshot",
            actor_id=current_user.id,
            actor_username=current_user.username,
            target_id=snapshot_id,
            target_name=snapshot.name if snapshot else None,
            old_value=f"vm_id={vm_id},vm_name={vm.name}",
            new_value=f"snapshot={snapshot.image_tag if snapshot else 'unknown'}",
            request_id=request_id,
            ip_address=client_ip,
            tenant_id=current_tenant.id if current_tenant else None
        )
        
        return {"message": "Snapshot restored successfully"}
    except Exception as e:
        handle_service_exception(e, vm_id)


@router.delete("/{vm_id}/snapshots/{snapshot_id}")
def delete_snapshot(
    vm_id: int,
    snapshot_id: int,
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
    current_tenant: Tenant = Depends(get_current_tenant)
):
    """
    Delete a snapshot.
    """
    vm = db.query(VM).filter(VM.id == vm_id, VM.tenant_id == current_tenant.id).first()
    if not vm:
        raise_http_error(404, "VM not found", f"VM {vm_id} not found")
    
    from app.core.iam import has_permission
    
    if not (has_permission(current_user, current_tenant.id, "vm:update", db) or vm.owner_id == current_user.id):
        raise_http_error(
            403,
            "You can only delete your own snapshots unless you have vm_admin or admin role",
            f"User {current_user.id} denied delete snapshot access for VM {vm_id}"
        )
    
    try:
        from app.models.vm import VMSnapshot
        snapshot = db.query(VMSnapshot).filter(VMSnapshot.id == snapshot_id).first()
        
        vm_service.delete_snapshot_logic(db, vm_id, snapshot_id)
        
        from app.core.audit import log_audit_event, AUDIT_ACTIONS
        request_id = request.headers.get("X-Request-ID", "unknown") if request else "unknown"
        client_ip = get_client_ip(request)
        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["VM_SNAPSHOT_DELETE"],
            target_type="vm_snapshot",
            actor_id=current_user.id,
            actor_username=current_user.username,
            target_id=snapshot_id,
            target_name=snapshot.name if snapshot else None,
            old_value=f"vm_id={vm_id},vm_name={vm.name},image_tag={snapshot.image_tag if snapshot else 'unknown'}",
            request_id=request_id,
            ip_address=client_ip,
            tenant_id=current_tenant.id if current_tenant else None
        )
        
        return {"message": "Snapshot deleted successfully"}
    except Exception as e:
        handle_service_exception(e, vm_id)


@router.post("/{vm_id}/resize-disk", response_model=DiskResizeResponse)
def resize_vm_disk(
    vm_id: int,
    request_body: DiskResizeRequest,
    request: Request,
    current_user: User = Depends(get_user),
    db: Session = Depends(get_db),
):
    """
    Resize a VM disk.
    
    - Only accepts relative sizes (+XG format)
    - Works on both running and stopped VMs
    - Updates tenant disk quota
    - Logs to audit trail
    """
    request_id = request.headers.get("X-Request-ID", "unknown")
    client_ip = get_client_ip(request)
    
    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        raise_http_error(404, f"VM {vm_id} not found")
    
    if vm.tenant_id != current_user.tenant_id:
        raise_http_error(403, "You don't have access to this VM")
    
    provider = get_hypervisor_provider()
    
    if not has_permission(current_user, vm.tenant_id, "vm:update", db):
        raise_http_error(403, "You don't have permission to resize disk")
    
    if vm.provider == "proxmox":
        if vm.proxmox_vm_id is None:
            raise_http_error(400, f"VM {vm_id} has no Proxmox VM ID configured")
        proxmox_id = vm.proxmox_vm_id
    else:
        proxmox_id = vm_id
    
    vm_status = provider.get_vm_status(proxmox_id)
    if vm_status.get("lock"):
        raise_http_error(423, f"VM is locked: {vm_status['lock']}. Try again later.")
    
    disk_info_list = provider.get_vm_disk_info(proxmox_id)
    disk_info = next((d for d in disk_info_list if d['id'] == request_body.disk), None)
    if not disk_info:
        raise_http_error(400, f"Disk {request_body.disk} not found on VM {vm_id}")
    
    requested_mib = provider._parse_size_to_mib(request_body.size)
    
    if requested_mib <= 0:
        raise_http_error(400, "Size must be greater than 0")
    
    check_disk_resize_quota(vm.tenant_id, requested_mib, db)
    
    recent_resize = db.query(VMDiskResize).filter(
        VMDiskResize.vm_id == vm_id,
        VMDiskResize.disk_id == request_body.disk,
        VMDiskResize.created_at > datetime.utcnow() - timedelta(minutes=2)
    ).order_by(VMDiskResize.created_at.desc()).first()
    
    if recent_resize:
        target_size_mib = recent_resize.previous_size_mib + requested_mib
        if recent_resize.new_size_mib == target_size_mib:
            return DiskResizeResponse(
                disk_id=request_body.disk,
                previous_size_mib=recent_resize.previous_size_mib,
                new_size_mib=recent_resize.new_size_mib,
                previous_size_gb=round(recent_resize.previous_size_mib / 1024, 1),
                new_size_gb=round(recent_resize.new_size_mib / 1024, 1),
                status="already_resized",
                restarted=False
            )
    
    previous_size_mib = disk_info['size_mib']
    
    provider.resize_disk(proxmox_id, request_body.disk, request_body.size)
    
    # Calculate expected new size (more reliable than reading config after resize)
    expected_new_size_mib = previous_size_mib + requested_mib
    
    new_disk_info = provider.get_vm_disk_info(proxmox_id)
    new_disk = next((d for d in new_disk_info if d['id'] == request_body.disk), None)
    # Use the actual size from Proxmox if available, otherwise use calculated expected size
    new_size_mib = new_disk.get('size_mib') if new_disk and new_disk.get('size_mib') else expected_new_size_mib
    
    # Update VM disk size in database
    vm.disk_size_mb = new_size_mib
    db.add(VMDiskResize(
        vm_id=vm_id,
        disk_id=request_body.disk,
        previous_size_mib=previous_size_mib,
        new_size_mib=new_size_mib,
        resized_by=current_user.id
    ))
    
    try:
        db.commit()
        logger.info(f"Disk resize successful - VM {vm_id}: disk {request_body.disk} {previous_size_mib}M -> {new_size_mib}M")
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to commit disk resize for VM {vm_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to save disk resize: {str(e)}")
    
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["VM_DISK_RESIZE"],
        target_type="vm",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=vm_id,
        target_name=vm.name,
        old_value=f"disk={request_body.disk},size={previous_size_mib}M",
        new_value=f"disk={request_body.disk},size={new_size_mib}M",
        details=f"Resized disk {request_body.disk} by {request_body.size}",
        request_id=request_id,
        ip_address=client_ip,
        tenant_id=vm.tenant_id
    )
    
    restarted = False
    if request_body.restart_after_resize:
        try:
            vm_service.restart_vm_logic(db, vm.id)
            restarted = True
            
            # Fetch actual disk size from Proxmox after restart
            post_restart_disk_info = provider.get_vm_disk_info(proxmox_id)
            post_restart_disk = next((d for d in post_restart_disk_info if d['id'] == request_body.disk), None)
            if post_restart_disk and post_restart_disk.get('size_mib'):
                actual_size_mib = post_restart_disk['size_mib']
                # Always update database with actual Proxmox disk size after restart
                vm.disk_size_mb = actual_size_mib
                db.add(VMDiskResize(
                    vm_id=vm_id,
                    disk_id=request_body.disk,
                    previous_size_mib=new_size_mib,
                    new_size_mib=actual_size_mib,
                    resized_by=current_user.id
                ))
                db.commit()
                new_size_mib = actual_size_mib
                logger.info(f"Updated disk size after restart to: {actual_size_mib}M")
            
            logger.info(f"VM {vm_id} restarted after disk resize")
        except Exception as e:
            logger.warning(f"Failed to restart VM {vm_id} after resize: {e}")
    
    return DiskResizeResponse(
        disk_id=request_body.disk,
        previous_size_mib=previous_size_mib,
        new_size_mib=new_size_mib,
        previous_size_gb=round(previous_size_mib / 1024, 1),
        new_size_gb=round(new_size_mib / 1024, 1),
        status="resized",
        restarted=restarted
    )


@router.get("/{vm_id}/resources", response_model=VMResourcesResponse)
def get_vm_resources(
    vm_id: int,
    current_user: User = Depends(get_user),
    db: Session = Depends(get_db),
):
    """
    Get VM resource configuration (CPU, RAM, Disk).
    """
    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        raise_http_error(404, f"VM {vm_id} not found")
    
    if vm.tenant_id != current_user.tenant_id:
        raise_http_error(403, "You don't have access to this VM")
    
    provider = get_hypervisor_provider()
    
    if vm.provider == "proxmox":
        if vm.proxmox_vm_id is None:
            raise_http_error(400, f"VM {vm_id} has no Proxmox VM ID configured")
        proxmox_id = vm.proxmox_vm_id
    else:
        proxmox_id = vm_id
    
    resources = provider.get_vm_resources(proxmox_id)
    
    return VMResourcesResponse(
        cpu_cores=resources.get("cpu_cores", 1),
        memory_mb=resources.get("memory_mb", 1024),
        memory_gb=round(resources.get("memory_mb", 1024) / 1024, 1),
        disks=resources.get("disks", {}),
        digest=resources.get("digest"),
        name=resources.get("name"),
        status=resources.get("status", "running")
    )


@router.post("/{vm_id}/resize-cpu", response_model=ResourceResizeResponse)
def resize_vm_cpu(
    vm_id: int,
    request_body: CPUResizeRequest,
    request: Request,
    current_user: User = Depends(get_user),
    db: Session = Depends(get_db),
):
    """
    Resize VM CPU cores.
    
    - Requires VM restart to take effect
    - Only works on Proxmox VMs
    """
    request_id = request.headers.get("X-Request-ID", "unknown")
    client_ip = get_client_ip(request)
    
    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        raise_http_error(404, f"VM {vm_id} not found")
    
    if vm.tenant_id != current_user.tenant_id:
        raise_http_error(403, "You don't have access to this VM")
    
    if not has_permission(current_user, vm.tenant_id, "vm:update", db):
        raise_http_error(403, "You don't have permission to resize CPU")
    
    provider = get_hypervisor_provider()
    
    if vm.provider == "proxmox":
        if vm.proxmox_vm_id is None:
            raise_http_error(400, f"VM {vm_id} has no Proxmox VM ID configured")
        proxmox_id = vm.proxmox_vm_id
    else:
        raise_http_error(400, "CPU resize only supported for Proxmox VMs")
    
    # Get current resources to know previous value
    current_resources = provider.get_vm_resources(proxmox_id)
    previous_cores = current_resources.get("cpu_cores", 1)
    
    # Update CPU
    provider.update_vm_resources(proxmox_id, cpu_cores=request_body.cores)
    
    # Update VM record
    vm.cpu = request_body.cores
    db.commit()
    
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["VM_UPDATE"],
        target_type="vm",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=vm_id,
        target_name=vm.name,
        new_value=f"cpu_cores={request_body.cores}",
        request_id=request_id,
        ip_address=client_ip,
        tenant_id=vm.tenant_id
    )
    
    restarted = False
    if request_body.restart_after_resize:
        try:
            vm_service.restart_vm_logic(db, vm.id)
            restarted = True
            logger.info(f"VM {vm_id} restarted after CPU resize")
        except Exception as e:
            logger.warning(f"Failed to restart VM {vm_id} after CPU resize: {e}")
    
    return ResourceResizeResponse(
        resource_type="cpu",
        previous_value=previous_cores,
        new_value=request_body.cores,
        status="resized",
        restarted=restarted
    )


@router.post("/{vm_id}/resize-ram", response_model=ResourceResizeResponse)
def resize_vm_ram(
    vm_id: int,
    request_body: RAMResizeRequest,
    request: Request,
    current_user: User = Depends(get_user),
    db: Session = Depends(get_db),
):
    """
    Resize VM RAM.
    
    - Requires VM restart to take effect
    - Only works on Proxmox VMs
    """
    request_id = request.headers.get("X-Request-ID", "unknown")
    client_ip = get_client_ip(request)
    
    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        raise_http_error(404, f"VM {vm_id} not found")
    
    if vm.tenant_id != current_user.tenant_id:
        raise_http_error(403, "You don't have access to this VM")
    
    if not has_permission(current_user, vm.tenant_id, "vm:update", db):
        raise_http_error(403, "You don't have permission to resize RAM")
    
    provider = get_hypervisor_provider()
    
    if vm.provider == "proxmox":
        if vm.proxmox_vm_id is None:
            raise_http_error(400, f"VM {vm_id} has no Proxmox VM ID configured")
        proxmox_id = vm.proxmox_vm_id
    else:
        raise_http_error(400, "RAM resize only supported for Proxmox VMs")
    
    # Get current resources to know previous value
    current_resources = provider.get_vm_resources(proxmox_id)
    previous_memory = current_resources.get("memory_mb", 1024)
    
    # Update RAM
    provider.update_vm_resources(proxmox_id, memory_mb=request_body.memory_mb)
    
    # Update VM record
    vm.ram = request_body.memory_mb
    db.commit()
    
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["VM_UPDATE"],
        target_type="vm",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=vm_id,
        target_name=vm.name,
        new_value=f"memory_mb={request_body.memory_mb}",
        request_id=request_id,
        ip_address=client_ip,
        tenant_id=vm.tenant_id
    )
    
    restarted = False
    if request_body.restart_after_resize:
        try:
            vm_service.restart_vm_logic(db, vm.id)
            restarted = True
            logger.info(f"VM {vm_id} restarted after RAM resize")
        except Exception as e:
            logger.warning(f"Failed to restart VM {vm_id} after RAM resize: {e}")
    
    return ResourceResizeResponse(
        resource_type="memory",
        previous_value=previous_memory,
        new_value=request_body.memory_mb,
        status="resized",
        restarted=restarted
    )


@router.get("/{vm_id}/disk-info", response_model=list[VMDiskInfo])
def get_vm_disk_info(
    vm_id: int,
    current_user: User = Depends(get_user),
    db: Session = Depends(get_db),
):
    """Get current disk configuration for a VM."""
    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        raise_http_error(404, f"VM {vm_id} not found")
    
    if vm.tenant_id != current_user.tenant_id:
        raise_http_error(403, "You don't have access to this VM")
    
    provider = get_hypervisor_provider()
    
    if vm.provider == "proxmox" and vm.proxmox_vm_id is None:
        raise_http_error(400, f"VM {vm_id} has no Proxmox VM ID configured")
    
    proxmox_id = vm.proxmox_vm_id if vm.provider == "proxmox" else vm_id
    disk_list = provider.get_vm_disk_info(proxmox_id)
    
    return [
        VMDiskInfo(
            id=d['id'],
            storage=d['storage'],
            volume=d['volume'],
            size_mib=d['size_mib'],
            size_gb=d['size_gb']
        )
        for d in disk_list
    ]


@router.get("/storage-info", response_model=dict[str, StorageInfoResponse])
def get_storage_info(
    current_user: User = Depends(get_user),
    db: Session = Depends(get_db),
):
    """Get available storage info for the current pod."""
    provider = get_hypervisor_provider()
    storage_data = provider.get_storage_info()
    
    return {
        name: StorageInfoResponse(
            total_gb=info['total_gb'],
            free_gb=info['free_gb'],
            used_gb=info['used_gb'],
            content=info.get('content', '')
        )
        for name, info in storage_data.items()
    }


@router.get("/{vm_id}/ssh-info", response_model=SshInfoResponse)
def get_ssh_info(
    vm_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
    current_tenant: Tenant = Depends(get_current_tenant),
):
    """Get SSH connection info for a VM."""
    from app.core.iam import has_permission
    vm = db.query(VM).filter(VM.id == vm_id, VM.tenant_id == current_tenant.id).first()
    if not vm:
        raise_http_error(404, "VM not found", f"VM {vm_id} not found")
    if not has_permission(current_user, current_tenant.id, "vm:read", db):
        raise_http_error(403, "Not authorized")

    ssh_user = vm.ssh_user or "ubuntu"
    ip = vm.ip_address
    ssh_command = f"ssh {ssh_user}@{ip} -i {vm.name}.pem" if ip else None

    return SshInfoResponse(
        vm_id=vm.id,
        vm_name=vm.name,
        ssh_user=ssh_user,
        ip_address=ip,
        ssh_public_key=vm.ssh_public_key,
        ssh_command=ssh_command,
        has_private_key=bool(vm.ssh_private_key_enc),
    )


@router.get("/{vm_id}/ssh-key")
def download_ssh_key(
    vm_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
    current_tenant: Tenant = Depends(get_current_tenant),
):
    """Download the SSH private key for a VM as a .pem file."""
    from fastapi.responses import Response
    from app.core.iam import has_permission
    from app.core.crypto import decrypt

    vm = db.query(VM).filter(VM.id == vm_id, VM.tenant_id == current_tenant.id).first()
    if not vm:
        raise_http_error(404, "VM not found", f"VM {vm_id} not found")
    if not has_permission(current_user, current_tenant.id, "vm:read", db):
        raise_http_error(403, "Not authorized")
    if not vm.ssh_private_key_enc:
        raise_http_error(404, "No SSH key", "This VM has no stored SSH private key")

    private_key = decrypt(vm.ssh_private_key_enc)
    return Response(
        content=private_key,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{vm.name}.pem"'},
    )


@router.post("/{vm_id}/ssh-key/regenerate", response_model=SshKeyRegenerateResponse)
def regenerate_ssh_key(
    vm_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
    current_tenant: Tenant = Depends(get_current_tenant),
):
    """Regenerate the SSH key pair for a VM. Returns the new private key (one-time download)."""
    from app.core.iam import has_permission
    from app.core.crypto import encrypt
    from app.core.ssh import generate_ssh_keypair

    vm = db.query(VM).filter(VM.id == vm_id, VM.tenant_id == current_tenant.id).first()
    if not vm:
        raise_http_error(404, "VM not found", f"VM {vm_id} not found")
    if not has_permission(current_user, current_tenant.id, "vm:create", db):
        raise_http_error(403, "Not authorized")

    ssh_user = vm.ssh_user or "ubuntu"
    public_key, private_pem = generate_ssh_keypair(comment=f"{vm.name}@proxmox-iaas-platform")
    vm.ssh_public_key = public_key
    vm.ssh_private_key_enc = encrypt(private_pem)
    db.commit()

    if vm.proxmox_vm_id and vm.ip_address:
        try:
            provider = get_hypervisor_provider()
            authorized_keys_line = public_key.replace("'", "'\\''")
            provider.exec_in_vm(
                vm.proxmox_vm_id,
                f"home=~{ssh_user} && "
                f"mkdir -p $home/.ssh && "
                f"echo '{authorized_keys_line}' > $home/.ssh/authorized_keys && "
                f"chmod 700 $home/.ssh && "
                f"chmod 600 $home/.ssh/authorized_keys && "
                f"chown -R {ssh_user}:{ssh_user} $home/.ssh",
                timeout=60,
            )
        except Exception as e:
            logger.error(f"Failed to deploy SSH key to VM {vm_id} via guest agent: {e}")
            raise_http_error(500, "SSH key generated but failed to deploy to VM. "
                             "The VM may still have the old key. "
                             f"Error: {e}")

    ip = vm.ip_address
    ssh_command = f"ssh {ssh_user}@{ip} -i {vm.name}.pem" if ip else None

    return SshKeyRegenerateResponse(
        vm_id=vm.id,
        ssh_user=ssh_user,
        ssh_public_key=public_key,
        ssh_private_key=private_pem,
        ssh_command=ssh_command or "",
    )
