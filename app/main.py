import json
import uuid
import time
from contextlib import asynccontextmanager
import logging
from typing import Callable, Optional
from sqlalchemy.orm import Session

from fastapi import FastAPI, Depends, Request, Response, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.websockets import WebSocket, WebSocketDisconnect

from app.api.auth import router as auth_router, create_default_admin
from app.api.vm import router as vm_router 
from app.api.networks import router as networks_router
from app.api.firewall_manager import router as firewall_manager_router
from app.api.tenant import router as tenant_router
from app.api.iam import router as iam_router
from app.api.invitations import router as invitations_router
from app.api.bridge_pool import router as bridge_pool_router
from app.api.images import router as images_router
from app.api.pods import router as pods_router
from app.api.wireguard import router as wireguard_router
from app.core.database import Base, engine, SessionLocal, check_database_connection, get_pool_status, get_db
from app.api import admin
from app.core.config import settings
from app.core.iam import is_super_admin
from app.core.websocket import manager
from app.models.user import User
from app.models.tenant import Tenant
from app.models.iam import UserRole
from app.core.dependencies import get_current_user


logger = logging.getLogger(__name__)


class RequestBodySizeLimitMiddleware(BaseHTTPMiddleware):
    """
    Middleware to limit request body size to prevent memory exhaustion attacks.
    Default limit is 10MB (10485760 bytes), configurable via REQUEST_MAX_BODY_SIZE.
    """
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.method in ["POST", "PUT", "PATCH"]:
            content_length = request.headers.get("content-length")
            if content_length:
                content_length = int(content_length)
                if content_length > settings.REQUEST_MAX_BODY_SIZE:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Request body too large. Maximum allowed: {settings.REQUEST_MAX_BODY_SIZE} bytes"
                    )
        
        return await call_next(request)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    
    from sqlalchemy import text
    db = SessionLocal()
    try:
        db.execute(text("""
            ALTER TABLE tenant_networks
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        """))
        db.commit()
    except Exception:
        db.rollback()

    try:
        db.execute(text("""
            ALTER TABLE image_builds
            ADD COLUMN IF NOT EXISTS download_only BOOLEAN NOT NULL DEFAULT FALSE
        """))
        db.commit()
    except Exception:
        db.rollback()

    try:
        # Removed: Default Tenant auto-creation
        # Tenants should be explicitly created by super admin
        
        from app.core.iam.seed import seed_permissions, assign_default_role_to_user
        from app.api.auth import create_default_admin
        seed_permissions(db)
        
        # Create default admin if configured (without default tenant)
        if settings.CREATE_DEFAULT_ADMIN:
            create_default_admin(db, None)
        
        users_without_roles = db.query(User).filter(
            User.id.notin_(
                db.query(UserRole.user_id).distinct()
            )
        ).all()
        
        for user in users_without_roles:
            if user.tenant_id:
                assign_default_role_to_user(db, user, user.tenant_id)
        
        # Seed bridge pool if empty
        from app.models.bridge_pool import BridgePool
        bridge_count = db.query(BridgePool).count()
        if bridge_count == 0:
            logger.info("Seeding bridge pool (100-4094)...")
            for bridge_id in range(100, 4095):
                db.add(BridgePool(bridge_id=bridge_id, status="available"))
            db.commit()
            logger.info(f"Seeded {4095 - 100} bridges")
        
        # Auto-seed pod, IP pool, and VLAN pool if no pods exist
        from app.models.network import Pod
        pod_count = db.query(Pod).count()
        if pod_count == 0:
            logger.info("Auto-seeding default pod and IP pools...")
            from app.services.seed import (
                seed_default_pod, seed_global_ip_pool, seed_vlan_pool,
                seed_wireguard_pool,
            )
            seed_default_pod(db)
            seed_global_ip_pool(db)
            seed_vlan_pool(db, pod_id=1)
            seed_wireguard_pool(db)
            logger.info("Auto-seed completed: pod-1, global IP pool, VLAN pool, WG pool created")

        # Always (idempotently) make sure the WireGuard pool exists, even on
        # databases that already have pods but were upgraded without the
        # wireguard tables.
        from app.models.wireguard import WireGuardPool
        wg_pool_count = db.query(WireGuardPool).count()
        if wg_pool_count == 0:
            logger.info("Seeding WireGuard IP pool (10.200.0.0/14, 1024 /24s)...")
            from app.services.seed import seed_wireguard_pool
            seed_wireguard_pool(db)
            logger.info("WireGuard IP pool seeded")
        
        logger.info("IAM seeding completed")
    except Exception as e:
        logger.error(f"Failed to initialize: {e}")
        db.rollback()
    finally:
        db.close()
    
    manager.start_redis_listener()
    
    yield


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Middleware to add a unique request ID to each request for audit trails.
    """
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("X-Request-ID")
        if not request_id:
            request_id = str(uuid.uuid4())
        
        request.state.request_id = request_id
        
        start_time = time.time()
        response = await call_next(request)
        process_time = time.time() - start_time
        
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = f"{process_time:.3f}s"
        
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Middleware to add security headers to all responses.
    """
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31531536000; includeSubDomains"
        
        # Allow Swagger UI CDN in non-production environments
        if settings.ENVIRONMENT != "production":
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "connect-src 'self' https://cdn.jsdelivr.net; "
                "img-src 'self' data:;"
            )
        else:
            response.headers["Content-Security-Policy"] = "default-src 'self'"
        
        response.headers["Referrer-Policy"] = "no-referrer-when-downgrade"
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        
        return response


cors_origins = settings.cors_origins_list


