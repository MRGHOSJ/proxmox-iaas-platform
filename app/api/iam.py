from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session, joinedload
import logging
import secrets
from datetime import datetime, timezone

from app.core.database import get_db
from app.core.dependencies import get_current_user, get_current_tenant
from app.core.iam import is_super_admin, is_tenant_admin
from app.models.user import User
from app.models.tenant import Tenant
from app.models.iam import Permission, Role, UserRole
from app.models.invitation import Invitation
from app.core.audit import log_audit_event, AUDIT_ACTIONS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/iam", tags=["IAM"])


# ===========================================
# Schemas
# ===========================================

class PermissionResponse(BaseModel):
    id: int
    name: str
    resource_type: str
    action: str
    description: Optional[str] = None

    model_config = {"from_attributes": True}


class RoleResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    is_preset: bool
    is_system: bool
    permissions: List[PermissionResponse] = []

    model_config = {"from_attributes": True}


class RoleCreate(BaseModel):
    name: str
    description: Optional[str] = None
    permission_ids: Optional[List[int]] = []


class RoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    permission_ids: Optional[List[int]] = None


class UserRoleResponse(BaseModel):
    id: int
    user_id: int
    tenant_id: int
    role_id: int
    granted_by: Optional[int] = None

    model_config = {"from_attributes": True}


class UserWithRole(BaseModel):
    id: int
    username: str
    email: str
    full_name: Optional[str] = None
    is_active: bool
    role_id: Optional[int] = None
    role_name: Optional[str] = None
    granted_by: Optional[int] = None


class UserWithRoles(BaseModel):
    id: int
    username: str
    email: str
    full_name: Optional[str] = None
    is_active: bool
    roles: List[str] = []


class InvitationResponse(BaseModel):
    id: int
    email: str
    tenant_id: int
    role_id: Optional[int] = None
    token: str
    invited_by: Optional[int] = None
    is_used: bool
    expires_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class PaginatedUsersResponse(BaseModel):
    users: List[UserWithRole]
    total: int


class PaginatedInvitationsResponse(BaseModel):
    invitations: List[InvitationResponse]
    total: int


class InvitationCreate(BaseModel):
    email: EmailStr
    role_id: Optional[int] = None


class InvitationAccept(BaseModel):
    token: str
    username: Optional[str] = None
    password: str
    full_name: Optional[str] = None


# ===========================================
# Global Endpoints
# ===========================================

@router.get("/permissions", response_model=List[PermissionResponse])
def list_permissions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    List all permissions in the system. All authenticated users can access this.
    """
    permissions = db.query(Permission).order_by(
        Permission.resource_type, Permission.action
    ).all()
    return permissions


# ===========================================
# Tenant-scoped IAM Endpoints
# ===========================================

@router.get("/tenants/{tenant_id}/roles", response_model=List[RoleResponse])
def list_tenant_roles(
    tenant_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    List roles in a tenant (both preset and custom).
    """
    if not is_super_admin(current_user, db) and current_user.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    roles = db.query(Role).options(joinedload(Role.permissions)).filter(
        (Role.tenant_id == tenant_id) | (Role.tenant_id == None)
    ).all()
    
    return roles


