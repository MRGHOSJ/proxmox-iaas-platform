# Admin API Documentation

This module provides administrative tools for managing the platform infrastructure, including pod management, tenant verification, quota management, and infrastructure auditing.

**Note:** Access to these endpoints is restricted to users with the **Super Admin** IAM role.

## Table of Contents
- [Pod Management](#pod-management)
- [Tenant Verification](#tenant-verification)
- [Quota Management](#quota-management)
- [Infrastructure Audit](#infrastructure-audit)
- [Audit Logs](#audit-logs)
- [Impersonation](#impersonation)
- [VM Status Override](#vm-status-override)
- [System Resources](#system-resources)
- [System Health](#system-health)
- [Recent Activity](#recent-activity)
- [Authorization](#authorization)

---

## Authorization

All endpoints in this module require **Super Admin** IAM role.

| Role | Pods | Tenants | Quotas | Audit |
|------|------|--------|--------|-------|
| `super_admin` | ✅ | ✅ | ✅ | ✅ |

---

## Pod Management

### 1. List Pods

Retrieves all available pods.

**Endpoint:** `GET /admin/pods/`  
**Authorization:** Super Admin only

**Success Response:** `200 OK`
```json
{
  "total": 2,
  "pods": [
    {
      "id": 1,
      "name": "pod-01",
      "provider_type": "proxmox",
      "node_names": "pve01,pve02",
      "max_tenants": 100,
      "tenant_count": 25,
      "status": "active"
    },
    {
      "id": 2,
      "name": "pod-02",
      "provider_type": "proxmox",
      "node_names": "pve03",
      "max_tenants": 50,
      "tenant_count": 10,
      "status": "active"
    }
  ]
}
```

---

### 2. Get Pod Details

Retrieves detailed information for a specific pod.

**Endpoint:** `GET /admin/pods/{pod_id}`  
**Authorization:** Super Admin only

**URL Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `pod_id` | integer | Pod ID |

**Success Response:** `200 OK`
```json
{
  "id": 1,
  "name": "pod-01",
  "provider_type": "proxmox",
  "node_names": "pve01,pve02",
  "max_tenants": 100,
  "tenant_count": 25,
  "status": "active"
}
```

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `404 Not Found` | Pod not found |

---

### 3. Create Pod

Creates a new pod.

**Endpoint:** `POST /admin/pods/`  
**Authorization:** Super Admin only

**Request Body:**
```json
{
  "name": "pod-03",
  "provider_type": "proxmox",
  "node_names": "pve04,pve05",
  "max_tenants": 100
}
```

**Parameters:**
| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| `name` | string | Yes | Unique | Pod name |
| `provider_type` | string | Yes | `proxmox`, `vsphere`, `kvm`, `hyperv` | Provider type |
| `node_names` | string | No | Comma-separated | Node names |
| `max_tenants` | integer | No | Default: 100 | Max tenants |

**Logic Flow:**
1. Validate name uniqueness
2. Validate provider_type
3. Create Pod record
4. Auto-seed VLAN pool for the pod

**Success Response:** `201 Created`
```json
{
  "id": 3,
  "name": "pod-03",
  "provider_type": "proxmox",
  "node_names": "pve04,pve05",
  "max_tenants": 100,
  "tenant_count": 0,
  "status": "active"
}
```

---

### 4. Update Pod

Updates pod configuration.

**Endpoint:** `PATCH /admin/pods/{pod_id}`  
**Authorization:** Super Admin only

**Request Body:**
```json
{
  "node_names": "pve01,pve02,pve03",
  "max_tenants": 150,
  "status": "maintenance"
}
```

**Updatable Fields:**
- `node_names` - Comma-separated node names
- `max_tenants` - Maximum tenants
- `status` - `active`, `maintenance`, `offline`

**Success Response:** `200 OK`

---

### 5. Delete Pod

Deletes a pod.

**Endpoint:** `DELETE /admin/pods/{pod_id}`  
**Authorization:** Super Admin only

**Success Response:** `204 No Content`

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `400 Bad Request` | Pod has active tenants |
| `404 Not Found` | Pod not found |

---

## Tenant Verification

### 6. List Unverified Tenants

Retrieves all unverified tenants.

**Endpoint:** `GET /tenants/unverified`  
**Authorization:** Super Admin only

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `skip` | integer | `0` | Pagination offset |
| `limit` | integer | `100` | Results limit |

**Success Response:** `200 OK`
```json
[
  {
    "id": 5,
    "name": "New Corp",
    "slug": "new-corp",
    "is_active": true,
    "is_verified": false,
    "status": "pending_approval",
    "user_count": 1,
    "vm_count": 0,
    "network_count": 0
  }
]
```

---

### 7. Verify Tenant

Verifies a tenant and initiates provisioning.

**Endpoint:** `POST /tenants/{tenant_id}/verify`  
**Authorization:** Super Admin only

**Request Body:**
```json
{
  "quota": {
    "max_vms": 10,
    "max_cpu_cores": 32,
    "max_ram_mb": 65536,
    "max_disk_gb": 500,
    "max_networks": 5
  }
}
```

**Parameters:**
| Field | Type | Description |
|-------|------|-------------|
| `quota.max_vms` | integer | Maximum VMs |
| `quota.max_cpu_cores` | integer | Maximum CPU cores |
| `quota.max_ram_mb` | integer | Maximum RAM in MB |
| `quota.max_disk_gb` | integer | Maximum disk in GB |
| `quota.max_networks` | integer | Maximum networks |

**Logic Flow:**
1. Verify tenant exists
2. Check tenant is not already verified
3. If tenant in ERROR state, reset to PENDING_APPROVAL
4. Call `approve_tenant()` to provision OPNsense VM
5. Set tenant as verified
6. Save quota settings
7. Log audit event

**Success Response:** `200 OK`
```json
{
  "id": 5,
  "name": "New Corp",
  "slug": "new-corp",
  "is_active": true,
  "is_verified": true,
  "status": "provisioning",
  "bridge_id": 3,
  "opnsense_vm_id": 101,
  "opnsense_vm_name": "opnsense-new-corp",
  "lan_ip": "10.0.3.1",
  "wan_ip": "203.0.113.10"
}
```

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `400 Bad Request` | Tenant already verified |
| `404 Not Found` | Tenant not found |

---

### 8. Get Tenant Quota

Retrieves quota settings and current usage for a tenant.

**Endpoint:** `GET /tenants/{tenant_id}/quota`  
**Authorization:** Super Admin or Tenant Admin

**Success Response:** `200 OK`
```json
{
  "tenant_id": 1,
  "quota": {
    "max_vms": 10,
    "max_cpu_cores": 32,
    "max_ram_mb": 65536,
    "max_disk_gb": 500,
    "max_networks": 5
  },
  "current_usage": {
    "vm_count": 5,
    "cpu_cores": 8,
    "ram_mb": 16384,
    "disk_gb": 100,
    "network_count": 2
  }
}
```

---

### 9. Update Tenant Quota

Updates quota settings for a tenant.

**Endpoint:** `PATCH /tenants/{tenant_id}/quota`  
**Authorization:** Super Admin only

**Request Body:**
```json
{
  "max_vms": 20,
  "max_cpu_cores": 64,
  "max_ram_mb": 131072
}
```

**Success Response:** `200 OK`
```json
{
  "tenant_id": 1,
  "quota": {
    "max_vms": 20,
    "max_cpu_cores": 64,
    "max_ram_mb": 131072,
    "max_disk_gb": 500,
    "max_networks": 5
  }
}
```

---

## Infrastructure Audit

### 10. Audit Infrastructure

Performs read-only comparison between Database and Infrastructure state.

**Endpoint:** `GET /admin/audit`  
**Authorization:** Super Admin only

**Response:** `200 OK`

Returns report containing:
- `orphans`: Containers in infrastructure not in DB
- `ghosts`: VMs in DB not in infrastructure
- `drift`: VMs with mismatched status
- `synced`: Aligned VMs

```json
{
  "orphans": [
    { "name": "lost_container", "status": "stopped" }
  ],
  "ghosts": [
    { "vm_id": 5, "name": "db-server-01", "db_status": "running" }
  ],
  "drift": [
    { 
      "vm_id": 2, 
      "name": "web-01", 
      "db_status": "running", 
      "real_status": "stopped" 
    }
  ],
  "synced": ["cache-01"]
}
```

---

### 11. Reconcile Infrastructure

Performs automatic repair actions.

**Endpoint:** `POST /admin/reconcile`  
**Authorization:** Super Admin only

**Actions:**
1. Purge Orphans: Remove containers not in DB
2. Purge Ghosts: Delete VM records not in infrastructure
3. Correct Drift: Update DB status to match infrastructure

**Success Response:** `200 OK`
```json
{
  "status": "reconciliation_complete",
  "actions_taken": {
    "orphan_purged": ["lost_container"],
    "ghost_purged": [],
    "drift_corrected": [
      {
        "name": "web-01",
        "old_status": "running",
        "new_status": "stopped"
      }
    ]
  }
}
```

---

### 12. Fix VM

Repairs a specific VM in error state.

**Endpoint:** `POST /admin/fix/{vm_id}`  
**Authorization:** Super Admin only

**URL Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `vm_id` | integer | VM ID to fix |

**Success Response:** `200 OK`
```json
{
  "status": "success",
  "message": "Re-provision task dispatched successfully",
  "vm_id": 5,
  "vm_name": "db-server-01"
}
```

---

## Audit Logs

### 13. Query Audit Logs

Query audit logs with filtering by action, target type, actor, date range, and more. Super admins see all logs system-wide; tenant admins see only their tenant's logs.

**Endpoint:** `GET /admin/audit-logs`  
**Authorization:** Super Admin or user with `audit:read` permission

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `action` | string | `null` | Filter by action type |
| `target_type` | string | `null` | Filter by target type (user, vm, network) |
| `actor_id` | integer | `null` | Filter by actor user ID |
| `target_id` | integer | `null` | Filter by target ID |
| `start_date` | datetime | `null` | Filter by start date |
| `end_date` | datetime | `null` | Filter by end date |
| `skip` | integer | `0` | Pagination offset |
| `limit` | integer | `100` | Maximum records (1-1000) |

**Success Response:** `200 OK`
```json
{
  "total": 42,
  "logs": [
    {
      "id": 1,
      "timestamp": "2025-01-15T10:30:00Z",
      "action": "vm.create",
      "target_type": "vm",
      "target_id": 5,
      "target_name": "web-01",
      "actor_id": 1,
      "actor_username": "admin",
      "old_value": null,
      "new_value": "cpu=2,ram=4096",
      "details": "VM created by admin",
      "ip_address": "192.168.1.100",
      "request_id": "req-abc-123",
      "tenant_id": 1,
      "impersonated_by": null
    }
  ],
  "skip": 0,
  "limit": 100
}
```

---

### 14. Get Specific Audit Log

Retrieves a single audit log entry by ID.

**Endpoint:** `GET /admin/audit-logs/{log_id}`  
**Authorization:** Super Admin or user with `audit:read` for the log's tenant

**URL Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `log_id` | integer | Audit log ID |

**Success Response:** `200 OK`
```json
{
  "id": 1,
  "timestamp": "2025-01-15T10:30:00Z",
  "action": "vm.create",
  "target_type": "vm",
  "target_id": 5,
  "target_name": "web-01",
  "actor_id": 1,
  "actor_username": "admin",
  "old_value": null,
  "new_value": "cpu=2,ram=4096",
  "details": "VM created by admin",
  "ip_address": "192.168.1.100",
  "request_id": "req-abc-123",
  "tenant_id": 1,
  "impersonated_by": null
}
```

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `404 Not Found` | Audit log not found |
| `403 Forbidden` | Access denied (tenant mismatch) |

---

## Impersonation

### 15. Start Impersonation

Logs the start of a super admin impersonation session for a target tenant.

**Endpoint:** `POST /admin/impersonate/start`  
**Authorization:** Super Admin only

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `tenant_id` | integer | Target tenant ID to impersonate |

**Success Response:** `200 OK`
```json
{
  "status": "success",
  "message": "Impersonation of tenant 'Acme Corp' started",
  "tenant_id": 1,
  "tenant_name": "Acme Corp",
  "admin_username": "superadmin"
}
```

---

### 16. End Impersonation

Logs the end of a super admin impersonation session.

**Endpoint:** `POST /admin/impersonate/end`  
**Authorization:** Super Admin only

**Query Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `tenant_id` | integer | Target tenant ID that was impersonated |

**Success Response:** `200 OK`
```json
{
  "status": "success",
  "message": "Impersonation of tenant 'Acme Corp' ended",
  "tenant_id": 1,
  "tenant_name": "Acme Corp",
  "admin_username": "superadmin"
}
```

---

## VM Status Override

### 17. Override VM Status

Admin-only endpoint to override VM status, bypassing normal state machine validation. Use `force=true` to skip transition checks. All overrides are logged for audit.

**Endpoint:** `PATCH /admin/vm/{vm_id}/status`  
**Authorization:** Super Admin or Tenant Admin

**URL Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `vm_id` | integer | VM ID |

**Request Body:**
```json
{
  "status": "stopped",
  "reason": "Fixing stuck provisioning state",
  "force": true
}
```

**Parameters:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `status` | string | Yes | Target status |
| `reason` | string | Yes | Reason for override (mandatory for audit) |
| `force` | boolean | No | Bypass state transition validation |

**Success Response:** `200 OK` — Returns `VMResponse` object.

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `400 Bad Request` | Invalid status transition (without force) |
| `404 Not Found` | VM not found |

---

## System Resources

### 18. Get System Resource Usage

Returns system-wide resource usage statistics including per-tenant breakdowns for CPU, RAM, and disk.

**Endpoint:** `GET /admin/resources`  
**Authorization:** Super Admin or Tenant Admin

**Success Response:** `200 OK`
```json
{
  "node": {
    "name": "pve01",
    "cpu_cores_total": 32,
    "cpu_cores_used": 12,
    "cpu_usage_percent": 37.5,
    "ram_gb_total": 128,
    "ram_gb_used": 48,
    "disk_gb_total": 2000,
    "disk_gb_used": 800,
    "vm_count": 15
  },
  "totals": {
    "cpu_cores_total": 32,
    "cpu_cores_used": 12,
    "ram_gb_total": 128,
    "ram_gb_used": 48,
    "disk_gb_total": 2000,
    "disk_gb_used": 800,
    "vm_count_total": 15
  },
  "by_tenant": [
    {
      "tenant_id": 1,
      "tenant_name": "Acme Corp",
      "vm_count": 10,
      "cpu_cores_used": 8,
      "ram_gb_used": 32,
      "disk_gb_used": 500
    },
    {
      "tenant_id": 2,
      "tenant_name": "Beta Inc",
      "vm_count": 5,
      "cpu_cores_used": 4,
      "ram_gb_used": 16,
      "disk_gb_used": 300
    }
  ]
}
```

---

## System Health

### 19. Get Infrastructure Health

Returns health status for database, Redis, and Proxmox components.

**Endpoint:** `GET /admin/health`  
**Authorization:** Super Admin or Tenant Admin

**Success Response:** `200 OK`
```json
{
  "overall": "healthy",
  "components": {
    "database": {
      "status": "healthy",
      "message": "Connected"
    },
    "redis": {
      "status": "healthy",
      "message": "Connected"
    },
    "proxmox": {
      "status": "healthy",
      "message": "15 VMs"
    }
  }
}
```

**Status values:**
- `overall`: `healthy` (all components ok) or `degraded` (one or more unhealthy)
- `components.*.status`: `healthy`, `unhealthy`, or `unknown`

---

## Recent Activity

### 20. Get Recent Activity Feed

Returns recent system activity including audit events, VM creations, and tenant events.

**Endpoint:** `GET /admin/activity`  
**Authorization:** Super Admin or Tenant Admin

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `limit` | integer | `10` | Max results (1-50) |

**Success Response:** `200 OK`
```json
{
  "activities": [
    {
      "type": "audit",
      "timestamp": "2025-01-15T10:30:00Z",
      "action": "vm.create",
      "target_type": "vm",
      "target_name": "web-01",
      "actor_username": "admin"
    },
    {
      "type": "vm",
      "timestamp": "2025-01-15T09:00:00Z",
      "action": "created",
      "target_type": "vm",
      "target_name": "db-01",
      "status": "pending",
      "provider": "proxmox"
    },
    {
      "type": "tenant",
      "timestamp": "2025-01-14T15:00:00Z",
      "action": "created",
      "target_type": "tenant",
      "target_name": "New Corp",
      "status": "active"
    }
  ],
  "count": 3
}
```

---

## Tenant Management

### 21. List Tenants

Lists all tenants.

**Endpoint:** `GET /tenants/`  
**Authorization:** Super Admin only

**Success Response:** `200 OK`
```json
[
  {
    "id": 1,
    "name": "System",
    "slug": "system",
    "is_active": true,
    "is_verified": true,
    "status": "active",
    "user_count": 1,
    "vm_count": 0,
    "network_count": 1
  }
]
```

---

### 22. Get Tenant Details

Gets tenant with statistics.

**Endpoint:** `GET /tenants/{tenant_id}`  
**Authorization:** Super Admin

**Success Response:** `200 OK`
```json
{
  "id": 5,
  "name": "New Corp",
  "slug": "new-corp",
  "is_active": true,
  "is_verified": true,
  "status": "active",
  "user_count": 3,
  "vm_count": 5,
  "network_count": 2,
  "vm_status_breakdown": {
    "running": 3,
    "stopped": 1,
    "error": 1
  },
  "total_cpu": 8,
  "total_ram": 16384,
  "total_disk": 100,
  "vm_provider_breakdown": {
    "proxmox": 5
  }
}
```

---

### 23. Create Tenant

Creates a new tenant.

**Endpoint:** `POST /tenants/`  
**Authorization:** Super Admin only

**Request Body:**
```json
{
  "name": "Enterprise Inc",
  "slug": "enterprise-inc"
}
```

**Success Response:** `201 Created`

---

### 24. Update Tenant

Updates tenant.

**Endpoint:** `PATCH /tenants/{tenant_id}`  
**Authorization:** Super Admin only

**Request Body:**
```json
{
  "name": "Enterprise Corp",
  "is_active": true
}
```

---

### 25. Delete Tenant

Deletes a tenant (must have no users or VMs).

**Endpoint:** `DELETE /tenants/{tenant_id}`  
**Authorization:** Super Admin only

**Success Response:** `204 No Content`

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `400 Bad Request` | Cannot delete tenant with users/VMs |

---

## Error Handling

### Common Errors

**1. Super Admin Required**
```json
{
  "detail": "Super admin access required"
}
```
Status: `403 Forbidden`

**2. Tenant Not Found**
```json
{
  "detail": "Tenant not found"
}
```
Status: `404 Not Found`

**3. Pod Not Found**
```json
{
  "detail": "Pod not found"
}
```
Status: `404 Not Found`

**4. Cannot Delete Pod with Tenants**
```json
{
  "detail": "Cannot delete pod with 5 active tenant(s). Please reassign or delete tenants first."
}
```
Status: `400 Bad Request`

**5. Tenant Already Verified**
```json
{
  "detail": "Tenant is already verified"
}
```
Status: `400 Bad Request`

**6. Cannot Ban Super Admin**
```json
{
  "detail": "Cannot ban a super admin"
}
```
Status: `400 Bad Request`