app = FastAPI(
    title="Proxmox Orchestration & Infrastructure Automation",
    description=(
        "API for managing virtual infrastructure. "
        "Supports User Authentication, JWT Token Management, and VM Provisioning.\n\n"
        "## Async Operations\n\n"
        "VM and Network provisioning operations are **asynchronous**. When you create a VM or network, "
        "the API immediately returns a task ID. Long-running Terraform operations (up to 1 hour) "
        "continue in the background.\n\n"
        "### Checking Task Status\n\n"
        "- VM status is available via `GET /vm/{vm_id}` - check the `status` field\n"
        "- Network status is available via `GET /networks/{network_id}` - check the `status` field\n"
        "- Status transitions: `pending` → `provisioning` → `running` (or `error` on failure)\n\n"
        "### HTTP Timeouts\n\n"
        "Since operations are async, your HTTP client timeout should be 30-60 seconds. "
        "Do not wait for provisioning to complete in the initial request.\n\n"
        "### Error Recovery\n\n"
        "- Failed VMs are marked `error` and can be retried via admin endpoints\n"
        "- Partial infrastructure is automatically rolled back on failure\n"
    ),
    version="1.0.0",
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url="/redoc" if settings.ENVIRONMENT != "production" else None,
    lifespan=lifespan,
)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch unhandled exceptions and return JSON responses so CORS middleware can add headers."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )

app.add_middleware(RequestBodySizeLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIDMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins + ["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Requested-With", "X-Request-ID", "X-Tenant-ID", "X-Impersonate-Tenant-ID"],
    expose_headers=["X-Request-ID", "X-Process-Time"],
)

app.include_router(auth_router, prefix="/v1", tags=["Authentication"])
app.include_router(vm_router, prefix="/v1")
app.include_router(networks_router, prefix="/v1")
app.include_router(firewall_manager_router, prefix="/v1")
app.include_router(tenant_router, prefix="/v1")
app.include_router(iam_router, prefix="/v1")
app.include_router(invitations_router, prefix="/v1", tags=["Invitations"])
app.include_router(bridge_pool_router, prefix="/v1", tags=["Bridge Pool"])
app.include_router(images_router, prefix="/v1", tags=["Images"])
app.include_router(pods_router, prefix="/v1", tags=["Admin"])
app.include_router(wireguard_router, prefix="/v1")
app.include_router(admin.router, prefix="/v1")


@app.get("/")
def read_root():
    return {
        "message": "Proxmox Automation API",
        "status": "running",
        "version": "1.0.0",
        "environment": settings.ENVIRONMENT,
    }


@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    network_id: Optional[int] = None,
    tenant_id: Optional[int] = None,
    vm_id: Optional[int] = None,
    wireguard_tunnel_id: Optional[int] = None,
    wireguard_peer_id: Optional[int] = None,
):
    key = None
    if wireguard_tunnel_id is not None:
        key = f"wireguard:{wireguard_tunnel_id}"
    elif wireguard_peer_id is not None:
        key = f"wireguard-peer:{wireguard_peer_id}"
    elif tenant_id is not None:
        key = f"tenant:{tenant_id}"
    elif vm_id is not None:
        key = vm_id
    elif network_id is not None:
        key = network_id

    if vm_id is not None and tenant_id is not None:
        from app.core.database import SessionLocal
        from app.models.vm import VM
        db = SessionLocal()
        try:
            vm = db.query(VM).filter(VM.id == vm_id, VM.tenant_id == tenant_id).first()
            if not vm:
                logger.warning(f"WebSocket rejected: VM {vm_id} does not belong to tenant {tenant_id}")
                await websocket.close(code=4003, reason="VM not accessible")
                return
        finally:
            db.close()

    await manager.connect(websocket, key)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket, key)


@app.get("/health")
def health_check():
    """
    Basic health check endpoint.
    Returns 200 if the service is running.
    """
    return {"status": "healthy", "environment": settings.ENVIRONMENT}


@app.get("/health/ready")
def readiness_check():
    """
    Readiness check - verifies critical dependencies are available.
    Used by orchestrators (Kubernetes, etc.) to determine if traffic should be routed.
    """
    checks = {
        "database": False,
    }
    
    try:
        if check_database_connection():
            checks["database"] = True
        else:
            return {"status": "not_ready", "checks": checks}, 503
    except Exception as e:
        logger.error(f"Readiness check failed - database: {e}")
        return {"status": "not_ready", "checks": checks}, 503
    
    return {"status": "ready", "checks": checks}


@app.get("/health/live")
def liveness_check():
    """
    Liveness check - verifies the service is not deadlocked.
    Used by orchestrators to determine if the container should be restarted.
    """
    return {"status": "alive"}


@app.get("/health/full")
def full_health_check(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Comprehensive health check including database and Redis connectivity.
    Requires authentication. Detailed infrastructure info is only shown to admins.
    Does not expose internal error details for security.
    """
    if not is_super_admin(current_user, db):
        return {
            "status": "healthy",
            "environment": settings.ENVIRONMENT,
        }
    
    health_status = {
        "status": "healthy",
        "environment": settings.ENVIRONMENT,
        "components": {}
    }
    
    db_healthy = True
    try:
        if check_database_connection():
            health_status["components"]["database"] = "healthy"
            health_status["components"]["database_pool"] = get_pool_status()
        else:
            health_status["components"]["database"] = "unhealthy"
            db_healthy = False
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        health_status["components"]["database"] = "unhealthy"
        db_healthy = False
    
    redis_healthy = True
    if settings.CELERY_BROKER_URL:
        try:
            import redis
            r = redis.from_url(settings.CELERY_BROKER_URL)
            r.ping()
            health_status["components"]["redis"] = "healthy"
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            health_status["components"]["redis"] = "unhealthy"
            redis_healthy = False
    else:
        health_status["components"]["redis"] = "not_configured"
    
    if not all([db_healthy, redis_healthy]):
        health_status["status"] = "degraded"
    
    return health_status