@router.post("/tenants/{tenant_id}/roles", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
def create_role(
    tenant_id: int,
    role_data: RoleCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create a custom role in a tenant. Tenant admin only.
    """
    if not is_super_admin(current_user, db) and current_user.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    if not is_tenant_admin(current_user, tenant_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant admin access required"
        )
    
    # Check if role name already exists
    existing = db.query(Role).filter(
        Role.name == role_data.name,
        Role.tenant_id == tenant_id
    ).first()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role with this name already exists"
        )
    
    # Create role
    new_role = Role(
        name=role_data.name,
        description=role_data.description,
        tenant_id=tenant_id,
        is_preset=False,
        is_system=False
    )
    db.add(new_role)
    db.flush()
    
    # Add permissions
    if role_data.permission_ids:
        permissions = db.query(Permission).filter(
            Permission.id.in_(role_data.permission_ids)
        ).all()
        new_role.permissions = permissions
    
    db.commit()
    db.refresh(new_role)
    
    from app.api.auth import get_client_ip
    request_id = request.headers.get("X-Request-ID", "unknown")
    client_ip = get_client_ip(request)
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["ROLE_CHANGE"],
        target_type="role",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=new_role.id,
        target_name=new_role.name,
        new_value=f"description={new_role.description}, permissions={[p.name for p in new_role.permissions]}",
        request_id=request_id,
        ip_address=client_ip,
        tenant_id=tenant_id
    )
    
    logger.info(f"Role {new_role.name} created in tenant {tenant_id} by user {current_user.username}")
    return new_role


@router.patch("/tenants/{tenant_id}/roles/{role_id}", response_model=RoleResponse)
def update_role(
    tenant_id: int,
    role_id: int,
    role_data: RoleUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Update a custom role. Tenant admin only.
    """
    if not is_tenant_admin(current_user, tenant_id, db) and not is_super_admin(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant admin access required"
        )
    
    role = db.query(Role).filter(Role.id == role_id).first()
    
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found"
        )
    
    if role.is_preset:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot modify preset roles"
        )
    
    if role_data.name is not None:
        role.name = role_data.name
    if role_data.description is not None:
        role.description = role_data.description
    
    if role_data.permission_ids is not None:
        permissions = db.query(Permission).filter(
            Permission.id.in_(role_data.permission_ids)
        ).all()
        role.permissions = permissions
    
    db.commit()
    db.refresh(role)
    
    from app.api.auth import get_client_ip
    request_id = request.headers.get("X-Request-ID", "unknown")
    client_ip = get_client_ip(request)
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["ROLE_CHANGE"],
        target_type="role",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=role.id,
        target_name=role.name,
        new_value=f"description={role.description}, permissions={[p.name for p in role.permissions]}",
        request_id=request_id,
        ip_address=client_ip,
        tenant_id=tenant_id
    )
    
    logger.info(f"Role {role.name} updated in tenant {tenant_id}")
    return role


