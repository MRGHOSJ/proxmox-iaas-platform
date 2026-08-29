# Tenant API Documentation

This module handles tenant organization management including CRUD operations, verification, quota management, and network topology.

## Table of Contents
- [Tenant Model](#tenant-model)
- [Tenant Status](#tenant-status)
- [Endpoints](#endpoints)
- [User Management](#user-management)
- [Quota Management](#quota-management)
- [Network Topology](#network-topology)

---

## Tenant Model

The tenant represents an organization that owns resources (VMs, networks, users).

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Unique identifier |
| `name` | string | Organization name |
| `slug` | string | URL-friendly identifier |
| `is_active` | boolean | Organization active status |
| `is_verified` | boolean | Verification status |
| `status` | string | Current status |
| `settings` | string | JSON quota settings |
| `bridge_id` | integer | Allocated bridge |
| `pod_id` | integer | Assigned pod |
| `opnsense_vm_id` | integer | OPNsense firewall VM |
| `opnsense_vm_name` | string | OPNsense VM name |
| `lan_ip` | string | LAN IP address |
| `wan_ip` | string | WAN IP address |
| `fixed_wan_ip` | string | Static WAN IP |
| `provisioned_at` | datetime | Provisioning timestamp |

---

## Tenant Status

| Status | Description |
|--------|-------------|
| `pending` | Initial state |
| `pending_approval` | Awaiting admin approval |
| `verified` | Verified, awaiting provisioning |
| `provisioning` | Being provisioned |
| `active` | Fully operational |
| `suspended` | Temporarily suspended |
| `deprovisioned` | Deprovisioned |
| `error` | Provisioning failed |

---

## Endpoints

### 1. Get My Tenants

Gets all tenants the current user has access to.

**Endpoint:** `GET /tenants/my-tenants`  
**Authorization:** Authenticated users

**Success Response:** `200 OK`
```json
[
  {
    "id": 1,
    "name": "Acme Corp",
    "slug": "acme-corp",
    "is_active": true,
    "is_verified": true,
    "status": "active"
  }
]
```

---

### 2. List All Tenants

Lists all tenants (super admin only).

**Endpoint:** `GET /tenants/`  
**Authorization:** Super Admin only

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `skip` | integer | `0` | Pagination offset |
| `limit` | integer | `20` | Results limit |

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

### 3. Get Tenant Details

Gets tenant with detailed statistics.

**Endpoint:** `GET /tenants/{tenant_id}`  
**Authorization:** Super Admin or tenant member

**Success Response:** `200 OK`
```json
{
  "id": 1,
  "name": "Acme Corp",
  "slug": "acme-corp",
  "is_active": true,
  "is_verified": true,
  "status": "active",
  "user_count": 5,
  "vm_count": 10,
  "network_count": 2,
  "vm_status_breakdown": {
    "running": 7,
    "stopped": 2,
    "error": 1
  },
  "total_cpu": 20,
  "total_ram": 40960,
  "total_disk": 500,
  "vm_provider_breakdown": {
    "proxmox": 10
  }
}
```

---

### 4. Create Tenant

Creates a new tenant (super admin only).

**Endpoint:** `POST /tenants/`  
**Authorization:** Super Admin only

**Request Body:**
```json
{
  "name": "New Enterprise",
  "slug": "new-enterprise"
}
```

**Success Response:** `201 Created`

---

### 5. Update Tenant

Updates tenant settings.

**Endpoint:** `PATCH /tenants/{tenant_id}`  
**Authorization:** Super Admin only

**Request Body:**
```json
{
  "name": "Updated Name",
  "is_active": true
}
```

---

### 6. Delete Tenant

Deletes a tenant (must have no users or VMs).

**Endpoint:** `DELETE /tenants/{tenant_id}`  
**Authorization:** Super Admin only

**Success Response:** `204 No Content`

---

### 7. Verify Tenant

Verifies a tenant and starts provisioning.

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

**Success Response:** `200 OK`
```json
{
  "id": 5,
  "name": "New Corp",
  "is_verified": true,
  "status": "provisioning",
  "bridge_id": 3,
  "opnsense_vm_id": 101,
  "lan_ip": "10.0.3.1",
  "wan_ip": "203.0.113.10"
}
```

---

### 8. Get Tenant Quota

Gets quota settings and usage.

**Endpoint:** `GET /tenants/{tenant_id}/quota`  
**Authorization:** Super Admin or tenant admin

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

Updates quota settings.

**Endpoint:** `PATCH /tenants/{tenant_id}/quota`  
**Authorization:** Super Admin only

**Request Body:**
```json
{
  "max_vms": 20,
  "max_cpu_cores": 64
}
```

---

### 10. Get Tenant Networks

Gets all networks in a tenant.

**Endpoint:** `GET /tenants/{tenant_id}/networks`  
**Authorization:** Super Admin only

**Success Response:** `200 OK`
```json
[
  {
    "id": 1,
    "name": "default",
    "cidr": "10.0.1.0/24",
    "gateway": "10.0.1.1",
    "status": "active",
    "vlan_id": 100,
    "is_default": true,
    "pod_id": 1,
    "pod_name": "pod-01",
    "ips_used": 3,
    "ips_available": 250,
    "vms": [
      {
        "id": 1,
        "name": "web-01",
        "status": "running",
        "ip_address": "10.0.1.50"
      }
    ]
  }
]
```

---

### 11. Get Tenant VMs

Gets all VMs in a tenant.

**Endpoint:** `GET /tenants/{tenant_id}/vms`  
**Authorization:** Super Admin only

**Success Response:** `200 OK`
```json
[
  {
    "id": 1,
    "name": "web-01",
    "status": "running",
    "ip_address": "10.0.1.50",
    "cpu": 2,
    "ram": 4096
  }
]
```

---

## User Management

### 12. List All Users

Lists all users across all tenants with pagination and filtering.

**Endpoint:** `GET /tenants/users`  
**Authorization:** Super Admin only

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `skip` | integer | `0` | Pagination offset |
| `limit` | integer | `20` | Results limit |
| `search` | string | `null` | Search by username, email, or full_name |
| `tenant_id` | integer | `null` | Filter by tenant ID |
| `is_active` | boolean | `null` | Filter by active status |

**Success Response:** `200 OK`
```json
{
  "total": 15,
  "users": [
    {
      "id": 1,
      "username": "admin",
      "email": "admin@acme.com",
      "full_name": "Admin User",
      "is_active": true,
      "created_at": "2025-01-10T08:00:00Z",
      "tenant_id": 1,
      "tenant_name": "Acme Corp",
      "roles": ["tenant_admin"],
      "tenant_memberships": [
        {
          "tenant_id": 1,
          "tenant_name": "Acme Corp",
          "role_name": "tenant_admin"
        }
      ],
      "is_super_admin": false
    }
  ]
}
```

---

### 13. Ban/Unban User

Toggles the active status of a user (ban or unban). Super admins cannot be banned.

**Endpoint:** `PATCH /tenants/users/{user_id}/ban`  
**Authorization:** Super Admin only

**URL Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `user_id` | integer | User ID to ban/unban |

**Success Response:** `200 OK`
```json
{
  "id": 5,
  "username": "jdoe",
  "is_active": false,
  "message": "User banned successfully"
}
```

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `400 Bad Request` | Cannot ban a super admin |
| `404 Not Found` | User not found |

---

### 14. Get Unverified Tenants

Lists all tenants that have not yet been verified.

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

## Network Topology

### Get Tenant Topology

Gets the complete network topology for a tenant.

**Endpoint:** `GET /tenants/{tenant_id}/topology`  
**Authorization:** Super Admin or tenant member

**Success Response:** `200 OK`
```json
{
  "tenant_id": 1,
  "tenant_name": "Acme Corp",
  "tenant_status": "active",
  "wan": {
    "bridge": "vmbr0",
    "ip": "203.0.113.10"
  },
  "firewall": {
    "name": "opnsense-acme-corp",
    "lan_ip": "10.0.1.1",
    "wan_ip": "203.0.113.10",
    "status": "running"
  },
  "lan": {
    "bridge": "vmbr1",
    "cidr": "10.0.1.0/24",
    "gateway": "10.0.1.1"
  },
  "vms": [
    {
      "id": 1,
      "name": "web-01",
      "ip_address": "10.0.1.50",
      "status": "running",
      "provider": "proxmox",
      "cpu": 2,
      "ram": 4096
    }
  ],
  "stats": {
    "total": 5,
    "running": 3,
    "stopped": 1,
    "error": 1
  }
}
```

---

## Quota Management

### Quota Settings

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `max_vms` | integer | `null` | Maximum VMs (null = unlimited) |
| `max_cpu_cores` | integer | `null` | Maximum CPU cores |
| `max_ram_mb` | integer | `null` | Maximum RAM in MB |
| `max_disk_gb` | integer | `null` | Maximum disk in GB |
| `max_networks` | integer | `null` | Maximum networks |

### Checking Quota

Quota is checked during VM creation. If exceeded:

```json
{
  "detail": "Quota exceeded: max_vms limit of 10 reached (10 used, requested 1 more)"
}
```
Status: `400 Bad Request`

---

## Error Handling

### Common Errors

**1. Access Denied**
```json
{
  "detail": "Access denied"
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

**3. Cannot Delete with Resources**
```json
{
  "detail": "Cannot delete tenant with 5 user(s). Please reassign or delete users first."
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