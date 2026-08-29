from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime
import logging
import json
import ipaddress

from app.core.database import get_db
from app.core.dependencies import get_current_user
from app.core.iam import is_super_admin
from app.models.user import User
from app.models.tenant import Tenant, TenantStatus
from app.models.network import TenantNetwork
from app.models.iam import UserRole
from app.schemas.tenant import (
    QuotaSettings,
    TenantVerifyRequest,
    QuotaUpdate,
    TenantQuotaResponse
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tenants", tags=["Tenants"])


class TenantResponse(BaseModel):
    id: int
    name: str
    slug: str
    is_active: bool
    is_verified: bool = False
    status: str = "pending"
    settings: Optional[str] = None
    created_at: Optional[datetime] = None
    user_count: int = 0
    vm_count: int = 0
    network_count: int = 0
    error: Optional[str] = None  # Error message from last provisioning attempt

    model_config = {"from_attributes": True}


class TenantWithStats(TenantResponse):
    """Extended tenant response with user, VM, and network counts."""
    vm_status_breakdown: dict = {}  # running, stopped, error counts
    total_cpu: int = 0  # Total CPU cores across all VMs
    total_ram: int = 0  # Total RAM in MB across all VMs
    total_disk: float = 0.0  # Total disk in GB across all VMs
    vm_provider_breakdown: dict = {}  # Count by provider (proxmox, vsphere)
    # OPNsense provisioning info
    bridge_id: Optional[int] = None
    opnsense_vm_id: Optional[int] = None
    opnsense_vm_name: Optional[str] = None
    lan_ip: Optional[str] = None
    wan_ip: Optional[str] = None


class TenantUpdate(BaseModel):
    name: Optional[str] = None
    is_active: Optional[bool] = None
    settings: Optional[str] = None


class TenantUserResponse(BaseModel):
    id: int
    username: str
    email: str
    full_name: Optional[str] = None
    is_active: bool
    created_at: Optional[datetime] = None
    roles: list[str] = []

    model_config = {"from_attributes": True}


@router.get("/my-tenants", response_model=List[TenantResponse])
def get_my_tenants(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get all tenants the current user has access to.
    Returns primary tenant + any tenants via UserRole.
    """
    # Get primary tenant
    tenants = []
    if current_user.tenant_id:
        tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
        if tenant:
            tenants.append(tenant)
    
    # Get additional tenants via UserRole
    user_roles = db.query(UserRole).filter(UserRole.user_id == current_user.id).all()
    tenant_ids = [ur.tenant_id for ur in user_roles]
    
    additional_tenants = db.query(Tenant).filter(
        Tenant.id.in_(tenant_ids),
        Tenant.id != current_user.tenant_id
    ).all()
    
    tenants.extend(additional_tenants)
    
    return tenants


@router.get("", response_model=List[TenantWithStats])
def list_tenants(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    List all tenants with user and VM counts. Super admin only.
    """
    if not is_super_admin(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required"
        )
    
    tenants = db.query(Tenant).all()
    
    # Add user, VM, and network counts to each tenant
    from app.models.user import User
    from app.models.vm import VM
    from app.models.network import TenantNetwork
    
    result = []
    for tenant in tenants:
        user_count = db.query(User).filter(User.tenant_id == tenant.id).count()
        vm_count = db.query(VM).filter(VM.tenant_id == tenant.id).count()
        network_count = db.query(TenantNetwork).filter(
            TenantNetwork.tenant_id == tenant.id,
            TenantNetwork.status == "active"
        ).count()
        
        # VM status breakdown for misuse detection
        vm_status_breakdown = {}
        for status in ['running', 'stopped', 'error', 'pending']:
            count = db.query(VM).filter(
                VM.tenant_id == tenant.id,
                VM.status == status
            ).count()
            if count > 0:
                vm_status_breakdown[status] = count
        
        result.append(TenantWithStats(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            is_active=tenant.is_active,
            is_verified=tenant.is_verified,
            status=tenant.status,
            settings=tenant.settings,
            created_at=tenant.created_at,
            user_count=user_count,
            vm_count=vm_count,
            network_count=network_count,
            vm_status_breakdown=vm_status_breakdown,
            bridge_id=tenant.bridge_id,
            opnsense_vm_id=tenant.opnsense_vm_id,
            opnsense_vm_name=tenant.opnsense_vm_name,
            lan_ip=tenant.lan_ip,
            wan_ip=tenant.wan_ip,
            error=tenant.error,
        ))
    
    return result


@router.post("", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
def create_tenant(
    tenant_data: dict,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create a new tenant. Super admin only.
    """
    if not is_super_admin(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required"
        )
    
    name = tenant_data.get("name")
    slug = tenant_data.get("slug", name.lower().replace(" ", "-"))
    
    existing = db.query(Tenant).filter(
        (Tenant.slug == slug) | (Tenant.name == name)
    ).first()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant with this name or slug already exists"
        )
    
    tenant = Tenant(
        name=name,
        slug=slug,
        is_active=True,
        settings="{}"
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    
    from app.core.audit import log_audit_event, AUDIT_ACTIONS
    from app.api.auth import get_client_ip
    request_id = request.headers.get("X-Request-ID", "unknown")
    client_ip = get_client_ip(request)
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["TENANT_CREATE"],
        target_type="tenant",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=tenant.id,
        target_name=tenant.name,
        new_value=f"slug={slug}",
        request_id=request_id,
        ip_address=client_ip,
        tenant_id=None
    )
    
    logger.info(f"Tenant created: {tenant.name} by user {current_user.username}")
    return tenant


@router.get("/{tenant_id}/users", response_model=List[TenantUserResponse])
def get_tenant_users(
    tenant_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get all users in a tenant. Super admin only.
    """
    if not is_super_admin(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required"
        )
    
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found"
        )
    
    from app.models.iam import UserRole, Role
    
    users = db.query(User).filter(User.tenant_id == tenant_id).all()
    
    result = []
    for user in users:
        user_roles = db.query(UserRole).filter(UserRole.user_id == user.id).all()
        role_names = []
        for ur in user_roles:
            role = db.query(Role).filter(Role.id == ur.role_id).first()
            if role:
                role_names.append(role.name)
        
        result.append({
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "full_name": user.full_name,
            "is_active": user.is_active,
            "created_at": user.created_at,
            "roles": role_names
        })
    
    return result


class AllUsersResponse(BaseModel):
    total: int
    users: List[dict]


class TenantMembership(BaseModel):
    tenant_id: int
    tenant_name: str
    role_name: Optional[str] = None


@router.get("/users", response_model=AllUsersResponse)
def list_all_users(
    request: Request,
    skip: int = 0,
    limit: int = 20,
    search: Optional[str] = None,
    tenant_id: Optional[int] = None,
    is_active: Optional[bool] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    List all users across all tenants. Super admin only.
    Supports pagination and filtering.
    """
    if not is_super_admin(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required"
        )
    
    from app.models.iam import Role
    
    query = db.query(User)
    
    if search:
        search_filter = f"%{search}%"
        query = query.filter(
            (User.username.ilike(search_filter)) |
            (User.email.ilike(search_filter)) |
            (User.full_name.ilike(search_filter))
        )
    
    if tenant_id is not None:
        query = query.filter(User.tenant_id == tenant_id)
    
    if is_active is not None:
        query = query.filter(User.is_active == is_active)
    
    total = query.count()
    users = query.order_by(User.id).offset(skip).limit(limit).all()
    
    result = []
    for user in users:
        user_roles = db.query(UserRole).filter(UserRole.user_id == user.id).all()
        
        tenant_memberships = []
        role_names = []
        for ur in user_roles:
            role = db.query(Role).filter(Role.id == ur.role_id).first()
            if role:
                role_names.append(role.name)
                tenant = db.query(Tenant).filter(Tenant.id == ur.tenant_id).first()
                if tenant:
                    tenant_memberships.append({
                        "tenant_id": tenant.id,
                        "tenant_name": tenant.name,
                        "role_name": role.name
                    })
        
        primary_tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first() if user.tenant_id else None
        
        result.append({
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "full_name": user.full_name,
            "is_active": user.is_active,
            "created_at": user.created_at,
            "tenant_id": user.tenant_id,
            "tenant_name": primary_tenant.name if primary_tenant else None,
            "roles": role_names,
            "tenant_memberships": tenant_memberships,
            "is_super_admin": is_super_admin(user, db)
        })
    
    return {"total": total, "users": result}


@router.patch("/users/{user_id}/ban")
def ban_user(
    user_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Ban or unban a user. Super admin only.
    """
    if not is_super_admin(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required"
        )
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    if is_super_admin(user, db):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot ban a super admin"
        )
    
    user.is_active = not user.is_active
    db.commit()
    db.refresh(user)

    from app.core.audit import log_audit_event, AUDIT_ACTIONS
    from app.api.auth import get_client_ip
    request_id = request.headers.get("X-Request-ID", "unknown")
    client_ip = get_client_ip(request)
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["USER_STATUS_CHANGE"],
        target_type="user",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=user.id,
        target_name=user.username,
        old_value=f"is_active={not user.is_active}",
        new_value=f"is_active={user.is_active}",
        details=f"User {'banned' if not user.is_active else 'unbanned'} by super admin",
        request_id=request_id,
        ip_address=client_ip,
        tenant_id=user.tenant_id
    )
    
    return {
        "id": user.id,
        "username": user.username,
        "is_active": user.is_active,
        "message": f"User {'unbanned' if user.is_active else 'banned'} successfully"
    }


class TenantVMResponse(BaseModel):
    id: int
    name: str
    status: str
    ip_address: Optional[str] = None
    cpu: int
    ram: int
    disk_size_gb: Optional[float] = None
    disk_size_mb: Optional[int] = None

    model_config = {"from_attributes": True}

    @model_validator(mode='after')
    def compute_disk_size_gb(self):
        if self.disk_size_gb is None and self.disk_size_mb:
            self.disk_size_gb = round(self.disk_size_mb / 1024, 1)
        return self


@router.get("/{tenant_id}/vms", response_model=List[TenantVMResponse])
def get_tenant_vms(
    tenant_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get all VMs in a tenant (read-only). Super admin only.
    Returns basic VM info without sensitive data.
    """
    if not is_super_admin(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required"
        )
    
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found"
        )
    
    from app.models.vm import VM
    vms = db.query(VM).filter(VM.tenant_id == tenant_id).all()
    return vms


@router.get("/{tenant_id}/networks", response_model=List[dict])
def get_tenant_networks(
    tenant_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get all networks in a tenant (read-only). Super admin only.
    """
    if not is_super_admin(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required"
        )
    
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found"
        )
    
    from app.models.network import TenantNetwork, GlobalIPPool, Pod
    from app.models.vm import VM
    
    networks = db.query(TenantNetwork).filter(
        TenantNetwork.tenant_id == tenant_id,
        TenantNetwork.status == "active"
    ).all()
    
    # Get VMs for each network
    result = []
    for network in networks:
        vms = db.query(VM).filter(VM.network_id == network.id).all()
        
        # Get IP pool info
        ip_pool = None
        ips_used = 0
        ips_available = 0
        if network.ip_pool_id:
            ip_pool = db.query(GlobalIPPool).filter(GlobalIPPool.id == network.ip_pool_id).first()
            if ip_pool:
                try:
                    network_obj = ipaddress.ip_network(ip_pool.cidr, strict=False)
                    total_ips = network_obj.num_addresses - 2  # Exclude network and broadcast
                except:
                    total_ips = 254
                ips_used = db.query(VM).filter(VM.network_id == network.id).count()
                ips_available = max(0, total_ips - ips_used)
        
        pod = db.query(Pod).filter(Pod.id == network.pod_id).first()
        
        result.append({
            "id": network.id,
            "name": network.name,
            "cidr": network.cidr,
            "gateway": network.gateway_ip,
            "status": network.status,
            "vlan_id": network.vlan_id,
            "is_default": network.is_default,
            "pod_id": network.pod_id,
            "pod_name": pod.name if pod else None,
            "ips_used": ips_used,
            "ips_available": ips_available,
            "created_at": network.created_at,
            "vms": [
                {
                    "id": vm.id,
                    "name": vm.name,
                    "status": vm.status,
                    "ip_address": vm.ip_address
                }
                for vm in vms
            ]
        })
    
    return result


@router.get("/{tenant_id}", response_model=TenantWithStats)
def get_tenant(
    tenant_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get tenant details with user and VM counts. Super admin or tenant member.
    """
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found"
        )
    
    if not is_super_admin(current_user, db) and current_user.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    from app.models.user import User as UserModel
    from app.models.vm import VM
    from app.models.network import TenantNetwork
    from sqlalchemy import func
    
    user_count = db.query(UserModel).filter(UserModel.tenant_id == tenant.id).count()
    vm_count = db.query(VM).filter(VM.tenant_id == tenant.id).count()
    network_count = db.query(TenantNetwork).filter(
        TenantNetwork.tenant_id == tenant.id,
        TenantNetwork.status == "active"
    ).count()
    
    # VM status breakdown for misuse detection
    vm_status_breakdown = {}
    for status in ['running', 'stopped', 'error', 'pending']:
        count = db.query(VM).filter(
            VM.tenant_id == tenant.id,
            VM.status == status
        ).count()
        if count > 0:
            vm_status_breakdown[status] = count
    
    # Resource totals
    total_cpu = db.query(func.sum(VM.cpu)).filter(VM.tenant_id == tenant.id).scalar() or 0
    total_ram = db.query(func.sum(VM.ram)).filter(VM.tenant_id == tenant.id).scalar() or 0
    total_disk = round((db.query(func.sum(VM.disk_size_mb)).filter(VM.tenant_id == tenant.id).scalar() or 0) / 1024, 1)
    
    # VM provider breakdown
    vm_provider_breakdown = {}
    for provider in ['proxmox', 'vsphere', 'aws', 'azure', 'gcp']:
        count = db.query(VM).filter(
            VM.tenant_id == tenant.id,
            VM.provider == provider
        ).count()
        if count > 0:
            vm_provider_breakdown[provider] = count
    
    return TenantWithStats(
        id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        is_active=tenant.is_active,
        settings=tenant.settings,
        created_at=tenant.created_at,
        user_count=user_count,
        vm_count=vm_count,
        network_count=network_count,
        vm_status_breakdown=vm_status_breakdown,
        total_cpu=total_cpu,
        total_ram=total_ram,
        total_disk=total_disk,
        vm_provider_breakdown=vm_provider_breakdown
    )


@router.patch("/{tenant_id}", response_model=TenantResponse)
def update_tenant(
    tenant_id: int,
    tenant_data: TenantUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Update tenant. Super admin only.
    """
    if not is_super_admin(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required"
        )
    
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found"
        )
    
    if tenant_data.name is not None:
        tenant.name = tenant_data.name
    if tenant_data.is_active is not None:
        tenant.is_active = tenant_data.is_active
    if tenant_data.settings is not None:
        tenant.settings = tenant_data.settings
    
    db.commit()
    db.refresh(tenant)
    
    from app.core.audit import log_audit_event, AUDIT_ACTIONS
    from app.api.auth import get_client_ip
    request_id = request.headers.get("X-Request-ID", "unknown")
    client_ip = get_client_ip(request)
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["TENANT_UPDATE"],
        target_type="tenant",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=tenant.id,
        target_name=tenant.name,
        new_value=f"name={tenant.name},is_active={tenant.is_active}",
        request_id=request_id,
        ip_address=client_ip,
        tenant_id=None
    )
    
    return tenant


@router.delete("/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tenant(
    tenant_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Delete a tenant. Super admin only.
    Cannot delete a tenant that has users. Users must be reassigned first.
    """
    if not is_super_admin(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required"
        )
    
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found"
        )
    
    # Check if there are users in this tenant
    from app.models.user import User as UserModel
    user_count = db.query(UserModel).filter(UserModel.tenant_id == tenant_id).count()
    
    if user_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete tenant with {user_count} user(s). Please reassign or delete users first."
        )
    
    # Check if there are VMs in this tenant
    from app.models.vm import VM
    vm_count = db.query(VM).filter(VM.tenant_id == tenant_id).count()
    
    if vm_count > 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot delete tenant with {vm_count} VM(s). Please delete or reassign VMs first."
        )
    
    db.delete(tenant)
    db.commit()
    
    from app.core.audit import log_audit_event, AUDIT_ACTIONS
    from app.api.auth import get_client_ip
    request_id = request.headers.get("X-Request-ID", "unknown")
    client_ip = get_client_ip(request)
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["TENANT_DELETE"],
        target_type="tenant",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=tenant_id,
        target_name=tenant.name,
        old_value=f"name={tenant.name},slug={tenant.slug}",
        request_id=request_id,
        ip_address=client_ip,
        tenant_id=None
    )
    
    return None


@router.get("/unverified", response_model=List[TenantResponse])
def get_unverified_tenants(
    request: Request,
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get all unverified tenants. Super admin only.
    """
    if not is_super_admin(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required"
        )
    
    from app.models.vm import VM
    from app.models.network import TenantNetwork
    
    tenants = db.query(Tenant).filter(Tenant.is_verified == False).offset(skip).limit(limit).all()
    
    result = []
    for tenant in tenants:
        user_count = db.query(User).filter(User.tenant_id == tenant.id).count()
        vm_count = db.query(VM).filter(VM.tenant_id == tenant.id).count()
        network_count = db.query(TenantNetwork).filter(
            TenantNetwork.tenant_id == tenant.id,
            TenantNetwork.status == "active"
        ).count()
        
        result.append({
            "id": tenant.id,
            "name": tenant.name,
            "slug": tenant.slug,
            "is_active": tenant.is_active,
            "is_verified": tenant.is_verified,
            "settings": tenant.settings,
            "created_at": tenant.created_at,
            "user_count": user_count,
            "vm_count": vm_count,
            "network_count": network_count
        })
    
    return result


@router.post("/{tenant_id}/verify")
def verify_tenant(
    tenant_id: int,
    verify_data: TenantVerifyRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Verify a tenant and set their resource quota.
    Super admin only.
    """
    if not is_super_admin(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required"
        )
    
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found"
        )
    
    if tenant.is_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tenant is already verified"
        )
    
    request_id = request.headers.get("X-Request-ID", "unknown")
    
    # Allow retry for tenants in ERROR status - reset to PENDING_APPROVAL first
    if tenant.status == TenantStatus.ERROR:
        tenant.status = TenantStatus.PENDING_APPROVAL
        tenant.bridge_id = None
        tenant.opnsense_vm_id = None
        tenant.opnsense_vm_name = None
        tenant.lan_ip = None
        tenant.wan_ip = None
        tenant.opnsense_api_key = None
        tenant.opnsense_api_secret = None
        db.commit()
        logger.info(f"Reset tenant {tenant_id} from ERROR to PENDING_APPROVAL for retry")
    
    # Auto-provision OPNsense VM after verification
    from app.services.provisioning import approve_tenant
    provisioning_error = None
    provisioning_result = None
    try:
        provisioning_result = approve_tenant(db, tenant.id)
        tenant.is_verified = True
        tenant.settings = verify_data.quota.model_dump_json()
        logger.info(f"Tenant {tenant.name} (ID: {tenant_id}) verified and provisioning started by {current_user.username}, result: {provisioning_result}")
    except Exception as e:
        logger.error(f"Failed to start provisioning for tenant {tenant_id}: {e}")
        provisioning_error = str(e)
        tenant.status = TenantStatus.ERROR
        db.rollback()
    
    db.commit()
    db.refresh(tenant)
    logger.info(f"Tenant {tenant_id} status after commit: {tenant.status}, is_verified: {tenant.is_verified}")
    
    from app.core.audit import log_audit_event, AUDIT_ACTIONS
    from app.api.auth import get_client_ip
    client_ip = get_client_ip(request)
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS.get("TENANT_UPDATE", "tenant.update"),
        target_type="tenant",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=tenant_id,
        target_name=tenant.name,
        details=f"Tenant verified with quota: max_vms={verify_data.quota.max_vms}, max_cpu={verify_data.quota.max_cpu_cores}, max_ram={verify_data.quota.max_ram_mb}",
        request_id=request_id,
        ip_address=client_ip,
        tenant_id=None
    )
    
    logger.info(f"Tenant {tenant.name} (ID: {tenant_id}) verified by {current_user.username} (request_id={request_id})")
    
    # Use provisioning_result status if available, otherwise use tenant.status
    response_status = tenant.status
    if provisioning_result and "status" in provisioning_result:
        # Map result status to expected frontend values
        result_status = provisioning_result["status"]
        if result_status == "provisioning_started":
            response_status = "provisioning"
        elif result_status == "provisioning_complete":
            response_status = "active"
    
    logger.info(f"Returning status: {response_status} for tenant {tenant_id}")
    
    response_data = {
        "id": tenant.id,
        "name": tenant.name,
        "slug": tenant.slug,
        "is_active": tenant.is_active,
        "is_verified": tenant.is_verified,
        "status": response_status,
        "settings": tenant.settings,
        "created_at": tenant.created_at,
        "bridge_id": tenant.bridge_id,
        "opnsense_vm_id": tenant.opnsense_vm_id,
        "opnsense_vm_name": tenant.opnsense_vm_name,
        "lan_ip": tenant.lan_ip,
        "wan_ip": tenant.wan_ip,
        "wan_bridge": tenant.wan_bridge,
    }
    
    if provisioning_error:
        response_data["error"] = provisioning_error
    
    if provisioning_result and "opnsense_vm_id" in provisioning_result:
        response_data["opnsense_vm_id"] = provisioning_result["opnsense_vm_id"]
    
    return response_data


@router.get("/{tenant_id}/quota")
def get_tenant_quota(
    tenant_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get quota settings and current usage for a tenant.
    Super admin or tenant admin can view their own quota.
    """
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found"
        )
    
    if not is_super_admin(current_user, db) and current_user.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    from app.models.vm import VM
    from app.models.network import TenantNetwork
    
    quota = QuotaSettings.from_settings_json(tenant.settings)
    
    current_usage = {
        "vm_count": db.query(VM).filter(VM.tenant_id == tenant_id).count(),
        "cpu_cores": db.query(func.coalesce(func.sum(VM.cpu), 0)).filter(VM.tenant_id == tenant_id).scalar() or 0,
        "ram_mb": db.query(func.coalesce(func.sum(VM.ram), 0)).filter(VM.tenant_id == tenant_id).scalar() or 0,
        "disk_gb": round((db.query(func.coalesce(func.sum(VM.disk_size_mb), 0)).filter(VM.tenant_id == tenant_id).scalar() or 0) / 1024, 1),
        "network_count": db.query(TenantNetwork).filter(TenantNetwork.tenant_id == tenant_id).count()
    }
    
    return {
        "tenant_id": tenant_id,
        "quota": quota,
        "current_usage": current_usage
    }


@router.patch("/{tenant_id}/quota")
def update_tenant_quota(
    tenant_id: int,
    quota_update: QuotaUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Update quota settings for a tenant.
    Super admin only.
    """
    if not is_super_admin(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin access required"
        )
    
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found"
        )
    
    request_id = request.headers.get("X-Request-ID", "unknown")
    
    current_quota = QuotaSettings.from_settings_json(tenant.settings)
    
    update_data = quota_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(current_quota, key, value)
    
    tenant.settings = current_quota.model_dump_json()
    
    db.commit()
    db.refresh(tenant)
    
    from app.core.audit import log_audit_event, AUDIT_ACTIONS
    from app.api.auth import get_client_ip
    client_ip = get_client_ip(request)
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS.get("TENANT_UPDATE", "tenant.update"),
        target_type="tenant",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=tenant_id,
        target_name=tenant.name,
        details=f"Quota updated: {update_data}",
        request_id=request_id,
        ip_address=client_ip,
        tenant_id=None
    )
    
    logger.info(f"Tenant {tenant.name} (ID: {tenant_id}) quota updated by {current_user.username} (request_id={request_id})")
    
    return {
        "tenant_id": tenant_id,
        "quota": current_quota
    }


@router.get("/{tenant_id}/topology")
def get_tenant_topology(
    tenant_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get tenant network topology.
    Returns the full network architecture: WAN -> Firewall -> Networks -> VMs + VPN
    """
    from app.models.vm import VM
    from app.models.network import TenantNetwork
    from app.models.wireguard import WireGuardTunnel
    
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found"
        )
    
    if not is_super_admin(current_user, db) and current_user.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    vms = db.query(VM).filter(VM.tenant_id == tenant_id).all()
    tenant_networks = db.query(TenantNetwork).filter(TenantNetwork.tenant_id == tenant_id).all()
    
    def get_network_type(name: str) -> str:
        name_lower = (name or "").lower()
        if "dmz" in name_lower:
            return "dmz"
        elif "internal" in name_lower:
            return "internal"
        return "lan"
    
    networks = [
        {
            "id": str(net.id),
            "name": net.name,
            "cidr": net.cidr,
            "gateway": net.gateway_ip,
            "vlan_id": net.vlan_id,
            "dhcp_start": tenant.dhcp_pool_start,
            "dhcp_end": tenant.dhcp_pool_end,
            "type": get_network_type(net.name)
        }
        for net in tenant_networks
    ]
    
    if not networks and tenant.lan_ip:
        networks = [
            {
                "id": "lan",
                "name": "default",
                "cidr": f"{tenant.lan_ip.rsplit('.', 1)[0]}.0/24",
                "gateway": tenant.lan_ip,
                "vlan_id": None,
                "dhcp_start": tenant.dhcp_pool_start,
                "dhcp_end": tenant.dhcp_pool_end,
                "type": "lan"
            }
        ]
    
    running_count = sum(1 for v in vms if v.status == "running")
    stopped_count = sum(1 for v in vms if v.status == "stopped")
    error_count = sum(1 for v in vms if v.status == "error")

    wg_tunnels = db.query(WireGuardTunnel).filter(
        WireGuardTunnel.tenant_id == tenant_id,
        WireGuardTunnel.status.in_(["active", "provisioning"]),
    ).all()

    vpn = {
        "tunnels": [
            {
                "id": t.id,
                "name": t.name,
                "cidr": t.cidr,
                "gateway_ip": t.gateway_ip,
                "listen_port": t.listen_port,
                "status": t.status,
                "peer_count": len([p for p in t.peers if p.is_enabled]),
                "is_enabled": t.is_enabled,
                "peers": [
                    {"id": p.id, "name": p.name, "allowed_ip": p.allowed_ip, "is_enabled": p.is_enabled}
                    for p in t.peers if p.is_enabled
                ],
            }
            for t in wg_tunnels
        ],
        "total_tunnels": len(wg_tunnels),
    }

    return {
        "tenant_id": tenant.id,
        "tenant_name": tenant.name,
        "tenant_status": tenant.status,
        "wan": {
            "bridge": tenant.wan_bridge or "vmbr0",
            "ip": tenant.wan_ip
        },
        "firewall": {
            "name": tenant.opnsense_vm_name,
            "lan_ip": tenant.lan_ip,
            "wan_ip": tenant.wan_ip,
            "status": "running" if tenant.opnsense_vm_id else "not_provisioned"
        },
        "networks": networks,
        "lan": {
            "bridge": f"vmbr{tenant.bridge_id}" if tenant.bridge_id else None,
            "cidr": f"{tenant.lan_ip.rsplit('.', 1)[0]}.0/24" if tenant.lan_ip else None,
            "gateway": tenant.lan_ip,
            "vlan_id": networks[0]["vlan_id"] if networks else None,
            "dhcp_start": tenant.dhcp_pool_start,
            "dhcp_end": tenant.dhcp_pool_end
        },
        "vms": [
            {
                "id": vm.id,
                "name": vm.name,
                "ip_address": vm.ip_address,
                "status": vm.status,
                "provider": vm.provider,
                "cpu": vm.cpu,
                "ram": vm.ram,
                "network_id": str(vm.network_id) if vm.network_id else None
            }
            for vm in vms
        ],
        "stats": {
            "total": len(vms),
            "running": running_count,
            "stopped": stopped_count,
            "error": error_count
        },
        "vpn": vpn,
    }
