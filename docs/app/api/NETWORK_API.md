# Network Management API Documentation

This module handles the lifecycle management of tenant networks, bridges, and network infrastructure.

## Table of Contents
- [Core Concepts](#core-concepts)
- [Authorization](#authorization)
- [Network Models](#network-models)
- [Endpoints](#endpoints)
- [CIDR Validation](#cidr-validation)

---

## Core Concepts

### Multi-Tenant Networking
The platform implements a multi-tenant network architecture:
- Each **Tenant** has one or more **TenantNetworks**
- Tenants are allocated to **Pods** which provide compute resources
- Network resources include LAN bridges, VLANs, and IP address pools

### Network Components

| Component | Description |
|-----------|-------------|
| **Tenant** | Organization that owns networks and VMs |
| **Pod** | Physical/virtual infrastructure host |
| **TenantNetwork** | Virtual network segment for a tenant |
| **GlobalIPPool** | IP address pool allocated to a network |
| **VlanAllocation** | VLAN ID allocated per Pod |

---

## Authorization

### Permissions

| Permission | Description |
|------------|-------------|
| `network:create` | Create tenant networks |
| `network:read` | View network details |
| `network:delete` | Delete tenant networks |

### Endpoint Authorization Matrix

| Endpoint | Super Admin | Tenant Admin | Network Manager |
|----------|:-----------:|:------------:|:---------------:|
| `GET /networks/` | ✅ | ✅ | ✅ |
| `GET /networks/{id}` | ✅ | ✅ | ✅ |
| `GET /networks/{id}/logs` | ✅ | ✅ | ✅ |
| `POST /networks/` | ✅ | ✅ | ❌ |
| `DELETE /networks/{id}` | ✅ | ✅ | ❌ |
| `GET /admin/pods/` | ✅ | ❌ | ❌ |
| `GET /bridges/` | ✅ | ❌ | ❌ |

---

## Network Models

### TenantNetwork
Represents a virtual network segment assigned to a tenant.

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Unique identifier |
| `tenant_id` | integer | Owner tenant |
| `pod_id` | integer | Pod hosting the network |
| `ip_pool_id` | integer | GlobalIPPool reference |
| `cidr` | string | Network CIDR (e.g., "10.0.1.0/24") |
| `gateway_ip` | string | Gateway IP address |
| `vlan_id` | integer | VLAN ID (optional) |
| `name` | string | Network name (default, etc.) |
| `is_default` | boolean | Default network for tenant |
| `status` | string | `active`, `pending`, `error`, `deleted` |
| `provider_ref` | string | Provider-specific reference |
| `created_at` | datetime | Creation timestamp |

### Pod
Represents a physical or virtual infrastructure host.

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Unique identifier |
| `name` | string | Pod name |
| `provider_type` | string | `proxmox`, `vsphere`, `kvm` |
| `node_names` | string | Comma-separated node names |
| `max_tenants` | integer | Maximum tenants supported |
| `tenant_count` | integer | Current tenant count |
| `status` | string | `active`, `maintenance`, `offline` |

### GlobalIPPool
Represents an IP address pool allocated to a network.

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Unique identifier |
| `cidr` | string | Pool CIDR |
| `gateway_ip` | string | Gateway IP |
| `pool` | string | Pool identifier |
| `status` | string | `free`, `allocated` |
| `tenant_network_id` | integer | Allocated network |
| `allocated_at` | datetime | Allocation timestamp |

### VlanAllocation
Represents VLAN allocations per pod.

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Unique identifier |
| `pod_id` | integer | Pod reference |
| `vlan_id` | integer | VLAN ID |
| `status` | string | `free`, `allocated` |
| `tenant_network_id` | integer | Allocated network |

---

## Endpoints

### 1. List Tenant Networks

Retrieves all networks for the current tenant.

**Endpoint:** `GET /networks/`
**Authorization:** Bearer Token Required

**Success Response:** `200 OK`
```json
{
  "total": 2,
  "networks": [
    {
      "id": 1,
      "tenant_id": 1,
      "name": "default",
      "cidr": "10.0.1.0/24",
      "gateway_ip": "10.0.1.1",
      "vlan_id": 100,
      "is_default": true,
      "status": "active",
      "pod_id": 1,
      "created_at": "2026-04-23T10:00:00Z"
    }
  ]
}
```

---

### 2. Get Network Details

Retrieves a specific network by ID.

**Endpoint:** `GET /networks/{network_id}`
**Authorization:** Bearer Token Required

**URL Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `network_id` | integer | Network ID |

**Success Response:** `200 OK`
```json
{
  "id": 1,
  "tenant_id": 1,
  "name": "default",
  "cidr": "10.0.1.0/24",
  "gateway_ip": "10.0.1.1",
  "vlan_id": 100,
  "is_default": true,
  "status": "active",
  "created_at": "2026-04-23T10:00:00Z"
}
```

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `404 Not Found` | Network not found |

---

### 3. Get Network Logs

Retrieves logs for a specific network.

**Endpoint:** `GET /networks/{network_id}/logs`
**Authorization:** Bearer Token Required

**URL Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `network_id` | integer | Network ID |

**Success Response:** `200 OK`
```json
{
  "logs": [],
  "network_id": 1
}
```

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `404 Not Found` | Network not found |

---

### 4. Create Network

Creates an additional isolated network for the tenant. Each additional network gets its own /24 subnet and a VLAN tag. VMs on different networks can only communicate through OPNsense.

**Endpoint:** `POST /networks/`
**Authorization:** Bearer Token Required with `network:create` permission

**Request Body:**
```json
{
  "name": "staging-network"
}
```

**Parameters:**
| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| `name` | string | Yes | Unique within tenant | Network name |

**Success Response:** `201 Created`
```json
{
  "id": 2,
  "tenant_id": 1,
  "pod_id": 1,
  "ip_pool_id": 2,
  "cidr": "10.0.2.0/24",
  "gateway_ip": "10.0.2.1",
  "vlan_id": 101,
  "name": "staging-network",
  "is_default": false,
  "status": "pending",
  "provider_ref": "vmbr1",
  "created_at": "2026-04-23T12:00:00Z"
}
```

**Logic Flow:**
1. Verify tenant is provisioned (has pod assigned)
2. Allocate subnet from GlobalIPPool
3. Allocate VLAN from pod's VLAN pool
4. Resolve default network's bridge reference
5. Create TenantNetwork record with status `pending`
6. Create VLAN interface on hypervisor
7. Dispatch OPNsense VLAN creation task
8. Return network

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `400 Bad Request` | Tenant not fully provisioned, no default network |
| `403 Forbidden` | No `network:create` permission |

---

### 5. Delete Network

Deletes a non-default network and returns its IP and VLAN to the pool. The default network cannot be deleted (it is tied to OPNsense and the bridge).

**Endpoint:** `DELETE /networks/{network_id}`
**Authorization:** Bearer Token Required with `network:delete` permission

**URL Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `network_id` | integer | Network ID |

**Success Response:** `204 No Content`

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `400 Bad Request` | Cannot delete the default network |
| `404 Not Found` | Network not found |

---

### 6. List Pods (Admin)

Lists all available pods.

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
    }
  ]
}
```

---

### 7. Create Pod (Admin)

Creates a new pod.

**Endpoint:** `POST /admin/pods/`
**Authorization:** Super Admin only

**Request Body:**
```json
{
  "name": "pod-02",
  "provider_type": "proxmox",
  "node_names": "pve03,pve04",
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

**Success Response:** `201 Created`
```json
{
  "id": 2,
  "name": "pod-02",
  "provider_type": "proxmox",
  "node_names": "pve03,pve04",
  "max_tenants": 100,
  "tenant_count": 0,
  "status": "active"
}
```

---

### 8. Get Pod Details

Retrieves pod details.

**Endpoint:** `GET /admin/pods/{pod_id}`
**Authorization:** Super Admin only

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

---

### 9. Delete Pod

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

### 10. List Bridge Pools

Lists all bridge pool entries.

**Endpoint:** `GET /bridges/`
**Authorization:** Super Admin only

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `status_filter` | string | `null` | Filter by status |

**Success Response:** `200 OK`
```json
{
  "total": 10,
  "available": 8,
  "in_use": 2,
  "bridges": [
    {
      "bridge_id": 1,
      "status": "in_use",
      "tenant_id": 1,
      "tenant_name": "Acme Corp",
      "allocated_at": "2026-04-23T10:00:00Z"
    }
  ]
}
```

---

### 11. Allocate Bridge (Admin)

Allocates a bridge to a tenant.

**Endpoint:** `POST /bridges/allocate`
**Authorization:** Super Admin only

**Request Body:**
```json
{
  "tenant_id": 1
}
```

**Success Response:** `200 OK`
```json
{
  "bridge_id": 5,
  "status": "allocated",
  "tenant_id": 1,
  "allocated_at": "2026-04-23T12:00:00Z"
}
```

---

### 12. Release Bridge (Admin)

Releases a bridge back to the pool.

**Endpoint:** `POST /bridges/{bridge_id}/release`
**Authorization:** Super Admin only

**Success Response:** `200 OK`
```json
{
  "bridge_id": 5,
  "status": "available",
  "tenant_id": null,
  "allocated_at": null
}
```

---

## CIDR Validation

When creating networks, CIDR blocks are validated:

1. **Valid CIDR Format:** Must be valid IPv4 CIDR (e.g., `10.0.1.0/24`)
2. **No Overlap:** Cannot overlap with existing tenant networks
3. **Range Validation:** Static IPs must fall within network CIDR

### Network Status Values

| Status | Description |
|--------|-------------|
| `pending` | Network being provisioned |
| `active` | Network ready for VM attachment |
| `error` | Provisioning failed |
| `deleted` | Network deleted (soft delete, resources returned to pool) |

### Pod Status Values

| Status | Description |
|--------|-------------|
| `active` | Pod operational |
| `maintenance` | Pod under maintenance |
| `offline` | Pod unreachable |

---

## Error Handling

### Common Errors

**1. Network Not Found**
```json
{
  "detail": "Network not found"
}
```
Status: `404 Not Found`

**2. Pod Not Found**
```json
{
  "detail": "Pod not found"
}
```
Status: `404 Not Found`

**3. Super Admin Required**
```json
{
  "detail": "Super admin access required"
}
```
Status: `403 Forbidden`

**4. Cannot Delete Pod with Tenants**
```json
{
  "detail": "Cannot delete pod with 5 active tenant(s)"
}
```
Status: `400 Bad Request`

**5. Cannot Delete Default Network**
```json
{
  "detail": "Cannot delete the default network"
}
```
Status: `400 Bad Request`

**6. Tenant Not Provisioned**
```json
{
  "detail": "Tenant not fully provisioned - no pod assigned"
}
```
Status: `400 Bad Request`
