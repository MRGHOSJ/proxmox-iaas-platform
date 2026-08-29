import logging
import secrets
from typing import Annotated, Optional
from datetime import datetime, timedelta, timezone

from app.core.database import get_db
from app.core.security import (
    create_access_token,
    hash_password,
    verify_password,
)
from app.core.config import settings
from app.core.iam import is_super_admin
from app.core.dependencies import get_current_user, get_client_ip, get_current_tenant
from app.core.rate_limit import check_rate_limit
from app.core.audit import log_audit_event, AUDIT_ACTIONS
from app.models.user import User
from app.models.tenant import Tenant
from app.schemas.user import (
    Token, UserCreate, UserResponse, 
    UserProfileUpdate, PasswordChange
)
from app.schemas.invitation import UserRegisterWithTenant
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Authentication"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/login")


def _validate_email_domain(email: str) -> None:
    """Validate email domain against allowed list."""
    if not settings.allowed_email_domains_list:
        return
    
    domain = email.split("@")[-1].lower()
    if domain not in settings.allowed_email_domains_list:
        logger.warning(f"Registration rejected: email domain '{domain}' not in allowed list")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Email domain '{domain}' is not allowed. Allowed domains: {', '.join(settings.allowed_email_domains_list)}"
        )


def _require_admin(current_user: User, db: Session, request: Optional[Request] = None) -> None:
    """Helper to check admin access with audit logging."""
    if not is_super_admin(current_user, db):
        request_id = request.headers.get("X-Request-ID", "unknown") if request else "unknown"
        logger.warning(
            f"Admin access denied for user {current_user.id} ({current_user.username}) "
            f"(request_id={request_id})"
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required"
        )


