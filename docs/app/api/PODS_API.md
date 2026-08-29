# Pods API Documentation

This module handles Pod infrastructure management. Pods represent physical or virtual infrastructure hosts that host tenant workloads.

## Table of Contents
- [Pod Model](#pod-model)
- [Endpoints](#endpoints)

---

## Pod Model

| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Unique identifier |
| `name` | string | Pod name |
| `provider_type` | string | `proxmox`, `vsphere`, `kvm`, `hyperv` |
| `node_names` | string | Comma-separated node names |
| `max_tenants` | integer | Maximum tenants supported |
| `tenant_count` | integer | Current tenant count |
| `status` | string | `active`, `maintenance`, `offline` |

---

## Authorization

All endpoints require **Super Admin** IAM role.

---

## Endpoints

### 1. List Pods

Retrieves all pods.

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

### 2. Get Pod Details

Retrieves a specific pod.

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

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `400 Bad Request` | Invalid provider_type |
| `400 Bad Request` | Pod name already exists |

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

### 6. Get Pod VLANs

Gets VLAN allocations for a pod.

**Endpoint:** `GET /admin/pods/{pod_id}/vlans`  
**Authorization:** Super Admin only

**Success Response:** `200 OK`
```json
{
  "pod_id": 1,
  "pod_name": "pod-01",
  "vlans": [
    {
      "vlan_id": 100,
      "status": "allocated",
      "tenant_network_id": 1
    },
    {
      "vlan_id": 101,
      "status": "free",
      "tenant_network_id": null
    }
  ]
}
```

---

## Pod Status Values

| Status | Description |
|--------|-------------|
| `active` | Pod operational |
| `maintenance` | Pod under maintenance |
| `offline` | Pod unreachable |

---

## Provider Types

| Type | Description |
|------|-------------|
| `proxmox` | Proxmox VE |
| `vsphere` | VMware vSphere |
| `kvm` | KVM/QEMU |
| `hyperv` | Microsoft Hyper-V |

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

**2. Pod Not Found**
```json
{
  "detail": "Pod not found"
}
```
Status: `404 Not Found`

**3. Invalid Provider Type**
```json
{
  "detail": "Invalid provider_type. Must be one of: proxmox, vsphere, kvm, hyperv"
}
```
Status: `400 Bad Request`

**4. Cannot Delete Pod with Tenants**
```json
{
  "detail": "Cannot delete pod with 5 active tenant(s). Please reassign or delete tenants first."
}
```
Status: `400 Bad Request`