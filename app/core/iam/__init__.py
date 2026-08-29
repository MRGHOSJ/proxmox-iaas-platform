import logging
from typing import List, Optional
from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_user, get_current_tenant
from app.models.user import User
from app.models.tenant import Tenant
from app.models.iam import Permission, Role, UserRole

logger = logging.getLogger(__name__)

SUPER_ADMIN_TENANT_ID = None  # System-wide super_admin marker


def parse_permission(permission: str) -> tuple:
    parts = permission.split(":")
    if len(parts) >= 2:
        return parts[0], parts[1]
    return permission, ""


def get_user_permissions(user: User, tenant_id: int, db: Session) -> List[str]:
    # Check for wildcard first (could be granted via super_admin)
    if is_super_admin(user, db):
        return ["*"]
    
    user_roles = db.query(UserRole).filter(
        UserRole.user_id == user.id,
        UserRole.tenant_id == tenant_id
    ).all()
    
    permissions = set()
    for ur in user_roles:
        role = db.query(Role).filter(Role.id == ur.role_id).first()
        if role:
            for perm in role.permissions:
                permissions.add(perm.name)
    
    return list(permissions)


def has_permission(user: User, tenant_id: int, required_permission: str, db: Session) -> bool:
    user_permissions = get_user_permissions(user, tenant_id, db)
    
    if "*" in user_permissions:
        return True
    
    resource, action = parse_permission(required_permission)
    
    for perm in user_permissions:
        perm_resource, perm_action = parse_permission(perm)
        
        if perm_resource == resource:
            if perm_action == action:
                return True
            if perm_action == "*":
                return True
    
    return False


def has_any_permission(user: User, tenant_id: int, permissions: List[str], db: Session) -> bool:
    for perm in permissions:
        if has_permission(user, tenant_id, perm, db):
            return True
    return False


def has_all_permissions(user: User, tenant_id: int, permissions: List[str], db: Session) -> bool:
    for perm in permissions:
        if not has_permission(user, tenant_id, perm, db):
            return False
    return True


def is_tenant_admin(user: User, tenant_id: int, db: Session) -> bool:
    # Super admins are also tenant admins for any tenant
    if is_super_admin(user, db):
        return True
    
    user_roles = db.query(UserRole).filter(
        UserRole.user_id == user.id,
        UserRole.tenant_id == tenant_id
    ).all()
    
    for ur in user_roles:
        role = db.query(Role).filter(Role.id == ur.role_id).first()
        if role and role.name == "tenant_admin":
            return True
    
    return False


def is_super_admin(user: User, db: Session = None) -> bool:
    """
    Check if user has super_admin IAM role.
    Super admin has tenant_id=NULL to indicate system-wide access.
    """
    if db is None:
        return False
    
    user_roles = db.query(UserRole).filter(
        UserRole.user_id == user.id,
        UserRole.tenant_id.is_(None)
    ).all()
    
    for ur in user_roles:
        role = db.query(Role).filter(Role.id == ur.role_id).first()
        if role and role.is_system and role.name == "super_admin":
            return True
    
    return False


class RequirePermission:
    def __init__(self, permission: str, allow_super_admin: bool = True):
        self.permission = permission
        self.allow_super_admin = allow_super_admin
    
    def __call__(
        self, 
        current_user: User = Depends(get_current_user),
        current_tenant: Tenant = Depends(get_current_tenant),
        db: Session = Depends(get_db)
    ) -> User:
        if self.allow_super_admin and is_super_admin(current_user, db):
            return current_user
        
        if not has_permission(current_user, current_tenant.id, self.permission, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: {self.permission} required"
            )
        
        return current_user


class RequireAnyPermission:
    def __init__(self, permissions: List[str], allow_super_admin: bool = True):
        self.permissions = permissions
        self.allow_super_admin = allow_super_admin
    
    def __call__(
        self, 
        current_user: User = Depends(get_current_user),
        current_tenant: Tenant = Depends(get_current_tenant),
        db: Session = Depends(get_db)
    ) -> User:
        if self.allow_super_admin and is_super_admin(current_user, db):
            return current_user
        
        if not has_any_permission(current_user, current_tenant.id, self.permissions, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: one of {self.permissions} required"
            )
        
        return current_user


class RequireTenantAdmin:
    def __init__(self, allow_super_admin: bool = True):
        self.allow_super_admin = allow_super_admin
    
    def __call__(
        self, 
        current_user: User = Depends(get_current_user),
        current_tenant: Tenant = Depends(get_current_tenant),
        db: Session = Depends(get_db)
    ) -> User:
        if self.allow_super_admin and is_super_admin(current_user, db):
            return current_user
        
        if not is_tenant_admin(current_user, current_tenant.id, db):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant admin access required"
            )
        
        return current_user


require_vm_create = RequirePermission("vm:create")
require_vm_delete = RequirePermission("vm:delete")
require_vm_read = RequirePermission("vm:read")
require_network_create = RequirePermission("network:create")
require_network_delete = RequirePermission("network:delete")
require_firewall_create = RequirePermission("firewall:create")
require_tenant_admin = RequireTenantAdmin()
