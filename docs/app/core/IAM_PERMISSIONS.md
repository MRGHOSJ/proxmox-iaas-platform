# IAM Permissions Documentation

This document describes the Identity and Access Management (IAM) permission system in detail.

---

## Table of Contents
- [Overview](#overview)
- [Permission System](#permission-system)
- [Role System](#role-system)
- [Usage in Code](#usage-in-code)
- [Dependency Classes](#dependency-classes)

---

## Overview

The platform uses **Permission-Based Access Control (PBAC)** instead of traditional Role-Based Access Control (RBAC).

### Key Differences

| RBAC (Old) | PBAC (New) |
|-----------|-----------|
| Fixed roles: admin, vm_operator | Granular permissions: vm:create, vm:read |
| Role assigned to user directly | Role assigned to user, role contains permissions |
| Single role per user | Multiple roles via UserRole |
| Single tenant scope | Per-tenant scope |

---

## Permission System

### Permission Strings

Permissions follow the format: `<resource>:<action>`

| Permission | Resource | Action | Description |
|-------------|----------|--------|-------------|
| `vm:create` | vm | create | Create new VMs |
| `vm:read` | vm | read | View VM details and list |
| `vm:update` | vm | update | Modify, start, stop, restart VMs |
| `vm:delete` | vm | delete | Delete VMs |
| `network:create` | network | create | Create tenant networks |
| `network:read` | network | read | View network details |
| `network:delete` | network | delete | Delete tenant networks |
| `firewall:create` | firewall | create | Create firewall rules |

### Wildcard Permissions

- `*` - All permissions (super_admin only)
- `vm:*` - All VM permissions

---

## Role System

### Role Types

| Role | Type | Scope | Description |
|------|------|------|-------------|
| `super_admin` | System | NULL (tenant_id) | System-wide access |
| `tenant_admin` | Preset | Per-tenant | Full tenant access |
| `vm_operator` | Preset | Per-tenant | Manage own VMs |
| `viewer` | Preset | Per-tenant | Read-only access |

### Role Hierarchy

```
super_admin (tenant_id=NULL)
    │
    └── All permissions (*)

tenant_admin (tenant_id=X)
    │
    └── vm:create, vm:read, vm:update, vm:delete
    └── network:create, network:read, network:delete
    └── firewall:create

vm_operator (tenant_id=X)
    │
    └── vm:create, vm:read, vm:update

viewer (tenant_id=X)
    │
    └── vm:read, network:read
```

### UserRole Assignment

Users can have roles in multiple tenants:

| user_id | tenant_id | role_id | granted_by |
|---------|-----------|---------|------------|
| 1 | NULL | 1 (super_admin) | NULL |
| 1 | 1 | 2 (tenant_admin) | 1 |
| 2 | 1 | 3 (vm_operator) | 1 |
| 2 | 2 | 3 (vm_operator) | 5 |

---

## Usage in Code

### Checking Permissions

```python
from app.core.iam import has_permission

@app.post("/vm/create")
def create_vm(
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant),
    db: Session = Depends(get_db)
):
    if not has_permission(current_user, current_tenant.id, "vm:create", db):
        raise HTTPException(403, detail="vm:create permission required")
```

### Checking Multiple Permissions

```python
from app.core.iam import has_any_permission

# User can create OR delete VMs
if has_any_permission(user, tenant_id, ["vm:create", "vm:delete"], db):
    pass
```

### Checking Super Admin

```python
from app.core.iam import is_super_admin

if is_super_admin(current_user, db):
    # Can access all tenants
    pass
```

### Checking Tenant Admin

```python
from app.core.iam import is_tenant_admin

if is_tenant_admin(current_user, tenant_id, db):
    # Can manage tenant settings
    pass
```

---

## Dependency Classes

### RequirePermission

Enforces a specific permission:

```python
from app.core.iam import RequirePermission

@app.get("/vms")
def list_vms(
    current_user: User = Depends(RequirePermission("vm:read"))
):
    return db.query(VM).all()
```

### RequireAnyPermission

Enforces any of the specified permissions:

```python
from app.core.iam import RequireAnyPermission

@app.post("/vm/create")
def create_vm(
    vm_data: VMCreate,
    current_user: User = Depends(RequireAnyPermission(["vm:create", "vm:delete"]))
):
    return create_vm_logic(vm_data)
```

### RequireTenantAdmin

Requires tenant admin role:

```python
from app.core.iam import RequireTenantAdmin

@app.patch("/tenants/{id}")
def update_tenant(
    tenant_data: TenantUpdate,
    current_user: User = Depends(RequireTenantAdmin())
):
    return update_tenant(tenant_data)
```

---

## Permission Matrix

| Action | Super Admin | Tenant Admin | VM Operator | Viewer |
|-------|:-----------:|:------------:|:-----------:|:------:|
| Create VM | ✅ |✅ | ✅ | ❌ |
| List VMs | ✅ | ✅ | ✅ | ✅ |
| View VM Details | ✅* | ✅ | ✅ Own | ✅ |
| Update VM | ✅ | ✅ | ✅ Own | ❌ |
| Start/Stop VM | ✅ | ✅ | ✅ Own | ❌ |
| Delete VM | ✅ | ✅ | ✅ Own | ❌ |
| View Logs | ❌ | ✅ | ✅ Own | ❌ |
| Create Network | ✅ | ✅ | ❌ | ❌ |
| Delete Network | ✅ | ✅ | ❌ | ❌ |
| Create Firewall | ✅ | ✅ | ✅ Own | ❌ |
| List Tenants | ✅ | ❌ | ❌ | ❌ |
| Verify Tenant | ✅ | ❌ | ❌ | ❌ |

*Super admin can view all VMs but NOT logs (privacy protection)

---

## Database Schema

### Tables

- `permissions` - Permission definitions
- `iam_roles` - Role definitions  
- `user_roles` - User role assignments
- `permission_role` - Many-to-many mapping

### Seeding

On first run, permissions and roles are seeded:

```python
# Permissions
vm:create, vm:read, vm:update, vm:delete
network:create, network:read, network:delete
firewall:create

# Roles
super_admin (system)
tenant_admin (preset)
vm_operator (preset)
viewer (preset)
```

---

## Migration from RBAC

The old role-based system used a single `role` field on User. The new PBAC system:

1. **Removes** the `role` field from User model
2. **Uses** UserRole table for role assignments
3. **Supports** multiple roles per user (different tenants)
4. **Enables** wildcard permissions for super_admin