def create_default_admin(db: Session, tenant_id: Optional[int] = None) -> bool:
    """
    Create default admin user from environment variables.
    Returns True if admin was created, False otherwise.
    """
    if not settings.DEFAULT_ADMIN_USERNAME or not settings.DEFAULT_ADMIN_PASSWORD:
        logger.debug("Default admin credentials not configured, skipping.")
        return False
    
    existing = db.query(User).filter(
        User.username == settings.DEFAULT_ADMIN_USERNAME
    ).first()
    
    if existing:
        logger.debug(f"Default admin '{settings.DEFAULT_ADMIN_USERNAME}' already exists.")
        return False
    
    try:
        admin_user = User(
            username=settings.DEFAULT_ADMIN_USERNAME,
            email=settings.DEFAULT_ADMIN_EMAIL,
            full_name="Default Administrator",
            hashed_password=hash_password(settings.DEFAULT_ADMIN_PASSWORD),
            is_active=True,
            tenant_id=tenant_id
        )
        db.add(admin_user)
        db.flush()
        
        from app.core.iam.seed import assign_super_admin_role
        assign_super_admin_role(db, admin_user)
        
        db.commit()
        logger.info(f"Created default admin user with super_admin IAM role: {settings.DEFAULT_ADMIN_USERNAME}")
        return True
    except Exception as e:
        logger.error(f"Failed to create default admin: {e}")
        db.rollback()
        return False


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(
    user_data: UserRegisterWithTenant, 
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Register a new user with a new tenant.
    
    Note: Registration can be disabled via ALLOW_REGISTRATION=false env variable.
    When disabled, only admins can create users.
    Rate limited to prevent abuse.
    """
    check_rate_limit(request, endpoint="register")
    
    if not settings.ALLOW_REGISTRATION:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Public registration is disabled. Contact an administrator."
        )
    
    _validate_email_domain(user_data.email)
    
    request_id = request.headers.get("X-Request-ID", secrets.token_urlsafe(16))
    logger.info(f"Registration attempt for username: {user_data.username} (request_id={request_id})")
    
    try:
        existing_user = db.query(User).filter(
            (User.username == user_data.username) | (User.email == user_data.email)
        ).first()
        
        if existing_user:
            logger.warning(f"Registration failed: Username/Email already exists (request_id={request_id})")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Username or email already registered"
            )
        
        tenant_slug = user_data.tenant_slug or user_data.tenant_name.lower().replace(" ", "-")
        
        existing_tenant = db.query(Tenant).filter(
            (Tenant.slug == tenant_slug) | (Tenant.name == user_data.tenant_name)
        ).first()
        
        if existing_tenant:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Organization with this name already exists"
            )
        
        new_tenant = Tenant(
            name=user_data.tenant_name,
            slug=tenant_slug,
            is_active=True,
            is_verified=False,
            settings="{}"
        )
        db.add(new_tenant)
        db.flush()
        
        hashed_password = hash_password(user_data.password)
        
        db_user = User(
            username=user_data.username,
            email=user_data.email,
            full_name=user_data.full_name,
            hashed_password=hashed_password,
            tenant_id=new_tenant.id
        )
        
        db.add(db_user)
        db.flush()
        
        from app.core.iam.seed import seed_permissions, assign_tenant_admin_role
        seed_permissions(db)
        assign_tenant_admin_role(db, db_user, new_tenant.id)
        
        db.commit()
        db.refresh(db_user)
        
        client_ip = get_client_ip(request)
        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["TENANT_CREATE"],
            target_type="tenant",
            actor_id=db_user.id,
            actor_username=db_user.username,
            target_id=new_tenant.id,
            target_name=new_tenant.name,
            new_value=f"slug={new_tenant.slug},is_verified=false",
            request_id=request_id,
            ip_address=client_ip,
            tenant_id=new_tenant.id
        )
        
        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["USER_CREATE"],
            target_type="user",
            actor_id=db_user.id,
            actor_username=db_user.username,
            target_id=db_user.id,
            target_name=db_user.username,
            new_value=f"email={db_user.email},tenant_id={new_tenant.id}",
            request_id=request_id,
            ip_address=client_ip,
            tenant_id=new_tenant.id
        )
        
        logger.info(f"User registered successfully with new tenant: {user_data.username} (ID: {db_user.id}, tenant: {new_tenant.name}, request_id={request_id})")
        return db_user

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during registration (request_id={request_id}): {e}", exc_info=True)
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred during registration."
        )


@router.post("/login", response_model=Token)
def login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()], 
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Authenticate a user and return a JWT access token.
    Rate limited to prevent brute force attacks.
    """
    check_rate_limit(request, endpoint="login")
    
    request_id = request.headers.get("X-Request-ID", secrets.token_urlsafe(16))
    logger.info(f"Login attempt for username: {form_data.username} (request_id={request_id})")
    
    try:
        user = db.query(User).filter(User.username == form_data.username).first()
        
        if not user:
            logger.warning(f"Login failed: User not found (request_id={request_id})")
            client_ip = get_client_ip(request)
            log_audit_event(
                db=db,
                action=AUDIT_ACTIONS["LOGIN_FAILED"],
                target_type="user",
                actor_id=None,
                actor_username=form_data.username,
                details="User not found",
                request_id=request_id,
                ip_address=client_ip
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        if not verify_password(form_data.password, user.hashed_password):
            logger.warning(f"Login failed: Incorrect password (request_id={request_id})")
            client_ip = get_client_ip(request)
            log_audit_event(
                db=db,
                action=AUDIT_ACTIONS["LOGIN_FAILED"],
                target_type="user",
                actor_id=user.id,
                actor_username=user.username,
                details="Incorrect password",
                request_id=request_id,
                ip_address=client_ip
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect username or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        if not user.is_active:
            logger.warning(f"Login failed: User inactive (request_id={request_id})")
            client_ip = get_client_ip(request)
            log_audit_event(
                db=db,
                action=AUDIT_ACTIONS["LOGIN_FAILED"],
                target_type="user",
                actor_id=user.id,
                actor_username=user.username,
                details="User inactive",
                request_id=request_id,
                ip_address=client_ip
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Inactive user"
            )
        
        if user.tenant and not user.tenant.is_verified and not is_super_admin(user, db):
            logger.warning(f"Login failed: Tenant not verified (request_id={request_id})")
            client_ip = get_client_ip(request)
            log_audit_event(
                db=db,
                action=AUDIT_ACTIONS["LOGIN_FAILED"],
                target_type="user",
                actor_id=user.id,
                actor_username=user.username,
                details="Tenant not verified",
                request_id=request_id,
                ip_address=client_ip
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your tenant is pending verification. Contact an administrator."
            )
        
        access_token = create_access_token(data={
            "sub": str(user.id), 
            "tenant_id": user.tenant_id
        })
        
        client_ip = get_client_ip(request)
        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["LOGIN"],
            target_type="user",
            actor_id=user.id,
            actor_username=user.username,
            request_id=request_id,
            ip_address=client_ip
        )
        
        logger.info(f"User logged in successfully: {user.username} (request_id={request_id})")
        return {"access_token": access_token, "token_type": "bearer"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error during login (request_id={request_id}): {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal error occurred during login."
        )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    token: str = Depends(oauth2_scheme),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Logout the current user by blacklisting their token.
    The token will be invalidated until it naturally expires.
    """
    from app.core.token_blacklist import add_token_to_blacklist
    from jose import jwt
    
    request_id = request.headers.get("X-Request-ID", "unknown")
    
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.ALGORITHM])
        jti = payload.get("jti")
        exp = payload.get("exp")
        
        if jti and exp:
            add_token_to_blacklist(jti, exp)
            
            client_ip = get_client_ip(request)
            log_audit_event(
                db=db,
                action=AUDIT_ACTIONS["LOGOUT"],
                target_type="user",
                actor_id=current_user.id,
                actor_username=current_user.username,
                request_id=request_id,
                ip_address=client_ip
            )
            
            logger.info(f"User {current_user.username} logged out (request_id={request_id})")
    except Exception as e:
        logger.error(f"Error during logout for {current_user.username}: {e}")
    
    return None


@router.get("/me", response_model=UserResponse)
def get_current_user_info(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Retrieve the profile of the currently authenticated user.
    Includes IAM roles for authorization.
    """
    from app.core.iam import is_super_admin, is_tenant_admin
    from app.models.iam import UserRole, Role
    
    logger.info(f"Fetching profile for user: {current_user.username}")
    
    iam_roles = []
    
    # Get all IAM roles for this user
    user_roles = db.query(UserRole).filter(
        UserRole.user_id == current_user.id
    ).all()
    
    for ur in user_roles:
        role = db.query(Role).filter(Role.id == ur.role_id).first()
        if role:
            permissions = [p.name for p in role.permissions]
            iam_roles.append({
                "id": role.id,
                "name": role.name,
                "description": role.description,
                "is_system": role.is_system,
                "is_preset": role.is_preset,
                "tenant_id": ur.tenant_id,
                "permissions": permissions
            })
    
    # Add IAM roles to user object
    current_user.iam_roles = iam_roles
    
    return current_user


@router.patch("/me", response_model=UserResponse)
def update_current_user_profile(
    profile_update: UserProfileUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Update the profile of the currently authenticated user.
    """
    request_id = request.headers.get("X-Request-ID", secrets.token_urlsafe(16))
    client_ip = get_client_ip(request)
    
    changes = []
    
    if profile_update.email is not None:
        existing = db.query(User).filter(
            User.email == profile_update.email,
            User.id != current_user.id
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use"
            )
        if current_user.email != profile_update.email:
            changes.append(f"email: {current_user.email} -> {profile_update.email}")
            current_user.email = profile_update.email
    
    if profile_update.full_name is not None:
        old_full_name = current_user.full_name or ""
        new_full_name = profile_update.full_name or ""
        if old_full_name != new_full_name:
            changes.append(f"full_name: '{old_full_name}' -> '{new_full_name}'")
            current_user.full_name = profile_update.full_name
    
    db.commit()
    db.refresh(current_user)
    
    if changes:
        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["USER_PROFILE_UPDATE"],
            target_type="user",
            actor_id=current_user.id,
            actor_username=current_user.username,
            target_id=current_user.id,
            target_name=current_user.username,
            details="; ".join(changes),
            request_id=request_id,
            ip_address=client_ip
        )
    
    logger.info(f"User {current_user.username} updated their profile (request_id={request_id})")
    return current_user


@router.post("/me/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    password_change: PasswordChange,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Change the password of the currently authenticated user.
    """
    request_id = request.headers.get("X-Request-ID", secrets.token_urlsafe(16))
    
    if not verify_password(password_change.current_password, current_user.hashed_password):
        logger.warning(f"Password change failed: incorrect current password (request_id={request_id})")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect"
        )
    
    current_user.hashed_password = hash_password(password_change.new_password)
    db.commit()
    
    client_ip = get_client_ip(request)
    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["PASSWORD_CHANGE"],
        target_type="user",
        actor_id=current_user.id,
        actor_username=current_user.username,
        request_id=request_id,
        ip_address=client_ip
    )
    
    logger.info(f"User {current_user.username} changed their password (request_id={request_id})")
    return None

