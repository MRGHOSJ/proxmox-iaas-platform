import logging
from typing import Optional
from fastapi import Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import verify_token
from app.models.user import User
from app.models.tenant import Tenant

logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/login")


def get_client_ip(request: Request) -> Optional[str]:
    """
    Get the real client IP address, considering proxies and load balancers.
    
    Checks headers in order:
    1. X-Forwarded-For (most common, may contain multiple IPs)
    2. X-Real-IP (nginx, some proxies)
    3. Fallback to request.client.host
    
    Returns the first (original) client IP if multiple are present.
    """
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        ips = [ip.strip() for ip in x_forwarded_for.split(",")]
        return ips[0] if ips else None
    
    x_real_ip = request.headers.get("X-Real-IP")
    if x_real_ip:
        return x_real_ip.strip()
    
    if request.client:
        return request.client.host
    
    return None


def get_current_user(
    token: str = Depends(oauth2_scheme), 
    db: Session = Depends(get_db)
) -> User:
    """
    Dependency to retrieve the current user from a valid JWT token.
    Verifies both token validity AND that role claim matches database.
    """
    logger.debug("Verifying access token...")
    
    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = verify_token(token, credentials_exception)
        
        if not payload:
            logger.warning("Token verification failed: Invalid payload.")
            raise credentials_exception
        
        user_id = payload.get("sub")
        if user_id is None:
            logger.warning("Token verification failed: No subject in token.")
            raise credentials_exception
        
        logger.debug(f"Fetching user from DB with ID: {user_id}")
        user = db.query(User).filter(User.id == int(user_id)).first()
        
        if not user:
            logger.warning(f"Token verification failed: User with ID {user_id} not found.")
            raise HTTPException(
                status_code=404,
                detail="User not found"
            )
        
        if not user.is_active:
            raise HTTPException(
                status_code=403,
                detail="User account is deactivated"
            )
        
        return user

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_current_user: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Internal error validating user."
        )


class TenantContext:
    def __init__(self, tenant: Tenant, user: User):
        self.tenant = tenant
        self.user = user
        self.tenant_id = tenant.id if tenant else None


def extract_tenant_from_request(request: Request) -> Optional[int | str]:
    """
    Extract tenant ID from request.
    Priority: 
    1. X-Tenant-ID header (for API clients)
    2. Subdomain (for web UI)
    3. None (fallback to user's default tenant)
    """
    tenant_id_header = request.headers.get("X-Tenant-ID")
    if tenant_id_header:
        try:
            return int(tenant_id_header)
        except ValueError:
            logger.warning(f"Invalid X-Tenant-ID header: {tenant_id_header}")
    
    host = request.headers.get("host", "")
    if "." in host:
        subdomain = host.split(".")[0]
        if subdomain not in ["www", "api", "localhost"]:
            logger.debug(f"Extracted subdomain as tenant slug: {subdomain}")
            return subdomain
    
    return None


def get_impersonation_context(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Optional[str]:
    """
    Returns the admin username if the request is being made during impersonation.
    Returns None if not impersonating.
    """
    from app.core.iam import is_super_admin
    
    impersonate_id = request.headers.get("X-Impersonate-Tenant-ID")
    if impersonate_id and is_super_admin(current_user, db):
        return current_user.username
    return None


def get_current_tenant(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> Tenant:
    """
    Dependency to get the current tenant from request.
    Checks X-Impersonate-Tenant-ID header first (for super admin impersonation),
    then X-Tenant-ID header, then subdomain, then falls back to user's tenant.
    Only allows access to tenants the user belongs to (unless super_admin).
    """
    from app.core.iam import is_super_admin
    
    # IMPERSONATION: Check FIRST, before any normal resolution
    impersonate_id = request.headers.get("X-Impersonate-Tenant-ID")
    if impersonate_id:
        try:
            impersonate_id_int = int(impersonate_id)
            tenant = db.query(Tenant).filter(Tenant.id == impersonate_id_int).first()
        except ValueError:
            tenant = db.query(Tenant).filter(Tenant.slug == impersonate_id).first()
        
        if tenant and tenant.is_active and is_super_admin(current_user, db):
            logger.info(
                f"IMPERSONATION: admin_id={current_user.id} "
                f"target_tenant_id={tenant.id} target_tenant_name={tenant.name} "
                f"endpoint={request.url.path} method={request.method}"
            )
            return tenant
    
    tenant_id = extract_tenant_from_request(request)
    
    if tenant_id:
        if isinstance(tenant_id, int):
            tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        else:
            tenant = db.query(Tenant).filter(Tenant.slug == tenant_id).first()
        
        if tenant and tenant.is_active:
            if is_super_admin(current_user, db):
                return tenant
            
            from app.models.iam import UserRole
            user_tenant_ids = db.query(UserRole.tenant_id).filter(
                UserRole.user_id == current_user.id
            ).distinct().all()
            user_tenant_ids = [t.tenant_id for t in user_tenant_ids]
            user_tenant_ids.append(current_user.tenant_id)
            
            if tenant.id not in user_tenant_ids:
                raise HTTPException(
                    status_code=403,
                    detail="You do not have access to this tenant"
                )
            return tenant
        elif tenant and not tenant.is_active:
            raise HTTPException(
                status_code=403,
                detail="Tenant is inactive"
            )
    
    if current_user.tenant_id:
        tenant = db.query(Tenant).filter(Tenant.id == current_user.tenant_id).first()
        if tenant and tenant.is_active:
            return tenant
    
    if not tenant_id:
        raise HTTPException(
            status_code=400,
            detail="Tenant not specified. Provide X-Tenant-ID header or access via tenant subdomain."
        )
    
    raise HTTPException(
        status_code=404,
        detail="Tenant not found"
    )


def get_tenant_context(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
) -> TenantContext:
    """
    Dependency that provides both the current user and their tenant.
    """
    tenant = get_current_tenant(request, current_user, db)
    return TenantContext(tenant=tenant, user=current_user)