@router.delete("/tenants/{tenant_id}/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(
    tenant_id: int,
    role_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Delete a custom role. Tenant admin only.
    """
    if not is_tenant_admin(current_user, tenant_id, db) and not is_super_admin(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant admin access required"
        )
    
    role = db.query(Role).filter(Role.id == role_id).first()
    
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found"
        )
    
    if role.is_preset:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete preset roles"
        )
    
    # Check if role is in use
    in_use = db.query(UserRole).filter(UserRole.role_id == role_id).first()
    if in_use:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete role that is assigned to users"
        )
    
    role_name = role.name
    role_description = role.description
    
    db.delete(role)
    db.commit()
    
    from app.api.auth import get_client_ip
    request_id = request.headers.get("X-Request-ID", "unknown")
    client_ip = get_client_ip(request)
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["ROLE_CHANGE"],
        target_type="role",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=role_id,
        target_name=role_name,
        old_value=f"name={role_name},description={role_description}",
        request_id=request_id,
        ip_address=client_ip,
        tenant_id=tenant_id
    )
    
    logger.info(f"Role {role_name} deleted from tenant {tenant_id}")
    return None


# ===========================================
# User Management
# ===========================================

@router.get("/tenants/{tenant_id}/users", response_model=PaginatedUsersResponse)
def list_tenant_users(
    tenant_id: int,
    request: Request,
    skip: int = 0,
    limit: int = 50,
    include_roles: bool = True,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    List users in a tenant with pagination. Tenant admin only.
    By default returns users with their roles.
    """
    if not is_super_admin(current_user, db) and current_user.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    if not is_tenant_admin(current_user, tenant_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only tenant admins can view users"
        )
    
    if include_roles:
        user_roles_query = db.query(UserRole, User, Role).join(
            User, UserRole.user_id == User.id
        ).join(
            Role, UserRole.role_id == Role.id
        ).filter(
            UserRole.tenant_id == tenant_id
        )
        
        total = user_roles_query.count()
        user_roles = user_roles_query.order_by(User.id).offset(skip).limit(limit).all()
        
        result = []
        for ur, user, role in user_roles:
            result.append(UserWithRole(
                id=user.id,
                username=user.username,
                email=user.email,
                full_name=user.full_name,
                is_active=user.is_active,
                role_id=role.id,
                role_name=role.name,
                granted_by=ur.granted_by
            ))
        
        return {"users": result, "total": total}
    else:
        primary_users = db.query(User).filter(User.tenant_id == tenant_id).all()
        
        user_ids_with_roles = db.query(UserRole.user_id).filter(
            UserRole.tenant_id == tenant_id
        ).distinct().all()
        user_ids_with_roles = [ur.user_id for ur in user_ids_with_roles]
        
        all_user_ids = set([u.id for u in primary_users] + user_ids_with_roles)
        total = len(all_user_ids)
        
        all_users_query = db.query(User).filter(User.id.in_(all_user_ids))
        all_users = all_users_query.order_by(User.id).offset(skip).limit(limit).all()
        
        result = []
        for user in all_users:
            result.append(UserWithRole(
                id=user.id,
                username=user.username,
                email=user.email,
                full_name=user.full_name,
                is_active=user.is_active,
                role_id=None,
                role_name=None,
                granted_by=None
            ))
        
        return {"users": result, "total": total}


@router.delete("/tenants/{tenant_id}/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tenant_user(
    tenant_id: int,
    user_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Remove a user from a tenant. Tenant admin only.
    """
    if not is_tenant_admin(current_user, tenant_id, db) and not is_super_admin(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant admin access required"
        )
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Remove user's roles in this tenant
    db.query(UserRole).filter(
        UserRole.user_id == user_id,
        UserRole.tenant_id == tenant_id
    ).delete()
    
    user_email = user.email
    user_username = user.username
    
    db.commit()
    
    from app.api.auth import get_client_ip
    request_id = request.headers.get("X-Request-ID", "unknown")
    client_ip = get_client_ip(request)
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["USER_DELETE"],
        target_type="user",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=user_id,
        target_name=user_username,
        old_value=f"email={user_email},tenant_id={tenant_id}",
        details=f"User removed from tenant {tenant_id}",
        request_id=request_id,
        ip_address=client_ip,
        tenant_id=tenant_id
    )
    
    logger.info(f"User {user_email} removed from tenant {tenant_id}")
    return None


# ===========================================
# User Roles
# ===========================================

@router.get("/tenants/{tenant_id}/users/roles", response_model=List[UserWithRoles])
def list_tenant_user_roles(
    tenant_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    List all users in a tenant with their roles. 
    """
    if not is_super_admin(current_user, db) and current_user.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found"
        )
    
    user_roles = db.query(UserRole, User, Role).join(
        User, UserRole.user_id == User.id
    ).join(
        Role, UserRole.role_id == Role.id
    ).filter(
        UserRole.tenant_id == tenant_id
    ).all()
    
    user_dict = {}
    for ur, user, role in user_roles:
        if user.id not in user_dict:
            user_dict[user.id] = {
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "full_name": user.full_name,
                "is_active": user.is_active,
                "roles": []
            }
        user_dict[user.id]["roles"].append(role.name)
    
    result = [UserWithRoles(**v) for v in user_dict.values()]
    return result


@router.post("/tenants/{tenant_id}/users/{user_id}/roles", response_model=UserRoleResponse, status_code=status.HTTP_201_CREATED)
def assign_role(
    tenant_id: int,
    user_id: int,
    role_data: dict,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Assign a role to a user in a tenant. Tenant admin only.
    """
    if not is_tenant_admin(current_user, tenant_id, db) and not is_super_admin(current_user, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant admin access required"
        )
    
    role_id = role_data.get("role_id")
    if not role_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="role_id is required"
        )
    
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Role not found"
        )
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    # Check if already assigned
    existing = db.query(UserRole).filter(
        UserRole.user_id == user_id,
        UserRole.tenant_id == tenant_id,
        UserRole.role_id == role_id
    ).first()
    
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already has this role"
        )
    
    user_role = UserRole(
        user_id=user_id,
        tenant_id=tenant_id,
        role_id=role_id,
        granted_by=current_user.id
    )
    db.add(user_role)
    db.commit()
    db.refresh(user_role)
    
    from app.api.auth import get_client_ip
    request_id = request.headers.get("X-Request-ID", "unknown")
    client_ip = get_client_ip(request)
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["ROLE_CHANGE"],
        target_type="user_role",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=user_id,
        target_name=user.username,
        new_value=f"role={role.name},tenant_id={tenant_id}",
        details=f"Role {role.name} assigned to user {user.username}",
        request_id=request_id,
        ip_address=client_ip,
        tenant_id=tenant_id
    )
    
    logger.info(f"Role {role.name} assigned to user {user.username} in tenant {tenant_id}")
    return user_role


@router.delete("/tenants/{tenant_id}/users/{user_id}/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_role(
    tenant_id: int,
    user_id: int,
    role_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Remove a role from a user in a tenant. Tenant admin only.
    """
    if not is_super_admin(current_user, db) and current_user.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    if not is_tenant_admin(current_user, tenant_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant admin access required"
        )
    
    user_role = db.query(UserRole).filter(
        UserRole.user_id == user_id,
        UserRole.tenant_id == tenant_id,
        UserRole.role_id == role_id
    ).first()
    
    if not user_role:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User role not found"
        )
    
    role = db.query(Role).filter(Role.id == role_id).first()
    user = db.query(User).filter(User.id == user_id).first()
    
    db.delete(user_role)
    db.commit()
    
    from app.api.auth import get_client_ip
    request_id = request.headers.get("X-Request-ID", "unknown")
    client_ip = get_client_ip(request)
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["ROLE_CHANGE"],
        target_type="user_role",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=user_id,
        target_name=user.username if user else str(user_id),
        old_value=f"role={role.name if role else role_id},tenant_id={tenant_id}",
        details=f"Role {role.name if role else role_id} removed from user {user.username if user else user_id}",
        request_id=request_id,
        ip_address=client_ip,
        tenant_id=tenant_id
    )
    
    return None


