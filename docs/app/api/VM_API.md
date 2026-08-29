# VM CRUD API Documentation


This document describes the complete VM lifecycle management API for the platform. This API supports multi-tenant isolation, permission-based access control, and multiple virtualization providers.

## Table of Contents
1. [Core Concepts](#core-concepts)
2. [Authorization](#authorization)
3. [API Reference](#api-reference)
4. [Data Models \& Schemas](#data-models--schemas)
5. [Error Handling](#error-handling)

---

## Authentication

All endpoints within this module are protected and require a valid **JSON Web Token (JWT)**.

**How to Authenticate:**
Include the token in the `Authorization` header with the `Bearer` scheme.

```http
Authorization: Bearer <your_jwt_token>
```

---

## Core Concepts

### Multi-Tenant Architecture
The platform operates as a multi-tenant system where:
- Every VM belongs to a **Tenant** (organization)
- Every User belongs to a primary Tenant
- Users can access VMs only within their tenant (unless super_admin)
- Tenants have quotas limiting CPU, RAM, disk, and VM count

### VM Status Lifecycle
A VM transitions through the following states during its lifecycle:

| Status | Description |
| :--- | :--- |
| `creating` | Database record created. Task dispatch pending. |
| `pending` | VM is queued in the database. Background worker accepted the job. |
| `provisioning` | Terraform/Proxmox is currently creating infrastructure. |
| `running` | VM is active, accessible, and infrastructure is stable. |
| `stopped` | VM is powered off. Infrastructure exists but the VM is halted. |
| `error` | An error occurred during provisioning or lifecycle operation failed. |

### Valid State Transitions

| Current Status | Allowed Transitions To |
| :--- | :--- |
| `creating` | `pending`, `provisioning`, `error` |
| `pending` | `creating`, `provisioning`, `running`, `error` |
| `provisioning` | `running`, `error` |
| `running` | `stopped`, `error` |
| `stopped` | `running`, `error` |
| `error` | `pending`, `stopped` |

### Providers
The platform supports multiple virtualization providers, defined by the `provider` field.

| Provider | Description | Use Case |
|----------|-------------|----------|
| `proxmox` | **Primary** - KVM-based virtualization | Production workloads |
| `docker` | Lightweight, container-based | **DEPRECATED** - Use Proxmox |
| `vsphere` | Full VMware-based virtualization | Future support |

**Important:** Docker network provisioning is no longer supported. Use Proxmox provider with TenantNetworks.

---

## Authorization

### Permission-Based Access Control (IAM)

The platform uses a permission-based system instead of fixed roles. Permissions control access to specific actions.

### Permission Strings

| Permission | Description |
|------------|-------------|
| `vm:create` | Create new VMs |
| `vm:read` | View VM details and list |
| `vm:update` | Modify, start, stop, restart VMs |
| `vm:delete` | Delete VMs |
| `vm:console` | Access VM console (VNC/serial) |
| `firewall:create` | Create firewall rules |
| `network:create` | Create tenant networks |
| `network:delete` | Delete tenant networks |

### Ownership Model

VMs are scoped to tenants. Access permissions:

| Scenario | Access |
|----------|--------|
| User owns the VM in their tenant | Full control (view, modify, lifecycle, delete) |
| User has `vm:delete` or `vm:update` permission in tenant | Can manage ALL VMs in tenant |
| User has `super_admin` IAM role | Cross-tenant access (read-only, cannot access logs/console) |
| Regular user | Can only manage their OWN VMs |

### Endpoint Authorization Matrix

| Endpoint | Super Admin | Tenant Admin | VM Manager | VM Owner | Viewer |
|----------|:----------:|:-----------:|:-----------:|:--------:|:------:|
| `POST /vm/create` | ✅ | ✅ | ✅ | ❌ | ❌ |
| `POST /vm/provision` | ✅ | ✅ | ✅ | ❌ | ❌ |
| `GET /vm/list` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `GET /vm/stats/summary` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `GET /vm/{id}` | ✅* | ✅ | ✅ | ✅ | ✅ |
| `PATCH /vm/{id}` | ✅ | ✅ | ✅ | ✅ Own only | ❌ |
| `POST /vm/{id}/start` | ✅ | ✅ | ✅ | ✅ Own only | ❌ |
| `POST /vm/{id}/stop` | ✅ | ✅ | ✅ | ✅ Own only | ❌ |
| `POST /vm/{id}/restart` | ✅ | ✅ | ✅ | ✅ Own only | ❌ |
| `DELETE /vm/{id}` | ✅ | ✅ | ✅ | ✅ Own only | ❌ |
| `GET /vm/{id}/logs` | ❌ | ✅ | ✅ | ✅ Own only | ❌ |
| `POST /vm/{id}/snapshots` | ✅ | ✅ | ✅ | ✅ Own only | ❌ |
| `GET /vm/{id}/snapshots` | ✅ | ✅ | ✅ | ✅ Own only | ✅ |
| `POST /vm/{id}/console` | ❌ | ✅ | ✅ | ✅ Own only | ❌ |
| `DELETE /vm/{id}/console` | ❌ | ✅ | ✅ | ✅ Own only | ❌ |
| `GET /vm/{id}/resources` | ✅ | ✅ | ✅ | ✅ Own only | ✅ |
| `POST /vm/{id}/resize-cpu` | ✅ | ✅ | ✅ | ✅ Own only | ❌ |
| `POST /vm/{id}/resize-ram` | ✅ | ✅ | ✅ | ✅ Own only | ❌ |
| `POST /vm/{id}/resize-disk` | ✅ | ✅ | ✅ | ✅ Own only | ❌ |
| `GET /vm/{id}/disk-info` | ✅ | ✅ | ✅ | ✅ Own only | ✅ |
| `GET /vm/storage-info` | ✅ | ✅ | ✅ | ❌ | ❌ |
| `GET /vm/{id}/ssh-info` | ✅ | ✅ | ✅ | ✅ Own only | ❌ |
| `GET /vm/{id}/ssh-key` | ✅ | ✅ | ✅ | ✅ Own only | ❌ |
| `POST /vm/{id}/ssh-key/regenerate` | ✅ | ✅ | ✅ | ✅ Own only | ❌ |

**Legend:**
- ✅ = Full access
- ✅* = Super admin can read all VMs but NOT logs/console (privacy protection)
- ✅ Own only = Access only to VMs owned by the user
- ❌ = No access

### Permission Helper Functions

| Function | Logic |
|----------|-------|
| `has_permission(user, tenant_id, "vm:create", db)` | User has `vm:create` permission in tenant |
| `has_permission(user, tenant_id, "vm:delete", db)` | User can delete/manage ALL VMs in tenant |
| `is_super_admin(user, db)` | User has `super_admin` IAM role (tenant_id=NULL) |

---

## API Reference

### 1. Create VM (Legacy)

> **DEPRECATED:** Use `/vm/provision` for Proxmox VMs.

**Endpoint:** `POST /vm/create`
**Authorization:** Bearer Token Required with `vm:create` permission

**Note:** This endpoint rejects Docker network provisioning. Use Proxmox provider with TenantNetworks.

**Request Body:**
```json
{
  "name": "web-server-01",
  "description": "Production web server",
  "provider": "proxmox",
  "cpu": 2,
  "ram": 4096
}
```

**Parameters:**
| Field | Type | Required | Constraints | Description |
| :--- | :--- | :--- | :--- | :--- |
| `name` | string | Yes | Unique within tenant. 3-50 chars | VM identifier |
| `provider` | string | No | Default: `proxmox` | Virtualization provider |
| `cpu` | integer | No | 1-32. Default: 2 | Number of cores |
| `ram` | integer | No | 512-65536 MB. Default: 4096 | Memory allocation |
| `template_id` | integer | Yes* | *Required for Proxmox | Template ID to clone |
| `description` | string | No | Max 500 chars | Optional description |
| `network_id` | integer | No | Deprecated | ~~Network attachment~~ |

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `400 Bad Request` | Docker network provisioning not supported |
| `403 Forbidden` | No `vm:create` permission |
| `409 Conflict` | VM name already exists |

---

### 2. Provision VM (Proxmox)

Provisions a new Proxmox VM with cloud-init configuration. This is the **primary** VM creation endpoint.

**Endpoint:** `POST /vm/provision`
**Authorization:** Bearer Token Required with `vm:create` permission

**Request Body:**
```json
{
  "name": "web-server-01",
  "description": "Production web server",
  "template_id": 100,
  "cpu": 2,
  "ram": 4096,
  "network_id": 1,
  "ip_mode": "dhcp",
  "username": "admin",
  "password": "securepassword123",
  "ssh_public_key": "ssh-rsa AAAAB...",
  "auto_start": true,
  "disk_size_gb": 20,
  "dns_nameservers": ["8.8.8.8", "1.1.1.1"],
  "dns_search": "example.com"
}
```

**Parameters:**
| Field | Type | Required | Constraints | Description |
| :--- | :--- | :--- | :--- | :--- |
| `name` | string | Yes | Unique within tenant | VM identifier |
| `template_id` | integer | Yes | Valid template ID | Proxmox template to clone |
| `cpu` | integer | No | 1-32. Default: 1 | vCPU cores |
| `ram` | integer | No | 512-65536. Default: 1024 | Memory in MB |
| `network_id` | integer | No | TenantNetwork ID | Network to attach to |
| `ip_mode` | string | No | `dhcp`/`static`. Default: `dhcp` | IP assignment mode |
| `ip_address` | string | Conditional | *Required if ip_mode=static | Static IP within network CIDR |
| `username` | string | No | Cloud-init user. Default: `ubuntu` | OS username |
| `password` | string | No | Cloud-init password | OS password |
| `ssh_public_key` | string | No | SSH key | Authorized SSH key |
| `auto_start` | boolean | No | Default: true | Start VM after provisioning |
| `description` | string | No | Max 500 chars | VM description |
| `disk_size_gb` | integer | No | 1-10000 GB. Min = template size | Target disk size in GB |
| `dns_nameservers` | array | No | DNS servers | DNS nameservers |
| `dns_search` | string | No | DNS search domain | DNS search domain |
| `skip_cloudinit` | boolean | No | Default: false | Skip cloud-init (for Windows VMs) |

**Success Response:** `201 Created`
```json
{
  "id": 1,
  "name": "web-server-01",
  "status": "provisioning",
  "provider": "proxmox",
  "cpu": 2,
  "ram": 4096,
  "disk_size_mb": 20480,
  "disk_size_gb": 20.0,
  "ip_address": null,
  "owner_id": 1,
  "tenant_id": 1,
  "template_id": 100,
  "created_at": "2026-04-23T10:00:00Z"
}
```

**Logic Flow:**
1. Verify `vm:create` permission
2. Verify tenant has pod assigned (is provisioned)
3. Resolve network (tenant default or specified)
4. Validate static IP within network CIDR
5. Check VM quota (tenant limits)
6. Acquire VM name advisory lock
7. Create VM record with status `provisioning`
8. Dispatch Celery task (`provision_vm_task`)
9. Return immediately

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `400 Bad Request` | Tenant not provisioned, invalid IP, quota exceeded |
| `403 Forbidden` | No `vm:create` permission |
| `404 Not Found` | Template or network not found |
| `409 Conflict` | VM name already exists |

---

### 3. List VMs

Retrieves a paginated list of VMs with filtering capabilities. Results ordered by creation date (newest first).

**Endpoint:** `GET /vm/list`
**Authorization:** Bearer Token Required (All authenticated users)

**Query Parameters:**
| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `status_filter` | string | `null` | Filter by status |
| `provider_filter` | string | `null` | Filter by provider |
| `owner_filter` | integer | `null` | Filter by owner (admin only) |
| `limit` | integer | `100` | Results to return (1-1000) |
| `offset` | integer | `0` | Results to skip |

**Success Response:** `200 OK`
```json
{
  "total": 2,
  "vms": [
    {
      "id": 2,
      "name": "db-server-01",
      "status": "stopped",
      "owner_id": 2,
      "tenant_id": 1
    },
    {
      "id": 1,
      "name": "web-server-01",
      "status": "running",
      "owner_id": 1,
      "tenant_id": 1
    }
  ],
  "offset": 0,
  "limit": 100
}
```

---

### 4. Get VM Statistics

Aggregates statistics for dashboard visualization.

**Endpoint:** `GET /vm/stats/summary`
**Authorization:** Bearer Token Required with `vm:delete` permission (or super_admin)

**Success Response:** `200 OK`
```json
{
  "total_vms": 15,
  "status_breakdown": {
    "running": 10,
    "stopped": 3,
    "error": 1,
    "pending": 1
  },
  "provider_breakdown": {
    "proxmox": 15,
    "docker": 0,
    "vsphere": 0
  },
  "cpu_total": 45,
  "ram_total_mb": 102400,
  "disk_total_gb": 500.0
}
```

---

### 5. Get VM Details

Retrieves detailed information for a specific VM.

**Endpoint:** `GET /vm/{vm_id}`
**Authorization:** Bearer Token Required with `vm:read` permission

**URL Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `vm_id` | integer | The ID of the VM |

**Success Response:** `200 OK`
```json
{
  "id": 1,
  "name": "web-server-01",
  "status": "running",
  "provider": "proxmox",
  "cpu": 2,
  "ram": 4096,
  "disk_size_mb": 20480,
  "disk_size_gb": 20.0,
  "ip_address": "10.0.1.50",
  "owner_id": 1,
  "tenant_id": 1,
  "template_id": 100,
  "created_at": "2026-04-23T10:00:00Z"
}
```

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `404 Not Found` | VM not found in tenant |
| `403 Forbidden` | Access denied |

---

### 6. Update VM Metadata

Updates metadata fields for an existing VM.

**Endpoint:** `PATCH /vm/{vm_id}`
**Authorization:** Bearer Token Required with `vm:update` permission or VM owner

**Request Body:**
```json
{
  "description": "Updated description for the VM"
}
```

**Updatable Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `description` | string | VM description |

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `403 Forbidden` | No permission to modify VM |

---

### 7. Start VM

Starts a stopped VM.

**Endpoint:** `POST /vm/{vm_id}/start`
**Authorization:** Bearer Token Required with `vm:update` permission or VM owner

**Success Response:** `200 OK`
Returns `VMResponse` with status `running`.

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `400 Bad Request` | VM not in startable state |
| `403 Forbidden` | No permission |

---

### 8. Stop VM

Gracefully stops a running VM.

**Endpoint:** `POST /vm/{vm_id}/stop`
**Authorization:** Bearer Token Required with `vm:update` permission or VM owner

**Success Response:** `200 OK`
Returns `VMResponse` with status `stopped`.

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `400 Bad Request` | VM not running |
| `403 Forbidden` | No permission |

---

### 9. Restart VM

Restarts a running VM (Stop → Start).

**Endpoint:** `POST /vm/{vm_id}/restart`
**Authorization:** Bearer Token Required with `vm:update` permission or VM owner

**Success Response:** `200 OK`
Returns `VMResponse` with status `running`.

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `400 Bad Request` | VM not running |
| `403 Forbidden` | No permission |

---

### 10. Delete VM

Permanently deletes a VM and destroys its infrastructure.

**Endpoint:** `DELETE /vm/{vm_id}`
**Authorization:** Bearer Token Required with `vm:update` permission or VM owner

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `force` | boolean | `false` | Force deletion of running VM |

**Success Response:** `204 No Content`

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `400 Bad Request` | Cannot delete running VM (use `force=true`) |
| `403 Forbidden` | No permission |

---

### 11. Get VM Logs

Retrieves logs from the VM for debugging.

**Endpoint:** `GET /vm/{vm_id}/logs`
**Authorization:** Bearer Token Required with `vm:update` permission or VM owner

**Note:** Super admins are BLOCKED from accessing logs (tenant privacy protection).

**Query Parameters:**
| Parameter | Type | Default | Constraints | Description |
|-----------|------|---------|-------------|-------------|
| `tail` | integer | `100` | 1-10000 | Log lines to retrieve |

**Success Response:** `200 OK`
```json
{
  "vm_id": 1,
  "vm_name": "web-server-01",
  "logs": "2026-04-23 12:00:00 - Starting services...",
  "lines": 2
}
```

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `403 Forbidden` | Super admin cannot access logs |

---

### 12. Get VM Resources

Retrieves live CPU, RAM, and disk resource configuration for a VM from the hypervisor.

**Endpoint:** `GET /vm/{vm_id}/resources`
**Authorization:** Bearer Token Required (must be in same tenant)

**URL Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `vm_id` | integer | The ID of the VM |

**Success Response:** `200 OK`
```json
{
  "cpu_cores": 2,
  "memory_mb": 4096,
  "memory_gb": 4.0,
  "disks": {
    "scsi0": {
      "size": "20G",
      "storage": "local-lvm"
    }
  },
  "digest": "abc123...",
  "name": "web-server-01",
  "status": "running"
}
```

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `404 Not Found` | VM not found |
| `403 Forbidden` | Access denied |
| `400 Bad Request` | VM has no Proxmox VM ID configured |

---

### 13. Resize CPU

Resizes the number of CPU cores for a VM. Requires VM restart to take effect.

**Endpoint:** `POST /vm/{vm_id}/resize-cpu`
**Authorization:** Bearer Token Required with `vm:update` permission or VM owner

**Request Body:**
```json
{
  "cores": 4,
  "restart_after_resize": true
}
```

**Parameters:**
| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| `cores` | integer | Yes | 1-32 | Target CPU cores |
| `restart_after_resize` | boolean | No | Default: true | Restart VM after resize |

**Success Response:** `200 OK`
```json
{
  "resource_type": "cpu",
  "previous_value": 2,
  "new_value": 4,
  "status": "resized",
  "restarted": true
}
```

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `404 Not Found` | VM not found |
| `403 Forbidden` | No permission |
| `400 Bad Request` | CPU resize only supported for Proxmox VMs |

---

### 14. Resize RAM

Resizes the RAM allocation for a VM. Requires VM restart to take effect.

**Endpoint:** `POST /vm/{vm_id}/resize-ram`
**Authorization:** Bearer Token Required with `vm:update` permission or VM owner

**Request Body:**
```json
{
  "memory_mb": 8192,
  "restart_after_resize": true
}
```

**Parameters:**
| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| `memory_mb` | integer | Yes | 512-65536 MB | Target RAM in MB |
| `restart_after_resize` | boolean | No | Default: true | Restart VM after resize |

**Success Response:** `200 OK`
```json
{
  "resource_type": "memory",
  "previous_value": 4096,
  "new_value": 8192,
  "status": "resized",
  "restarted": true
}
```

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `404 Not Found` | VM not found |
| `403 Forbidden` | No permission |
| `400 Bad Request` | RAM resize only supported for Proxmox VMs |

---

### 15. Resize Disk

Resizes a VM disk. Only accepts relative sizes (+XG format). Works on both running and stopped VMs.

**Endpoint:** `POST /vm/{vm_id}/resize-disk`
**Authorization:** Bearer Token Required with `vm:update` permission or VM owner

**Request Body:**
```json
{
  "disk": "scsi0",
  "size": "+10G",
  "restart_after_resize": false
}
```

**Parameters:**
| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| `disk` | string | No | Default: `scsi0` | Disk identifier (e.g., `scsi0`, `virtio0`) |
| `size` | string | Yes | Must start with `+` | Relative size to add (e.g., `+10G`, `+512M`) |
| `restart_after_resize` | boolean | No | Default: false | Restart VM after resize to apply changes |

**Success Response:** `200 OK`
```json
{
  "disk_id": "scsi0",
  "previous_size_mib": 20480,
  "new_size_mib": 30720,
  "previous_size_gb": 20.0,
  "new_size_gb": 30.0,
  "status": "resized",
  "restarted": false
}
```

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `404 Not Found` | VM not found |
| `403 Forbidden` | No permission |
| `400 Bad Request` | Disk not found or invalid size format |
| `423 Locked` | VM is locked (try again later) |

---

### 16. Get Disk Info

Retrieves current disk configuration for a VM from the hypervisor.

**Endpoint:** `GET /vm/{vm_id}/disk-info`
**Authorization:** Bearer Token Required (must be in same tenant)

**URL Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `vm_id` | integer | The ID of the VM |

**Success Response:** `200 OK`
```json
[
  {
    "id": "scsi0",
    "storage": "local-lvm",
    "volume": "vm-100-disk-0",
    "size_mib": 20480,
    "size_gb": 20.0
  }
]
```

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `404 Not Found` | VM not found |
| `403 Forbidden` | Access denied |

---

### 17. Get Storage Info

Retrieves available storage pool information for the current pod.

**Endpoint:** `GET /vm/storage-info`
**Authorization:** Bearer Token Required (must be in same tenant)

**Success Response:** `200 OK`
```json
{
  "local-lvm": {
    "total_gb": 500.0,
    "free_gb": 350.0,
    "used_gb": 150.0,
    "content": "images,rootdir"
  },
  "local": {
    "total_gb": 100.0,
    "free_gb": 80.0,
    "used_gb": 20.0,
    "content": "iso,vztmpl"
  }
}
```

---

### 18. Create VM Snapshot

Creates a snapshot of a VM. VM must be running.

**Endpoint:** `POST /vm/{vm_id}/snapshots`
**Authorization:** Bearer Token Required with `vm:update` permission or VM owner

**Request Body:**
```json
{
  "name": "web-server-backup",
  "description": "Before update"
}
```

**Parameters:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Snapshot name |
| `description` | string | No | Snapshot description |

**Success Response:** `201 Created`
```json
{
  "id": 1,
  "vm_id": 1,
  "name": "web-server-backup",
  "description": "Before update",
  "image_tag": "backup-20260423",
  "created_at": "2026-04-23T12:00:00Z"
}
```

---

### 19. List VM Snapshots

Lists all snapshots for a VM.

**Endpoint:** `GET /vm/{vm_id}/snapshots`
**Authorization:** Bearer Token Required with `vm:read` permission or VM owner

**Success Response:** `200 OK`
```json
[
  {
    "id": 1,
    "vm_id": 1,
    "name": "web-server-backup",
    "image_tag": "backup-20260423",
    "created_at": "2026-04-23T12:00:00Z"
  }
]
```

---

### 20. Restore VM Snapshot

Restores a VM from a snapshot.

**Endpoint:** `POST /vm/{vm_id}/snapshots/{snapshot_id}/restore`
**Authorization:** Bearer Token Required with `vm:update` permission or VM owner

**Success Response:** `200 OK`
```json
{
  "message": "Snapshot restored successfully"
}
```

---

### 21. Delete VM Snapshot

Deletes a VM snapshot.

**Endpoint:** `DELETE /vm/{vm_id}/snapshots/{snapshot_id}`
**Authorization:** Bearer Token Required with `vm:update` permission or VM owner

**Success Response:** `200 OK`
```json
{
  "message": "Snapshot deleted successfully"
}
```

---

### 22. Create Console Session

Creates a VNC or serial console session for a VM. Returns a WebSocket URL for browser-based console access.

**Endpoint:** `POST /vm/{vm_id}/console`
**Authorization:** Bearer Token Required with `vm:console` permission or VM owner

**Note:** Super admins are BLOCKED from console access (tenant privacy protection). VM must be running.

**Success Response:** `200 OK`
```json
{
  "websocket_url": "/v1/vm/ws/console/abc123...",
  "vm_id": 1,
  "vnc_password": "ticket123...",
  "console_type": "vnc",
  "desktop_name": "VNC Desktop"
}
```

**Console Types:**
- `vnc` - Standard VNC console (default fallback)
- `serial` - Serial console (preferred, lower bandwidth)

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `404 Not Found` | VM not found |
| `400 Bad Request` | VM not running or no Proxmox VM ID |
| `403 Forbidden` | Super admin blocked or no permission |

---

### 23. Disconnect Console Session

Disconnects an active console session for a VM.

**Endpoint:** `DELETE /vm/{vm_id}/console`
**Authorization:** Bearer Token Required with `vm:console` permission or VM owner

**Success Response:** `200 OK`
```json
{
  "status": "disconnected",
  "message": "Console session terminated"
}
```

**Possible Status Values:**
- `disconnected` - Session terminated successfully
- `no_active_session` - No active session found
- `disconnect_failed` - Failed to terminate session
- `no_session_info` - Session info not available

---

### 24. Get SSH Connection Info

Retrieves SSH connection information for a VM.

**Endpoint:** `GET /vm/{vm_id}/ssh-info`
**Authorization:** Bearer Token Required with `vm:read` permission

**URL Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `vm_id` | integer | The ID of the VM |

**Success Response:** `200 OK`
```json
{
  "vm_id": 1,
  "vm_name": "web-server-01",
  "ssh_user": "ubuntu",
  "ip_address": "10.0.1.50",
  "ssh_public_key": "ssh-rsa AAAAB...",
  "ssh_command": "ssh ubuntu@10.0.1.50 -i web-server-01.pem",
  "has_private_key": true
}
```

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `404 Not Found` | VM not found |
| `403 Forbidden` | Not authorized |

---

### 25. Download SSH Private Key

Downloads the SSH private key for a VM as a `.pem` file.

**Endpoint:** `GET /vm/{vm_id}/ssh-key`
**Authorization:** Bearer Token Required with `vm:read` permission

**URL Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `vm_id` | integer | The ID of the VM |

**Success Response:** `200 OK`
- Content-Type: `application/octet-stream`
- Content-Disposition: `attachment; filename="{vm_name}.pem"`

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `404 Not Found` | VM not found or no SSH key stored |
| `403 Forbidden` | Not authorized |

---

### 26. Regenerate SSH Keypair

Regenerates the SSH key pair for a VM. Returns the new private key (one-time download). Also deploys the new public key to the VM via guest agent.

**Endpoint:** `POST /vm/{vm_id}/ssh-key/regenerate`
**Authorization:** Bearer Token Required with `vm:create` permission

**URL Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `vm_id` | integer | The ID of the VM |

**Success Response:** `200 OK`
```json
{
  "vm_id": 1,
  "ssh_user": "ubuntu",
  "ssh_public_key": "ssh-rsa AAAAB...",
  "ssh_private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\n...",
  "ssh_command": "ssh ubuntu@10.0.1.50 -i web-server-01.pem",
  "warning": "Keep this key secure. Anyone with this file can access your VM."
}
```

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `404 Not Found` | VM not found |
| `403 Forbidden` | Not authorized |
| `500 Internal Server Error` | Key generated but failed to deploy to VM |

---

## Data Models \& Schemas

### VMResponse Schema

```typescript
interface VMResponse {
  id: number;
  name: string;
  description: string | null;
  provider: "proxmox" | "docker" | "vsphere";
  cpu: number;
  ram: number;           // MB
  disk_size_mb: number;  // MB (raw from database)
  disk_size_gb: number;  // GB (computed: disk_size_mb / 1024)
  ip_address: string | null;
  status: "creating" | "pending" | "provisioning" | "running" | "stopped" | "error";
  celery_task_id: string | null;
  owner_id: number;
  tenant_id: number;
  template_id: number | null;
  proxmox_vm_id: number | null;
  error: string | null;
  created_at: string;  // ISO 8601
}
```

### VMListResponse Schema

```typescript
interface VMListResponse {
  total: number;
  vms: VMResponse[];
  offset: number;
  limit: number;
}
```

### VMStatsResponse Schema

```typescript
interface VMStatsResponse {
  total_vms: number;
  status_breakdown: {
    running: number;
    stopped: number;
    error: number;
    pending: number;
  };
  provider_breakdown: {
    proxmox: number;
    docker: number;
    vsphere: number;
  };
  cpu_total: number;
  ram_total_mb: number;
  disk_total_gb: number;
}
```

### VMSnapshotResponse Schema

```typescript
interface VMSnapshotResponse {
  id: number;
  vm_id: number;
  name: string;
  description: string | null;
  image_tag: string;
  created_at: string;
  created_by: number | null;
}
```

---

## Error Handling

The API uses standard HTTP status codes.

### Status Codes

| Code | Meaning |
| :--- | :--- |
| `200 OK` | Request succeeded |
| `201 Created` | Resource created |
| `204 No Content` | Resource deleted |
| `400 Bad Request` | Invalid input |
| `401 Unauthorized` | Invalid/missing JWT |
| `403 Forbidden` | Insufficient permissions |
| `404 Not Found` | Resource not found |
| `409 Conflict` | Duplicate resource |
| `423 Locked` | Resource is locked (try again later) |
| `429 Too Many Requests` | Rate limited |
| `500 Internal Server Error` | Unexpected error |
| `503 Service Unavailable` | Task queue unavailable |

### Error Response Format

```json
{
  "detail": "Error message describing what went wrong."
}
```

### Common Error Scenarios

**1. Duplicate VM Name**
```json
{
  "detail": "VM name already exists. Please choose a different name."
}
```
Status: `409 Conflict`

**2. Permission Denied**
```json
{
  "detail": "Permission denied: vm:create required"
}
```
Status: `403 Forbidden`

**3. Quota Exceeded**
```json
{
  "detail": "Quota exceeded: max_vms limit of 10 reached (10 used, requested 1 more)"
}
```
Status: `400 Bad Request`

**4. Tenant Not Provisioned**
```json
{
  "detail": "Tenant not provisioned - no pod assigned. Please wait for tenant approval."
}
```
Status: `400 Bad Request`

**5. Docker Network Disabled**
```json
{
  "detail": "Docker network provisioning is no longer supported. Use Proxmox provider with TenantNetwork."
}
```
Status: `400 Bad Request`

**6. Super Admin Logs Blocked**
```json
{
  "detail": "Super admins cannot access VM logs. This is to protect tenant privacy."
}
```
Status: `403 Forbidden`

**7. Console Access Blocked**
```json
{
  "detail": "Console access is not available for super admins"
}
```
Status: `403 Forbidden`
