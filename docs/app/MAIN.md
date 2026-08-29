# FastAPI Application Documentation

This document describes the main FastAPI application setup, initialization, and middleware.

---

## Table of Contents
- [Overview](#overview)
- [Application Initialization](#application-initialization)
- [Middleware](#middleware)
- [Router Aggregation](#router-aggregation)
- [Lifespan Events](#lifespan-events)

---

## Overview

**File:** `app/main.py`

The main application file sets up:
- FastAPI app with documentation
- CORS middleware
- Custom middlewares
- Router aggregation
- Lifespan event handling

---

## Application Initialization

### FastAPI App

```python
app = FastAPI(
    title="Proxmox Orchestration & Infrastructure Automation",
    description="API for managing virtual infrastructure...",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)
```

### Configuration

| Setting | Description |
|----------|-------------|
| `title` | API title |
| `description` | API description |
| `version` | API version |
| `docs_url` | Swagger UI endpoint |
| `redoc_url` | ReDoc endpoint |
| `lifespan` | Lifespan event handler |

---

## Middleware

### 1. RequestBodySizeLimitMiddleware

Limits request body size to prevent memory exhaustion.

| Setting | Default |
|---------|---------|
| `REQUEST_MAX_BODY_SIZE` | 10MB |

### 2. RequestIDMiddleware

Adds unique request ID to each request for audit trails.

- Adds `X-Request-ID` header
- Adds `X-Process-Time` header

### 3. SecurityHeadersMiddleware

Adds security headers to all responses.

| Header | Value |
|--------|-------|
| `X-Content-Type-Options` | nosniff |
| `X-Frame-Options` | DENY |
| `X-XSS-Protection` | 1; mode=block |
| `Strict-Transport-Security` | max-age=31531536000 |
| `Referrer-Policy` | no-referrer-when-downgrade |
| `Cache-Control` | no-store, no-cache, must-revalidate |

### 4. CORSMiddleware

Configures Cross-Origin Resource Sharing.

```python
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", ...],
)
```

---

## Router Aggregation

All API routers are included with `/v1` prefix.

```python
app.include_router(auth_router, prefix="/v1", tags=["Authentication"])
app.include_router(vm_router, prefix="/v1")
app.include_router(networks_router, prefix="/v1")
app.include_router(firewall_router, prefix="/v1")
app.include_router(tenant_router, prefix="/v1")
app.include_router(iam_router, prefix="/v1")
app.include_router(invitations_router, prefix="/v1", tags=["Invitations"])
app.include_router(bridge_pool_router, prefix="/v1", tags=["Bridge Pool"])
app.include_router(images_router, prefix="/v1", tags=["Images"])
app.include_router(pods_router, prefix="/v1", tags=["Admin"])
app.include_router(wireguard_router, prefix="/v1")
app.include_router(admin.router, prefix="/v1")
```

### API Routes Summary

| Router | Path | Tags |
|--------|------|------|
| `auth_router` | `/v1/auth/` | Authentication |
| `vm_router` | `/v1/vm/` | VMs |
| `networks_router` | `/v1/networks/` | Networks |
| `firewall_router` | `/v1/firewall/` | Firewall |
| `tenant_router` | `/v1/tenants/` | Tenants |
| `iam_router` | `/v1/iam/` | IAM |
| `invitations_router` | `/v1/invitations/` | Invitations |
| `bridge_pool_router` | `/v1/bridges/` | Bridges |
| `images_router` | `/v1/images/` | Images |
| `pods_router` | `/v1/admin/pods/` | Admin |
| `wireguard_router` | `/v1/wireguard/` | WireGuard |
| `admin_router` | `/v1/admin/` | Admin |

---

## Health Check Endpoints

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/health` | GET | No | Basic health check - returns 200 if service is running |
| `/health/ready` | GET | No | Readiness check - verifies database connectivity |
| `/health/live` | GET | No | Liveness check - verifies service is not deadlocked |
| `/health/full` | GET | Yes (super_admin) | Comprehensive check - database + Redis connectivity |

```python
@app.get("/health")
def health_check():
    return {"status": "healthy", "environment": settings.ENVIRONMENT}

@app.get("/health/ready")
def readiness_check():
    checks = {"database": False}
    if check_database_connection():
        checks["database"] = True
    else:
        return {"status": "not_ready", "checks": checks}, 503
    return {"status": "ready", "checks": checks}

@app.get("/health/live")
def liveness_check():
    return {"status": "alive"}

@app.get("/health/full")
def full_health_check(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Requires super_admin role; checks database + Redis
    ...
```

---

## WebSocket Endpoint

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/ws` | WS | Real-time updates for VMs, networks, tenants, and WireGuard tunnels |

```python
@app.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    network_id: Optional[int] = None,
    tenant_id: Optional[int] = None,
    vm_id: Optional[int] = None,
    wireguard_tunnel_id: Optional[int] = None,
    wireguard_peer_id: Optional[int] = None,
):
    # Connects to Redis pub/sub for real-time updates
    # Supports filtering by resource type and ID
    ...
```

---

## Lifespan Events

### Startup

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Create database tables
    Base.metadata.create_all(bind=engine)
    
    # 2. Migrate database (if needed)
    
    # 3. Seed IAM permissions
    seed_permissions(db)
    
    # 4. Create default admin (if configured)
    
    # 5. Seed users without roles
    
    # 6. Seed bridge pool (100-4094)
    
    # 7. Auto-seed pod, IP pool, VLAN pool
    
    # 8. Start Redis WebSocket listener
    
    yield
    
    # Shutdown cleanup (future)
```

### Startup Tasks

| Task | Description |
|------|-------------|
| Create DB tables | Create all SQLAlchemy tables |
| Migrate | Add columns if missing |
| Seed permissions | Initialize IAM permissions |
| Create admin | Create default admin user |
| Seed bridge pool | Populate bridges 100-4094 |
| Seed infrastructure | Pod, IP pools, VLAN pools |
| Start Redis listener | WebSocket real-time updates |

---

## Seed Functions

### IAM Seeding

- `seed_permissions()` - Create permissions and roles
- `assign_default_role_to_user()` - Assign default role to users

### Infrastructure Seeding

- `seed_default_pod()` - Create default pod
- `seed_global_ip_pool()` - Create global IP pool
- `seed_vlan_pool()` - Create VLAN pool

### Bridge Pool Seeding

- Populates bridge IDs 100-4094

---

## Error Handling

### Global Exception Handler

```python
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )
```

---

## Security Features

| Feature | Implementation |
|---------|----------------|
| Request size limit | RequestBodySizeLimitMiddleware |
| Request IDs | RequestIDMiddleware |
| Security headers | SecurityHeadersMiddleware |
| CORS | CORSMiddleware |
| Rate limiting | Via config (core/rate_limit.py) |
| Token blacklist | Redis-based |