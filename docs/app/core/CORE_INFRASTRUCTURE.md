# Core Infrastructure Documentation

This document describes the foundational configuration, security, database, IAM permissions, rate limiting, and background task logic used throughout the Platform. These modules are located in the `app/core/` directory.

---

## Table of Contents
- [Configuration Management](#configuration-management)
- [Database Connection](#database-connection)
- [Security & Auth](#security--auth)
- [Dependencies](#dependencies)
- [IAM Permissions](#iam-permissions)
- [Rate Limiting](#rate-limiting)
- [Token Blacklist](#token-blacklist)
- [Custom Exceptions](#custom-exceptions)
- [Background Tasks (Celery)](#background-tasks-celery)

---

## Configuration Management

**File:** `app/core/config.py`

This module is responsible for loading environment variables and setting up the global logging standard. It includes a validation step to ensure critical settings are present in production environments.

### Environment Variables

#### Core Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `DATABASE_URL` | string | - | SQLAlchemy connection string (e.g., `postgresql://user:pass@host/db`) |
| `JWT_SECRET_KEY` | string | - | Secret key used for signing JWT tokens |
| `ALGORITHM` | string | `HS256` | Encryption algorithm used for JWT |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | integer | `30` | Expiration time for access tokens in minutes |
| `DEBUG` | boolean | `false` | Enables verbose logging if set to `true` |
| `LOG_LEVEL` | string | `INFO` | Sets the logging threshold |
| `ENVIRONMENT` | string | `development` | Application environment (`development`, `production`) |
| `CORS_ORIGINS` | string | `["http://localhost:3000", "http://localhost:8000"]` | JSON string array of allowed CORS origins |

#### Authentication Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `ALLOW_REGISTRATION` | boolean | `false` | Enable/disable public user registration |
| `ALLOWED_EMAIL_DOMAINS` | string | `null` | Comma-separated allowed email domains |
| `DEFAULT_ADMIN_USERNAME` | string | `None` | Username for auto-created admin account |
| `DEFAULT_ADMIN_PASSWORD` | string | `None` | Password for auto-created admin account |
| `DEFAULT_ADMIN_EMAIL` | string | `admin@cloud.com` | Email for auto-created admin account |

#### Rate Limiting Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `RATE_LIMIT_ENABLED` | boolean | `true` | Enable or disable rate limiting globally |
| `RATE_LIMIT_REQUESTS` | integer | `5` | Maximum requests allowed within the period |
| `RATE_LIMIT_PERIOD_SECONDS` | integer | `60` | Time window for rate limiting in seconds |
| `RATE_LIMIT_ADMIN_REQUESTS` | integer | `10` | Stricter limit for admin endpoints |
| `RATE_LIMIT_ADMIN_PERIOD_SECONDS` | integer | `60` | Admin rate limit window |

#### Celery Settings

| Variable | Type | Description |
|----------|------|-------------|
| `CELERY_BROKER_URL` | string | Redis URL for message broker (e.g., `redis://localhost:6379/0`) |
| `CELERY_RESULT_BACKEND` | string | Redis URL for storing task results |

#### Proxmox Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `PROXMOX_URL` | string | `https://YOUR_PROXMOX_HOST:8006` | Proxmox API URL |
| `PROXMOX_USERNAME` | string | `root@pam` | Proxmox API token user |
| `PROXMOX_TOKEN` | string | - | Proxmox API token secret |
| `PROXMOX_NODE` | string | `pve` | Proxmox node name |
| `PROXMOX_STORAGE` | string | `local-lvm` | Default storage pool |

#### OPNsense Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `OPNSENSE_TEMPLATE_ID` | integer | `9000` | VM ID of OPNsense template |
| `OPNSENSE_BOOTSTRAP_KEY` | string | - | Initial API key for OPNsense |
| `OPNSENSE_BOOTSTRAP_SECRET` | string | - | Initial API secret for OPNsense |
| `OPNSENSE_MIN_MEMORY_MB` | integer | `800` | Minimum memory for OPNsense |

#### WireGuard Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `WIREGUARD_DEFAULT_LISTEN_PORT` | integer | `51820` | Default listen port |
| `WIREGUARD_DEFAULT_MTU` | integer | `1420` | Default MTU |
| `WIREGUARD_DEFAULT_DNS` | string | `1.1.1.1, 1.0.0.1` | Default DNS servers |
| `WIREGUARD_GLOBAL_POOL_CIDR` | string | `10.200.0.0/14` | Global WireGuard IP pool |
| `WIREGUARD_FIELD_ENCRYPTION_KEY` | string | - | Fernet key for encrypting private keys at rest |

#### HashiCorp Vault Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `VAULT_ADDR` | string | - | Vault server address |
| `VAULT_ROLE_ID` | string | - | AppRole role ID |
| `VAULT_SECRET_ID` | string | - | AppRole secret ID |

#### Application Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `CREATE_DEFAULT_ADMIN` | boolean | `false` | Auto-create admin on first run |
| `TERRAFORM_TEMP_DIR` | string | - | Custom Terraform temp directory |
| `DEFAULT_VM_IMAGE` | string | `nginx:latest` | Default VM image (legacy) |
| `GUEST_AGENT_SETTLE_DELAY` | integer | `1` | Guest agent settle delay (seconds) |

### Settings Class & Validation

The `Settings` class includes a `validate_production_settings` method that runs on startup. If `ENVIRONMENT` is set to `production`, it verifies:
1. `JWT_SECRET_KEY` is set and not the insecure default.
2. `DEFAULT_ADMIN_PASSWORD` is set.
3. `DEFAULT_ADMIN_EMAIL` is set and not the default `admin@cloud.com`.
4. `DATABASE_URL` is set and does not contain `sqlite`.
5. `CELERY_BROKER_URL` starts with `redis://` when `REQUIRE_REDIS_IN_PRODUCTION` is True.
6. `CORS_ORIGINS` does not contain `*`.

If any check fails, the application logs the error and exits immediately (`sys.exit(1)`).

---

## Database Connection

**File:** `app/core/database.py`

This module initializes the SQLAlchemy engine and provides a dependency generator for FastAPI to manage database sessions safely.

### Components

| Component | Description |
|-----------|-------------|
| `engine` | The core SQLAlchemy engine connecting to the database defined in `DATABASE_URL` |
| `SessionLocal` | A factory class for creating new database sessions. Configured with `autocommit=False` and `autoflush=False` |
| `Base` | The declarative base class imported by all database models (e.g., `class User(Base)`) |

### Dependency: `get_db`

```python
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

**Behavior:**
1. Opens a session at the start of a request
2. Yields the session to the endpoint
3. **Crucially**, the `finally` block ensures the session is always closed, preventing connection leaks even if the request raises an exception

---

## Security & Auth

**File:** `app/core/security.py`

This module handles cryptographic operations: password hashing and JSON Web Token (JWT) generation/verification.

### Password Hashing

Passwords are never stored in plain text. We use `bcrypt` for secure hashing.

**Functions:**

| Function | Description |
|----------|-------------|
| `hash_password(password: str) -> str` | Hashes a password using bcrypt with automatic salt generation. Truncates input to 72 bytes. |
| `verify_password(plain_password: str, hashed_password: str) -> bool` | Verifies a plain password against a stored hash |

### JWT Strategy (JSON Web Tokens)

**Functions:**

| Function | Description |
|----------|-------------|
| `create_access_token(data: dict, expires_delta: timedelta = None) -> str` | Creates a signed JWT with a unique JTI and expiration |
| `verify_token(token: str, credentials_exception) -> dict` | Decodes, validates signature, checks expiry, and verifies token is not blacklisted |

**Token Creation Process:**
1. Accepts payload dictionary.
2. Generates a unique `jti` (JWT ID) using `uuid4`.
3. Adds `exp`, `iat`, and `jti` claims.
4. Encodes using `JWT_SECRET_KEY`.

**Token Verification Process:**
1. Decodes token using allowed algorithms (`HS256`).
2. Checks `sub` claim existence.
3. **Blacklist Check:** Calls `is_token_blacklisted(jti)`. If true, raises exception.
4. Returns payload.

---

## Dependencies

**File:** `app/core/dependencies.py`

Provides FastAPI dependency functions for authentication.

### `get_current_user`

**Process:**
1. Extracts Bearer token.
2. Verifies token via `verify_token()`.
3. Queries DB for user ID (`sub`).
4. Checks if user is active.
5. Returns the user object.

### `get_current_tenant`

**Process:**
1. Gets current user via `get_current_user`.
2. Queries DB for user's primary tenant.
3. Returns the tenant object.

**Error Responses:**

| Status Code | Condition |
|-------------|-----------|
| `401 Unauthorized` | Invalid token or token blacklisted |
| `403 Forbidden` | User account is deactivated |
| `404 Not Found` | User not found in database |

---

## IAM Permissions

**File:** `app/core/iam/`

This module implements the Identity and Access Management (IAM) system with permission-based access control (PBAC).

### Core Concepts

Instead of fixed roles, the platform uses a permission-based system:
- **Permissions** are granular actions (e.g., `"vm:create"`)
- **Roles** are collections of permissions
- **UserRoles** assign roles to users for specific tenants
- **Super Admin** has system-wide access (tenant_id=NULL)

### Permission Strings

| Permission | Description |
|-------------|-------------|
| `vm:create` | Create new VMs |
| `vm:read` | View VM details and list |
| `vm:update` | Modify, start, stop, restart VMs |
| `vm:delete` | Delete VMs |
| `network:create` | Create tenant networks |
| `network:read` | View network details |
| `network:delete` | Delete tenant networks |
| `firewall:create` | Create firewall rules |

### Core Functions

**File:** `app/core/iam/__init__.py`

| Function | Description |
|----------|-------------|
| `has_permission(user, tenant_id, permission, db)` | Check if user has a specific permission |
| `has_any_permission(user, tenant_id, permissions, db)` | Check if user has ANY of the permissions |
| `has_all_permissions(user, tenant_id, permissions, db)` | Check if user has ALL permissions |
| `is_tenant_admin(user, tenant_id, db)` | Check if user is tenant admin |
| `is_super_admin(user, db)` | Check if user has super_admin role |

### Permission Logic

```python
def has_permission(user: User, tenant_id: int, required_permission: str, db: Session) -> bool:
    user_permissions = get_user_permissions(user, tenant_id, db)
    
    if "*" in user_permissions:
        return True  # Super admin wildcard
    
    resource, action = parse_permission(required_permission)
    
    for perm in user_permissions:
        perm_resource, perm_action = parse_permission(perm)
        
        if perm_resource == resource:
            if perm_action == action or perm_action == "*":
                return True
    
    return False
```

### Dependency Classes

#### `RequirePermission`

```python
# Usage Example
@app.get("/vms")
def list_vms(
    current_user: User = Depends(RequirePermission("vm:read"))
):
    return db.query(VM).all()
```

#### `RequireTenantAdmin`

```python
# Usage Example
@app.patch("/tenants/{id}")
def update_tenant(
    current_user: User = Depends(RequireTenantAdmin())):
    # User is tenant admin
```

### Super Admin vs Tenant Admin

| Role | Tenant Scope | Access |
|------|-------------|-------|
| `super_admin` | NULL (system-wide) | All tenants, read-only, cannot access logs |
| `tenant_admin` | Per-tenant | Full access within tenant |

---

## Rate Limiting

**File:** `app/core/rate_limit.py`

Implements rate limiting to protect authentication endpoints from brute-force attacks.

### Strategies

1. **InMemoryRateLimiter:** Used in development/testing. Thread-safe, uses a sliding window algorithm.
2. **RedisRateLimiter:** Used in production if `CELERY_BROKER_URL` points to Redis. Allows distributed rate limiting.

### Fail-Closed Behavior

In production environments, availability of the rate limiter is critical for security. The `RedisRateLimiter` respects the `RATE_LIMIT_FAIL_CLOSED` setting:

- **Enabled (Default/Recommended):** If Redis is unreachable, the request is rejected with `503 Service Unavailable`.
- **Disabled:** If Redis is unreachable, the system falls back to in-memory rate limiter.

### Usage

```python
from app.core.rate_limit import check_rate_limit

@router.post("/login")
def login(request: Request, ...):
    check_rate_limit(request, endpoint="login")
    # ... logic ...
```

**Behavior:**
- Identifies clients by `X-Forwarded-For` header or client IP.
- Returns `429 Too Many Requests` if limit exceeded.
- Returns `503 Service Unavailable` if Redis is down and `RATE_LIMIT_FAIL_CLOSED` is True.

---

## Token Blacklist

**File:** `app/core/token_blacklist.py`

Manages a blacklist of revoked JWTs to support logout functionality.

### Implementation

- **Storage:** Uses Redis if available (distributed); otherwise falls back to an in-memory dictionary.
- **Key:** The `jti` (JWT ID) claim of the token.
- **TTL:** Blacklisted tokens are automatically purged after their natural expiration time.

### Functions

| Function | Description |
|----------|-------------|
| `add_token_to_blacklist(jti, expires_at)` | Adds a token's ID to the blacklist |
| `is_token_blacklisted(jti) -> bool` | Checks if a token ID is blacklisted |

---

## Custom Exceptions

**File:** `app/core/exceptions.py`

### Exception Classes

| Exception | Description |
|-----------|-------------|
| `ResourceNotFoundError` | Resource not found (404) |
| `ResourceConflictError` | Resource conflict (409) |
| `InvalidStateTransitionError` | Invalid state transition (400) |
| `ProviderUnavailableError` | Provider unavailable (503) |
| `QuotaExceededError` | Quota exceeded (400) |

---

## Background Tasks (Celery)

**Location:** `app/workers/`

The Celery application and tasks are now defined in `app/workers/` directory.

### Celery App

**File:** `app/workers/celery_app.py`

```python
from app.workers.celery_app import celery_app
```

### Task Scheduler

**File:** `app/workers/task_scheduler.py`

Central task dispatch for:
- VM deployment
- VM provisioning
- Firewall application
- Tenant provisioning

### Task Location

Tasks are organized in `app/workers/tasks/`:
- `vm.py` - VM deploy and cloud-init provisioning
- `tenant.py` - Tenant provisioning and destruction (705 lines)
- `network.py` - Network deploy/destroy tasks
- `firewall_manager.py` - Firewall sync, apply, reconcile (620 lines)
- `vlan.py` - VLAN provisioning via in-VM PHP (457 lines)
- `wireguard.py` - WireGuard tunnel/peer provisioning (654 lines)
- `kea.py` - Kea DHCP configuration
- `images.py` - Image build and template conversion
- `helpers.py` - Shared utilities, Proxmox/OPNsense clients