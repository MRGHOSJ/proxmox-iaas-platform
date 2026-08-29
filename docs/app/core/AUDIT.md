# Audit Logging Documentation

This document describes the audit logging system for tracking sensitive operations.

---

## Table of Contents
- [Overview](#overview)
- [Audit Actions](#audit-actions)
- [Usage](#usage)
- [Audit Log Model](#audit-log-model)

---

## Overview

**File:** `app/core/audit.py`

The audit logging system tracks sensitive operations for security and compliance:
- User authentication (login, logout, failures)
- Resource modifications (VM, network, firewall)
- Administrative actions (tenant approval, role changes)
- Configuration changes

---

## Audit Actions

### Authentication Events

| Action | Description |
|--------|-------------|
| `login` | Successful login |
| `logout` | Successful logout |
| `login_failed` | Failed login attempt |
| `password_change` | Password changed |

### User Events

| Action | Description |
|--------|-------------|
| `user_create` | New user created |
| `user_delete` | User deleted |
| `user_status_change` | User banned/unbanned |
| `user_profile_update` | Profile updated |
| `role_change` | Role assignment changed |

### VM Events

| Action | Description |
|--------|-------------|
| `vm_create` | VM created |
| `vm_start` | VM started |
| `vm_stop` | VM stopped |
| `vm_restart` | VM restarted |
| `vm_delete` | VM deleted |
| `vm_status_override` | Manual status override |
| `vm_snapshot_create` | Snapshot created |
| `vm_snapshot_restore` | Snapshot restored |
| `vm_snapshot_delete` | Snapshot deleted |

### Network Events

| Action | Description |
|--------|-------------|
| `network_create` | Network created |
| `network_delete` | Network deleted |

### Firewall Events

| Action | Description |
|--------|-------------|
| `firewall_rule_create` | Firewall rule created |
| `firewall_rule_update` | Firewall rule updated |
| `firewall_rule_delete` | Firewall rule deleted |
| `firewall_rule_apply` | Firewall rules applied |

### Invitation Events

| Action | Description |
|--------|-------------|
| `invite_create` | Invitation created |
| `invite_accept` | Invitation accepted |
| `invite_revoke` | Invitation revoked |

### Tenant Events

| Action | Description |
|--------|-------------|
| `tenant_create` | Tenant created |
| `tenant_update` | Tenant updated |
| `tenant_delete` | Tenant deleted |
| `tenant_approved` | Tenant verified |
| `tenant_provisioned` | Tenant provisioned |
| `wan_ip_assigned` | WAN IP assigned |
| `wan_ip_changed` | WAN IP changed |

### Admin Events

| Action | Description |
|--------|-------------|
| `admin_action` | Administrative action |
| `reconcile` | Reconciliation performed |

---

## Usage

### Logging an Event

```python
from app.core.audit import log_audit_event, AUDIT_ACTIONS

log_audit_event(
    db=db,
    action=AUDIT_ACTIONS["VM_CREATE"],
    target_type="vm",
    actor_id=user.id,
    actor_username=user.username,
    target_id=vm.id,
    target_name=vm.name,
    new_value=f"name={vm.name},provider={vm.provider}",
    request_id=request_id,
    ip_address=client_ip,
    tenant_id=vm.tenant_id
)
```

### Parameters

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `db` | Session | Yes | Database session |
| `action` | string | Yes | Action from AUDIT_ACTIONS |
| `target_type` | string | Yes | Type: user, vm, network, etc. |
| `actor_id` | int | No | User performing action |
| `actor_username` | string | Yes | Username (or "system") |
| `target_id` | int | No | Target resource ID |
| `target_name` | string | No | Target resource name |
| `old_value` | string | No | Previous value |
| `new_value` | string | No | New value |
| `details` | string | No | Additional details |
| `request_id` | string | No | Request tracing ID |
| `ip_address` | string | No | Client IP |
| `tenant_id` | int | No | Tenant scope |

---

## Audit Log Model

**Table:** `audit_logs`

| Field | Type | Description |
|-------|------|-------------|
| `id` | Integer | PK |
| `action` | String | Audit action |
| `target_type` | String | Resource type |
| `actor_id` | Integer | Acting user |
| `actor_username` | String | Actor username |
| `target_id` | Integer | Target ID |
| `target_name` | String | Target name |
| `old_value` | Text | Previous value |
| `new_value` | Text | New value |
| `details` | Text | Additional details |
| `request_id` | String | Request tracking |
| `ip_address` | String | Client IP |
| `tenant_id` | Integer | Tenant scope |
| `created_at` | DateTime | Timestamp |

---

## Security Features

### Safeguards

1. **Default actor:** If actor is unknown, defaults to "system"
2. **Immutable:** Audit logs cannot be modified
3. **Transaction safety:** On failure, logs error and rolls back
4. **Request tracking:** Uses `X-Request-ID` header

### Logged Information

- Who (actor_id, actor_username)
- What (action, target_type)
- When (created_at)
- Where (ip_address, request_id)
- Which tenant (tenant_id)