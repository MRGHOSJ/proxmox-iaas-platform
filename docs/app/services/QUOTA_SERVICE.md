# Quota Service Documentation

This document describes the quota management service that enforces resource limits per tenant.

---

## Table of Contents
- [Overview](#overview)
- [Quota Settings](#quota-settings)
- [Functions](#functions)
- [Usage](#usage)
- [Error Handling](#error-handling)

---

## Overview

**File:** `app/services/quota.py`

The quota service manages tenant resource limits including VM count, CPU cores, RAM, disk, and networks. Quotas are enforced during VM and network creation.

---

## Quota Settings

### Schema

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_vms` | integer | `null` | Maximum VMs (null = unlimited) |
| `max_cpu_cores` | integer | `null` | Maximum CPU cores |
| `max_ram_mb` | integer | `null` | Maximum RAM in MB |
| `max_disk_gb` | integer | `null` | Maximum disk in GB |
| `max_networks` | integer | `null` | Maximum networks |

### Default Quota

```json
{
  "max_vms": 100,
  "max_cpu_cores": 256,
  "max_ram_mb": 524288,
  "max_disk_gb": 5000,
  "max_networks": 10
}
```

---

## Functions

### get_quota_settings

Retrieves quota settings for a tenant.

```python
def get_quota_settings(tenant_id: int, db: Session) -> QuotaSettings:
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        return QuotaSettings()
    return QuotaSettings.from_settings_json(tenant.settings)
```

### get_current_usage

Calculates current resource usage for a tenant.

```python
def get_current_usage(tenant_id: int, db: Session) -> dict:
    return {
        "vm_count": db.query(VM).filter(VM.tenant_id == tenant_id).count(),
        "cpu_cores": db.query(func.coalesce(func.sum(VM.cpu), 0)).filter(VM.tenant_id == tenant_id).scalar(),
        "ram_mb": db.query(func.coalesce(func.sum(VM.ram), 0)).filter(VM.tenant_id == tenant_id).scalar(),
        "disk_gb": db.query(func.coalesce(func.sum(VM.disk_size), 0)).filter(VM.tenant_id == tenant_id).scalar(),
        "network_count": db.query(TenantNetwork).filter(TenantNetwork.tenant_id == tenant_id).count()
    }
```

### check_vm_quota

Validates VM creation against quota limits.

```python
def check_vm_quota(
    tenant_id: int,
    db: Session,
    cpu: int = 0,
    ram: int = 0,
    disk_size: int = 0
) -> None:
    # Raises QuotaExceededError if exceeded
```

### check_network_quota

Validates network creation against quota limits.

```python
def check_network_quota(tenant_id: int, db: Session) -> None:
    # Raises QuotaExceededError if exceeded
```

### get_quota_status

Returns comprehensive quota status.

```python
def get_quota_status(tenant_id: int, db: Session) -> dict:
    return {
        "quota": quota_settings,
        "usage": current_usage,
        "remaining": {...},
        "percentage": {...}
    }
```

---

## Usage

### Enforcing Quota on VM Creation

```python
from app.services.quota import check_vm_quota, QuotaExceededError

@app.post("/vm/provision")
def provision_vm(
    vm_data: VMProvision,
    current_tenant: Tenant = Depends(get_current_tenant),
    db: Session = Depends(get_db)
):
    try:
        check_vm_quota(
            current_tenant.id,
            db,
            cpu=vm_data.cpu,
            ram=vm_data.ram,
            disk_size=vm_data.disk_size
        )
    except QuotaExceededError as e:
        raise HTTPException(400, detail=str(e))
    
    # Proceed with VM creation
```

### Getting Quota Status

```python
from app.services.quota import get_quota_status

@app.get("/tenants/{id}/quota")
def get_quota(
    tenant_id: int,
    db: Session = Depends(get_db)
):
    return get_quota_status(tenant_id, db)

# Response:
# {
#     "quota": {"max_vms": 10, "max_cpu_cores": 32, ...},
#     "usage": {"vm_count": 5, "cpu_cores": 8, ...},
#     "remaining": {"vm_count": 5, "cpu_cores": 24, ...},
#     "percentage": {"vm_count": 50.0, "cpu_cores": 25.0, ...}
# }
```

---

## Error Handling

### QuotaExceededError

```python
class QuotaExceededError(Exception):
    def __init__(self, resource: str, limit: int, current: int, requested: int):
        self.resource = resource
        self.limit = limit
        self.current = current
        self.requested = requested
        
    def __str__(self):
        return f"Quota exceeded: {resource} limit of {self.limit} reached ({self.current} used, requested {self.requested} more)"
```

### Error Response

```json
{
  "detail": "Quota exceeded: max_vms limit of 10 reached (10 used, requested 1 more)"
}
```

---

## Quota Enforcement Points

| Operation | Check |
|-----------|-------|
| VM Creation | `check_vm_quota()` |
| VM Provisioning | `check_vm_quota()` |
| Network Creation | `check_network_quota()` |
| Quota Update | Admin-only |

---

## Tenant Settings Storage

Quotas are stored in the `Tenant.settings` field as JSON:

```json
{
  "max_vms": 10,
  "max_cpu_cores": 32,
  "max_ram_mb": 65536,
  "max_disk_gb": 500,
  "max_networks": 5
}
```