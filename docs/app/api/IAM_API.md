# IAM (Identity & Access Management) API Documentation

This module provides IAM (Identity and Access Management) functionality including permission checking, role management, and access control.

## Table of Contents
- [Core Concepts](#core-concepts)
- [Permission System](#permission-system)
- [Functions](#functions)
- [Endpoints](#endpoints)

---

## Core Concepts

### Permission-Based Access Control (PBAC)

Instead of fixed roles, the platform uses a permission-based system where:
- **Permissions** are granular actions (e.g., "vm:create")
- **Roles** are collections of permissions
- **UserRoles** assign roles to users for specific tenants

### Permission String Format

```
<resource>:<action>
```

| Resource | Actions |
|----------|----------|
| `vm` | `create`, `read`, `update`, `delete` |
| `network` | `create`, `read`, `delete` |
| `firewall` | `create`, `read`, `update`, `delete` |
| `tenant` | `admin`, `manage` |

---

## Permission System

### Permission Model

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Unique identifier |
| `name` | string | Permission name (e.g., "vm:create") |
| `resource_type` | string | Resource type (e.g., "vm") |
| `action` | string | Action (e.g., "create") |
| `description` | string | Description |

### Role Model

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Unique identifier |
| `name` | string | Role name |
| `description` | string | Description |
| `tenant_id` | integer | Tenant scope (NULL for system roles) |
| `is_preset` | boolean | Is a preset role |
| `is_system` | boolean | Is a system role |

### UserRole Model

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Unique identifier |
| `user_id` | integer | User reference |
| `tenant_id` | integer | Tenant scope (NULL for super_admin) |
| `role_id` | integer | Role reference |
| `granted_by` | integer | User who granted this role |
| `created_at` | datetime | Assignment timestamp |

---

## Functions

### Permission Checking

#### `has_permission(user, tenant_id, permission, db)`

Checks if a user has a specific permission.

```python
from app.core.iam import has_permission

if has_permission(user, tenant_id, "vm:create", db):
    # User can create VMs
```

**Logic:**
1. If user has wildcard (`*`) permission, return True
2. Parse permission into resource and action
3. Check if user has the exact permission OR wildcard (`*`) for that resource

---

#### `has_any_permission(user, tenant_id, permissions, db)`

Checks if user has ANY of the listed permissions.

```python
if has_any_permission(user, tenant_id, ["vm:create", "vm:delete"], db):
    # User can create OR delete VMs
```

---

#### `has_all_permissions(user, tenant_id, permissions, db)`

Checks if user has ALL of the listed permissions.

```python
if has_all_permissions(user, tenant_id, ["vm:create", "vm:read"], db):
    # User must have both permissions
```

---

### Role Checking

#### `is_tenant_admin(user, tenant_id, db)`

Checks if user is admin for a tenant.

```python
from app.core.iam import is_tenant_admin

if is_tenant_admin(user, tenant_id, db):
    # User is tenant admin
```

**Logic:**
1. If user is super_admin, return True
2. Check if user has "tenant_admin" role in the tenant

---

#### `is_super_admin(user, db)`

Checks if user has super_admin IAM role.

```python
from app.core.iam import is_super_admin

if is_super_admin(user, db):
    # User has system-wide admin access
```

**Logic:**
1. Look for UserRole with tenant_id=NULL
2. Check if role is system role named "super_admin"

---

### Dependency Classes

#### `RequirePermission(permission, allow_super_admin=True)`

FastAPI dependency for requiring a permission.

```python
from app.core.iam import RequirePermission

@app.get("/vms")
def list_vms(
    current_user: User = Depends(RequirePermission("vm:read"))
):
    return db.query(VM).all()
```

---

#### `RequireTenantAdmin(allow_super_admin=True)`

FastAPI dependency for requiring tenant admin.

```python
from app.core.iam import RequireTenantAdmin

@app.patch("/tenants/{id}")
def update_tenant(
    current_user: User = Depends(RequireTenantAdmin()))
):
    # User is tenant admin
```

---

## Endpoint Authorization Matrix

| Permission | Description |
|------------|-------------|
| `vm:create` | Create new VMs |
| `vm:read` | View VM details and list |
| `vm:update` | Modify, start, stop VMs |
| `vm:delete` | Delete VMs |
| `network:create` | Create networks |
| `network:read` | View networks |
| `network:delete` | Delete networks |
| `firewall:create` | Create firewall rules |

---

## Predefined Roles

### System Roles (tenant_id=NULL)

| Role | Permissions | Description |
|------|-------------|--------------|
| `super_admin` | `*` (all) | System-wide admin access |

### Tenant Preset Roles

| Role | Permissions | Description |
|------|-------------|--------------|
| `tenant_admin` | All VM/network/firewall | Full tenant access |
| `vm_operator` | `vm:create`, `vm:read`, `vm:update` | Manage own VMs |
| `viewer` | `vm:read`, `network:read` | Read-only access |

---

## Wildcard Permissions

### Super Admin Wildcard

Users with `super_admin` role have wildcard (`*`) permission, granting access to everything:

```python
def get_user_permissions(user, tenant_id, db):
    if is_super_admin(user, db):
        return ["*"]  # All permissions
```

When checking with wildcard:
- `"vm"*` matches `"vm:create"`, `"vm:read"`, etc.
- `"*"` matches everything

---

## Usage Examples

### Checking VM Creation Permission

```python
from app.core.iam import has_permission

@app.post("/vm/create")
def create_vm(
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant),
    db: Session = Depends(get_db)
):
    if not has_permission(current_user, current_tenant.id, "vm:create", db):
        raise HTTPException(status_code=403, detail="vm:create permission required")
    
    # Create VM
```

### Using Dependency Class

```python
from app.core.iam import RequirePermission

@app.post("/vm/create")
def create_vm(
    vm_data: VMCreate,
    current_user: User = Depends(RequirePermission("vm:create"))
):
    # User already validated
    # Create VM
```

---

## Error Handling

### Permission Denied

```json
{
  "detail": "Permission denied: vm:create required"
}
```
Status: `403 Forbidden`

### Super Admin Required

```json
{
  "detail": "Super admin access required"
}
```
Status: `403 Forbidden`

### Tenant Admin Required

```json
{
  "detail": "Tenant admin access required"
}
```
Status: `403 Forbidden`