# ===========================================
# Invitations
# ===========================================

@router.get("/tenants/{tenant_id}/invitations", response_model=PaginatedInvitationsResponse)
def list_tenant_invitations(
    tenant_id: int,
    request: Request,
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    List pending invitations for a tenant with pagination. Tenant admin only.
    """
    if not is_super_admin(current_user, db) and current_user.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    if not is_tenant_admin(current_user, tenant_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only tenant admins can view invitations"
        )
    
    query = db.query(Invitation).filter(
        Invitation.tenant_id == tenant_id,
        Invitation.is_used == False
    )
    
    total = query.count()
    invitations = query.order_by(Invitation.created_at.desc()).offset(skip).limit(limit).all()
    
    return {"invitations": invitations, "total": total}


@router.post("/tenants/{tenant_id}/invitations", response_model=InvitationResponse, status_code=status.HTTP_201_CREATED)
def create_tenant_invitation(
    tenant_id: int,
    invitation_data: InvitationCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    current_tenant = Depends(get_current_tenant),
    db: Session = Depends(get_db)
):
    """
    Create an invitation for a tenant. Tenant admin only.
    """
    if not is_super_admin(current_user, db) and current_user.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    if not is_tenant_admin(current_user, tenant_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only tenant admins can invite users"
        )
    
    request_id = request.headers.get("X-Request-ID", secrets.token_urlsafe(16))
    
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found"
        )
    
    # Check if user already exists in this tenant
    existing_user = db.query(User).filter(User.email == invitation_data.email).first()
    if existing_user:
        if existing_user.tenant_id == tenant_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="User with this email already exists in this tenant"
            )
        
        # Validate role if specified (for existing user from another tenant)
        if invitation_data.role_id:
            role = db.query(Role).filter(Role.id == invitation_data.role_id).first()
            if not role:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid role specified"
                )
            if role.is_system:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot invite users with system role"
                )
            role_name = role.name
        else:
            role_name = "Viewer"
        
        # Check for existing pending invitation
        existing_invitation = db.query(Invitation).filter(
            Invitation.email == invitation_data.email,
            Invitation.tenant_id == tenant_id,
            Invitation.is_used == False
        ).first()
        if existing_invitation:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Pending invitation already exists for this email"
            )
        
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + __import__('datetime').timedelta(days=7)
        
        invitation = Invitation(
            email=invitation_data.email,
            tenant_id=tenant_id,
            role_id=invitation_data.role_id,
            token=token,
            invited_by=current_user.id,
            expires_at=expires_at
        )
        db.add(invitation)
        db.commit()
        db.refresh(invitation)
        
        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["INVITE_CREATE"],
            target_type="invitation",
            actor_id=current_user.id,
            actor_username=current_user.username,
            details=f"Invited existing user {invitation_data.email} to tenant {tenant.name} as {role_name}",
            request_id=request_id,
            tenant_id=current_tenant.id if current_tenant else None
        )
        
        logger.info(f"Invitation created for existing user {invitation_data.email} in tenant {tenant.name} (request_id={request_id})")
        return invitation
    
    # Check for existing pending invitation for new user
    existing_invitation = db.query(Invitation).filter(
        Invitation.email == invitation_data.email,
        Invitation.tenant_id == tenant_id,
        Invitation.is_used == False
    ).first()
    
    if existing_invitation:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Pending invitation already exists for this email"
        )
    
    # Validate role if specified
    if invitation_data.role_id:
        role = db.query(Role).filter(Role.id == invitation_data.role_id).first()
        if not role:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid role specified"
            )
        if role.is_system:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot invite users with system role"
            )
        role_name = role.name
    else:
        role_name = "Viewer"
    
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + __import__('datetime').timedelta(days=7)
    
    invitation = Invitation(
        email=invitation_data.email,
        tenant_id=tenant_id,
        role_id=invitation_data.role_id,
        token=token,
        invited_by=current_user.id,
        expires_at=expires_at
    )
    db.add(invitation)
    db.commit()
    db.refresh(invitation)
    
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["INVITE_CREATE"],
        target_type="invitation",
        actor_id=current_user.id,
        actor_username=current_user.username,
        details=f"Invited {invitation_data.email} to tenant {tenant.name} as {role_name}",
        request_id=request_id,
        tenant_id=current_tenant.id if current_tenant else None
    )
    
    logger.info(f"Invitation created for {invitation_data.email} in tenant {tenant.name} (request_id={request_id})")
    return invitation


@router.delete("/tenants/{tenant_id}/invitations/{invitation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tenant_invitation(
    tenant_id: int,
    invitation_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    current_tenant = Depends(get_current_tenant),
    db: Session = Depends(get_db)
):
    """
    Delete a pending invitation. Tenant admin only.
    """
    if not is_super_admin(current_user, db) and current_user.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )
    
    if not is_tenant_admin(current_user, tenant_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only tenant admins can delete invitations"
        )
    
    invitation = db.query(Invitation).filter(
        Invitation.id == invitation_id,
        Invitation.tenant_id == tenant_id
    ).first()
    
    if not invitation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invitation not found"
        )
    
    invitation_email = invitation.email
    
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["INVITE_REVOKE"],
        target_type="invitation",
        actor_id=current_user.id,
        actor_username=current_user.username,
        details=f"Revoked invitation for {invitation_email}",
        request_id=request.headers.get("X-Request-ID", secrets.token_urlsafe(16)),
        tenant_id=current_tenant.id if current_tenant else None
    )
    
    db.delete(invitation)
    db.commit()
    
    return None
