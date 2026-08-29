# Images API Documentation

This module handles VM image/template management, build workflows, and tenant assignments for Proxmox provisioning.

## Table of Contents

- [Authorization](#authorization)
- [Categories](#categories)
- [URL Metadata](#url-metadata)
- [Downloaded Images](#downloaded-images)
- [Templates](#templates)
- [Builds](#builds)
- [Build Operations](#build-operations)
- [Build Hardware](#build-hardware)
- [ISO Management](#iso-management)
- [CRUD Operations](#crud-operations)
- [Tenant Assignment](#tenant-assignment)

---

## Authorization

Most endpoints require **Super Admin** role. Tenant-scoped endpoints require the user to belong to the target tenant.

---

## Categories

### List Categories

`GET /images/categories`

List image categories with counts (any authenticated user).

**Response:** `200 OK`
```json
[
  { "category": "client_vm", "count": 5 },
  { "category": "firewall", "count": 2 }
]
```

---

## URL Metadata

### Query Image Metadata

`GET /images/query-url-metadata?url=<url>`

Query Proxmox for URL metadata (filename, size, mimetype).

**Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `url` | string | Yes | URL to query |

**Response:** `200 OK`
```json
{
  "filename": "ubuntu-22.04.qcow2",
  "size": 2400000000,
  "mimetype": "application/octet-stream"
}
```

**Errors:**
- `502` -- Proxmox cannot reach URL (DNS, SSL, or connection failure)
- `504` -- Proxmox timed out

---

## Downloaded Images

### List Downloaded Images

`GET /images/downloaded?node=<node>&storage=<storage>`

List ISO and image files already downloaded to Proxmox storage.

**Parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `node` | string | `pve` | Proxmox node name |
| `storage` | string | `local` | Storage name |

**Authorization:** Super Admin

**Response:** `200 OK`
```json
[
  {
    "filename": "ubuntu-22.04.iso",
    "size": 4500000000,
    "format": "iso",
    "volid": "local:iso/ubuntu-22.04.iso",
    "is_image": false
  }
]
```

### Delete Downloaded Image

`DELETE /images/downloaded?node=<node>&storage=<storage>&volid=<volid>&filename=<filename>`

Delete a file (ISO/IMG) from Proxmox storage. Also removes any download-only ImageBuild row that tracked this file.

**Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `node` | string | Yes | Proxmox node name |
| `storage` | string | Yes | Storage name |
| `volid` | string | Yes | Volume ID to delete |
| `filename` | string | No | Display name for audit log |

**Authorization:** Super Admin

**Response:** `200 OK`
```json
{
  "status": "deleted",
  "volid": "local:iso/ubuntu-22.04.iso",
  "builds_removed": 0
}
```

---

## Templates

### List Templates

`GET /images/templates`

Super admin: list DB-registered templates with auto-sync from Proxmox.

**Authorization:** Super Admin

**Response:** `200 OK`
```json
[
  {
    "id": 1,
    "name": "ubuntu-22.04-template",
    "provider": "proxmox",
    "template_id": "9000",
    "category": "client_vm",
    "description": "Ubuntu 22.04 LTS",
    "version": "22.04",
    "os_type": "ubuntu",
    "tags": ["linux", "ubuntu"],
    "recommended_cpu": 2,
    "recommended_ram_mb": 4096,
    "recommended_disk_gb": 20,
    "provisioning_notes": "Cloud-init enabled",
    "is_active": true,
    "is_public": true,
    "api_enabled": true,
    "last_synced_at": "2024-01-15T10:30:00Z",
    "created_at": "2024-01-10T08:00:00Z",
    "tenant_count": 3
  }
]
```

---

## Builds

### List All Builds

`GET /images/builds`

Super admin: list all builds.

**Authorization:** Super Admin

**Response:** `200 OK`
```json
[
  {
    "id": 1,
    "vmid": 9100,
    "name": "my-build",
    "category": "client_vm",
    "node": "pve",
    "storage": "local-lvm",
    "iso_volid": "local:iso/ubuntu-22.04.iso",
    "iso_url": "https://releases.ubuntu.com/22.04/ubuntu-22.04.qcow2",
    "status": "running",
    "celery_task_id": "abc-123",
    "download_upid": null,
    "download_only": false,
    "created_at": "2024-01-15T10:30:00Z",
    "recommended_cpu": 2,
    "recommended_ram_mb": 4096,
    "recommended_disk_gb": 20
  }
]
```

---

## Build Operations

### Start Build

`POST /images/build`

Start template build from existing volid or download URL, then create VM.

**Authorization:** Super Admin

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Build name |
| `category` | string | Yes | Category (client_vm, firewall, vpn, etc.) |
| `node` | string | Yes | Proxmox node |
| `storage` | string | Yes | Storage name |
| `volid` | string | No* | Existing volume ID |
| `iso_url` | string | No* | ISO download URL |
| `image_url` | string | No* | Image download URL |
| `description` | string | No | Build description |
| `recommended_cpu` | int | No | Default CPU cores |
| `recommended_ram_mb` | int | No | Default RAM in MB |
| `recommended_disk_gb` | int | No | Default disk in GB |
| `download_only` | bool | No | Download ISO without creating VM |

*Provide one of `volid`, `iso_url`, or `image_url`.

**Response:** `200 OK`
```json
{
  "id": 1,
  "vmid": 9100,
  "status": "downloading_iso",
  "download_upid": "UPID:pve:0012345::taskid",
  "image_type": "iso",
  "download_only": false
}
```

### Get Download Progress

`GET /images/build/{vmid}/download-progress`

Poll ISO download progress. Auto-creates VM when complete.

**Authorization:** Super Admin

**Response:** `200 OK`
```json
{
  "status": "downloading",
  "percent": 67,
  "message": "Downloading (67%)",
  "downloaded_mb": 1200,
  "total_mb": 1800,
  "speed_mbps": "45M",
  "eta": "2m30s",
  "download_only": false
}
```

### Get Download Logs

`GET /images/build/{vmid}/download-logs`

Get full download task logs for a build.

**Authorization:** Super Admin

**Response:** `200 OK`
```json
{
  "vmid": 9100,
  "status": "stopped",
  "lines": ["--2024-01-15 10:30:00--  https://...", "Saving to: ..."]
}
```

### Get Build Status

`GET /images/build/{vmid}/status`

Check build VM status.

**Authorization:** Super Admin

**Response:** `200 OK`
```json
{
  "vmid": 9100,
  "name": "my-build",
  "status": "running",
  "proxmox_status": "running",
  "node": "pve",
  "celery_task_id": "abc-123",
  "download_only": false,
  "recommended_cpu": 2,
  "recommended_ram_mb": 4096,
  "recommended_disk_gb": 20
}
```

### Get Build Console

`POST /images/build/{vmid}/console`

Get VNC console for build VM. Returns WebSocket URL for real-time console access.

**Authorization:** Super Admin

**Response:** `200 OK`
```json
{
  "websocket_url": "/v1/vm/ws/console/abc123token",
  "vm_id": 9100,
  "vnc_password": "ticket-string",
  "console_type": "vnc",
  "desktop_name": "Build: my-build"
}
```

### Convert Build to Template

`POST /images/build/{vmid}/convert`

Convert build VM to Proxmox template (triggers Celery task).

**Authorization:** Super Admin

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | No | Template name |
| `category` | string | No | Category |
| `description` | string | No | Description |
| `os_type` | string | No | OS type |
| `os_version` | string | No | OS version |
| `recommended_cpu` | int | No | Default CPU cores |
| `recommended_ram_mb` | int | No | Default RAM in MB |
| `recommended_disk_gb` | int | No | Default disk in GB |
| `tags` | list[string] | No | Tags |

**Response:** `200 OK`
```json
{
  "status": "converting",
  "task_id": "celery-task-id"
}
```

### Cancel Build

`DELETE /images/build/{vmid}`

Cancel build. Destroys VM, keeps ISO.

**Authorization:** Super Admin

**Response:** `200 OK`
```json
{
  "status": "cancelled",
  "vm_destroyed": true,
  "iso_retained": true,
  "iso_path": "local:iso/ubuntu-22.04.iso"
}
```

---

## Build Hardware

### Get Build Resources

`GET /images/build/{vmid}/resources`

Super admin: get live CPU/RAM/disk of a build VM.

**Authorization:** Super Admin

**Response:** `200 OK`
```json
{
  "cpu_cores": 2,
  "memory_mb": 4096,
  "memory_gb": 4.0,
  "disks": { "scsi0": { "size_gb": 20 } },
  "digest": "...",
  "name": "my-build",
  "status": "running"
}
```

### Get Build Config

`GET /images/build/{vmid}/config`

Super admin: get the raw Proxmox VM config.

**Authorization:** Super Admin

**Response:** `200 OK`
```json
{
  "vmid": 9100,
  "config": { "cores": 2, "memory": 4096, "scsi0": "local-lvm:vm-9100-disk-0" }
}
```

### Resize Build CPU

`POST /images/build/{vmid}/resize-cpu`

**Authorization:** Super Admin

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `cores` | int | Yes | New CPU core count |
| `restart_after_resize` | bool | No | Restart VM after resize |

**Response:** `200 OK`
```json
{
  "resource_type": "cpu",
  "previous_value": 2,
  "new_value": 4,
  "status": "resized",
  "restarted": true
}
```

### Resize Build RAM

`POST /images/build/{vmid}/resize-ram`

**Authorization:** Super Admin

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `memory_mb` | int | Yes | New RAM in MB |
| `restart_after_resize` | bool | No | Restart VM after resize |

**Response:** `200 OK`
```json
{
  "resource_type": "memory",
  "previous_value": 4096,
  "new_value": 8192,
  "status": "resized",
  "restarted": true
}
```

### Resize Build Disk

`POST /images/build/{vmid}/resize-disk`

**Authorization:** Super Admin

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `disk` | string | Yes | Disk identifier (e.g., `scsi0`) |
| `size` | string | Yes | Size delta (e.g., `+10G`) |
| `restart_after_resize` | bool | No | Restart VM after resize |

**Response:** `200 OK`
```json
{
  "disk_id": "scsi0",
  "previous_size_mib": 20480,
  "new_size_mib": 30720,
  "previous_size_gb": 20.0,
  "new_size_gb": 30.0,
  "status": "resized",
  "restarted": true
}
```

---

## ISO Management

### List ISOs

`GET /images/iso/list?node=<node>&storage=<storage>`

List ISO files already present in Proxmox storage for reuse.

**Authorization:** Super Admin

**Response:** `200 OK`
```json
[
  {
    "filename": "ubuntu-22.04.iso",
    "size": 4500000000,
    "format": "iso",
    "volid": "local:iso/ubuntu-22.04.iso"
  }
]
```

---

## CRUD Operations

### List Images

`GET /images?category=<category>&provider=<provider>&status_filter=<filter>`

Super admin: list all images, auto-syncing from Proxmox.

**Authorization:** Super Admin

**Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `category` | string | No | Filter by category |
| `provider` | string | No | Filter by provider |
| `status_filter` | string | No | `registered`, `unregistered`, or `all` |

**Response:** `200 OK` -- Array of image objects.

### Get Image

`GET /images/{image_id}`

Super admin: full image metadata + tenant assignments.

**Authorization:** Super Admin

**Response:** `200 OK`
```json
{
  "id": 1,
  "name": "ubuntu-22.04-template",
  "provider": "proxmox",
  "template_id": "9000",
  "category": "client_vm",
  "assigned_tenant_ids": [1, 2, 3]
}
```

### Register Image

`POST /images`

Super admin: register existing Proxmox template.

**Authorization:** Super Admin

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Template name |
| `provider` | string | Yes | Provider (proxmox) |
| `template_id` | string | Yes | Proxmox VM template ID |
| `category` | string | Yes | Category |
| `description` | string | No | Description |
| `version` | string | No | Version |
| `os_type` | string | No | OS type |
| `tags` | list[string] | No | Tags |
| `recommended_cpu` | int | No | Default CPU cores |
| `recommended_ram_mb` | int | No | Default RAM in MB |
| `recommended_disk_gb` | int | No | Default disk in GB |
| `provisioning_notes` | string | No | Provisioning notes |
| `is_public` | bool | No | Public visibility |
| `api_enabled` | bool | No | Enable via API |

**Response:** `200 OK`
```json
{
  "id": 1,
  "name": "ubuntu-22.04-template",
  "message": "Image registered successfully"
}
```

### Update Image

`PATCH /images/{image_id}`

Super admin: update image metadata.

**Authorization:** Super Admin

**Request Body:** Partial update with any of the register fields.

**Response:** `200 OK`
```json
{
  "id": 1,
  "name": "ubuntu-22.04-template",
  "message": "Image updated successfully"
}
```

### Delete Image

`DELETE /images/{image_id}`

Super admin: delete image (destroys the underlying Proxmox template and soft-deletes the DB row).

**Authorization:** Super Admin

**Response:** `200 OK`
```json
{
  "message": "Image deleted successfully",
  "proxmox_destroyed": true,
  "proxmox_error": null
}
```

---

## Tenant Assignment

### Assign Image to Tenants

`POST /images/{image_id}/tenants`

Super admin: assign image to tenants.

**Authorization:** Super Admin

**Request Body:**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tenant_ids` | list[int] | Yes | Tenant IDs to assign |

**Response:** `200 OK`
```json
{
  "message": "Image assigned to 3 tenants"
}
```

### Unassign Image from Tenant

`DELETE /images/{image_id}/tenants/{tenant_id}`

Super admin: remove tenant assignment.

**Authorization:** Super Admin

**Response:** `200 OK`
```json
{
  "message": "Tenant unassigned successfully"
}
```

### List Tenant Images

`GET /images/tenants/{tenant_id}/images?category=<category>`

Tenant member: images visible to this tenant (public + assigned).

**Authorization:** Tenant member or Super Admin

**Response:** `200 OK`
```json
[
  {
    "id": 1,
    "name": "ubuntu-22.04-template",
    "category": "client_vm",
    "os_type": "ubuntu",
    "description": "Ubuntu 22.04 LTS",
    "recommended_cpu": 2,
    "recommended_ram_mb": 4096,
    "recommended_disk_gb": 20,
    "provisioning_notes": "Cloud-init enabled",
    "tags": ["linux", "ubuntu"],
    "template_id": "9000"
  }
]
```
