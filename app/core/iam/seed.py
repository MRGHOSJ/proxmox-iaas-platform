import logging
from typing import Optional
from sqlalchemy.orm import Session

from app.models.iam import Permission, Role, UserRole
from app.models.user import User
from app.core.iam.permissions import (
    ALL_PERMISSIONS, ROLE_PERMISSION_MAPPING,
    VM_PERMISSIONS, NETWORK_PERMISSIONS, FIREWALL_PERMISSIONS,
    WIREGUARD_PERMISSIONS,
    IP_PERMISSIONS, TENANT_PERMISSIONS, USER_PERMISSIONS, AUDIT_PERMISSIONS
)

logger = logging.getLogger(__name__)

SUPER_ADMIN_TENANT_ID = None  # System-wide super_admin marker


def seed_permissions(db: Session) -> None:
    # Check if permissions already exist - but still process to add any missing
    existing_perms = db.query(Permission).all()
    
    # Build permission_map from existing
    permission_map = {perm.name: perm for perm in existing_perms}
    
    all_perm_defs = {
        "vm": VM_PERMISSIONS,
        "network": NETWORK_PERMISSIONS,
        "firewall": FIREWALL_PERMISSIONS,
        "wireguard": WIREGUARD_PERMISSIONS,
        "ip": IP_PERMISSIONS,
        "audit": AUDIT_PERMISSIONS,
        "tenant": TENANT_PERMISSIONS,
        "user": USER_PERMISSIONS,
    }
    
    # Add any missing permissions
    for resource_type, perms in all_perm_defs.items():
        for perm_name in perms:
            # Skip if already exists
            if perm_name in permission_map:
                continue
            
            # Create new permission
            action = perm_name.replace(":", "_") if ":" in perm_name else perm_name
            perm = Permission(
                name=perm_name,
                resource_type=resource_type,
                action=action,
                description=f"Permission for {perm_name.replace(':', ' ')}"
            )
            db.add(perm)
            db.flush()
            permission_map[perm_name] = perm
            logger.info(f"Added new permission: {perm_name}")
    
    # Commit if we added any new permissions
    if len(permission_map) > len(existing_perms):
        db.commit()
        logger.info(f"Seeded {len(permission_map) - len(existing_perms)} new permissions")
    
    # Always seed preset roles to ensure they have all permissions
    _seed_preset_roles(db, permission_map)


def _seed_preset_roles(db: Session, permission_map: dict) -> None:
    existing_roles = db.query(Role).filter(Role.is_preset == True).all()
    
    # Get all permission names from the map
    perm_names = set(permission_map.keys())
    
    for role_name, perm_names_list in ROLE_PERMISSION_MAPPING.items():
        # Find existing role or create new
        role = next((r for r in existing_roles if r.name == role_name), None)
        
        if not role:
            # Create new role
            role = Role(
                name=role_name,
                description=f"Preset {role_name} role",
                is_preset=True,
                is_system=False,
                tenant_id=None
            )
            db.add(role)
            db.flush()
            existing_roles.append(role)
        
        # Get current permission names for this role
        current_perm_names = set(p.name for p in role.permissions)
        
        # Add any missing permissions
        for perm_name in perm_names_list:
            if perm_name in permission_map and perm_name not in current_perm_names:
                role.permissions.append(permission_map[perm_name])
                logger.info(f"Added permission {perm_name} to role {role_name}")
    
    # Handle super_admin role
    super_admin = next((r for r in existing_roles if r.name == "super_admin"), None)
    if not super_admin:
        super_admin = Role(
            name="super_admin",
            description="Super admin - full system access",
            is_preset=True,
            is_system=True,
            tenant_id=SUPER_ADMIN_TENANT_ID
        )
        db.add(super_admin)
        db.flush()
        existing_roles.append(super_admin)
    
    # Add all permissions to super_admin
    super_admin_perm_names = set(p.name for p in super_admin.permissions) if super_admin.permissions else set()
    for perm_name, perm in permission_map.items():
        if perm_name not in super_admin_perm_names:
            super_admin.permissions.append(perm)
    
    db.commit()
    
    logger.info(f"Seeded/updated {len(existing_roles)} preset roles")


def assign_super_admin_role(db: Session, user: User) -> None:
    """
    Assign system-wide super_admin role to a user.
    The super_admin role has tenant_id=-1 to indicate system-wide access.
    """
    role = db.query(Role).filter(
        Role.name == "super_admin",
        Role.is_system == True
    ).first()
    
    if not role:
        logger.error("super_admin role not found in database")
        return
    
    existing = db.query(UserRole).filter(
        UserRole.user_id == user.id,
        UserRole.role_id == role.id
    ).first()
    
    if existing:
        logger.debug(f"User {user.id} already has super_admin role")
        return
    
    user_role = UserRole(
        user_id=user.id,
        tenant_id=SUPER_ADMIN_TENANT_ID,
        role_id=role.id,
        granted_by=None
    )
    db.add(user_role)
    db.commit()
    logger.info(f"Assigned super_admin role to user {user.id} (system-wide)")


def assign_tenant_admin_role(db: Session, user: User, tenant_id: int) -> None:
    """
    Assign tenant_admin role to a user for a specific tenant.
    """
    role = db.query(Role).filter(
        Role.name == "tenant_admin",
        Role.tenant_id == None,
        Role.is_system == False
    ).first()
    
    if not role:
        logger.error("tenant_admin role not found in database")
        return
    
    existing = db.query(UserRole).filter(
        UserRole.user_id == user.id,
        UserRole.tenant_id == tenant_id
    ).first()
    
    if existing:
        logger.debug(f"User {user.id} already has a role in tenant {tenant_id}")
        return
    
    user_role = UserRole(
        user_id=user.id,
        tenant_id=tenant_id,
        role_id=role.id,
        granted_by=None
    )
    db.add(user_role)
    db.commit()
    logger.info(f"Assigned tenant_admin role to user {user.id} for tenant {tenant_id}")


def assign_viewer_role(db: Session, user: User, tenant_id: int) -> None:
    """
    Assign viewer role to a user for a specific tenant.
    """
    role = db.query(Role).filter(
        Role.name == "viewer",
        Role.tenant_id == None,
        Role.is_system == False
    ).first()
    
    if not role:
        logger.error("viewer role not found in database")
        return
    
    user_role = UserRole(
        user_id=user.id,
        tenant_id=tenant_id,
        role_id=role.id,
        granted_by=None
    )
    db.add(user_role)
    db.commit()
    logger.info(f"Assigned viewer role to user {user.id} for tenant {tenant_id}")


def assign_default_role_to_user(db: Session, user: User, tenant_id: int, is_super_admin: bool = False) -> None:
    """
    Assign default IAM role to a user based on registration type.
    
    Args:
        db: Database session
        user: The user to assign role to
        tenant_id: The tenant ID (not used for super_admin)
        is_super_admin: If True, assign super_admin role (system-wide)
    """
    if is_super_admin:
        assign_super_admin_role(db, user)
        return
    
    assign_tenant_admin_role(db, user, tenant_id)
