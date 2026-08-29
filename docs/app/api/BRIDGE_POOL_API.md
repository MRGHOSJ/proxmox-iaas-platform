# Bridge Pool API Documentation

This module handles bridge pool management for tenant network isolation. Bridges provide Layer 2 network segmentation.

## Table of Contents
- [Bridge Model](#bridge-model)
- [Endpoints](#endpoints)

---

## Bridge Model

| Field | Type | Description |
|-------|------|-------------|
| `bridge_id` | integer | Unique identifier |
| `status` | string | `available`, `in_use` |
| `tenant_id` | integer | Allocated tenant (nullable) |
| `allocated_at` | datetime | Allocation timestamp |

---

## Authorization

All endpoints require **Super Admin** IAM role.

---

## Endpoints

### 1. List Bridges

Lists all bridges with optional filtering.

**Endpoint:** `GET /bridges/`  
**Authorization:** Super Admin only

**Query Parameters:**
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `status_filter` | string | `null` | Filter by status |
| `skip` | integer | `0` | Pagination offset |
| `limit` | integer | `100` | Results limit |

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
    },
    {
      "bridge_id": 2,
      "status": "available",
      "tenant_id": null,
      "tenant_name": null,
      "allocated_at": null
    }
  ],
  "skip": 0,
  "limit": 100,
  "total_filtered": 10
}
```

---

### 2. Get Bridge Details

Gets a specific bridge.

**Endpoint:** `GET /bridges/{bridge_id}`  
**Authorization:** Super Admin only

**Success Response:** `200 OK`
```json
{
  "bridge_id": 1,
  "status": "in_use",
  "tenant_id": 1,
  "tenant_name": "Acme Corp",
  "allocated_at": "2026-04-23T10:00:00Z"
}
```

---

### 3. Allocate Bridge

Allocates a bridge to a tenant.

**Endpoint:** `POST /bridges/allocate`  
**Authorization:** Super Admin only

**Request Body:**
```json
{
  "tenant_id": 5
}
```

**Parameters:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tenant_id` | integer | Yes | Tenant to allocate to |

**Logic Flow:**
1. Find available bridge
2. Mark as in_use
3. Assign tenant_id
4. Set allocated_at timestamp

**Success Response:** `200 OK`
```json
{
  "bridge_id": 5,
  "status": "in_use",
  "tenant_id": 5,
  "tenant_name": "New Corp",
  "allocated_at": "2026-04-23T12:00:00Z"
}
```

---

### 4. Release Bridge

Releases a bridge back to the pool.

**Endpoint:** `POST /bridges/{bridge_id}/release`  
**Authorization:** Super Admin only

**Success Response:** `200 OK`
```json
{
  "bridge_id": 5,
  "status": "available",
  "tenant_id": null,
  "tenant_name": null,
  "allocated_at": null
}
```

---

### 5. Allocate Bridge Range

Allocates multiple bridges at once.

**Endpoint:** `POST /bridges/allocate-batch`  
**Authorization:** Super Admin only

**Request Body:**
```json
{
  "tenant_ids": [5, 6, 7],
  "vlan_start": 100
}
```

**Success Response:** `200 OK`
```json
{
  "allocated": 3,
  "bridges": [3, 4, 5]
}
```

---

## Bridge Status Values

| Status | Description |
|--------|-------------|
| `available` | Bridge available for allocation |
| `in_use` | Allocated to a tenant |

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

**2. No Available Bridges**
```json
{
  "detail": "No available bridges"
}
```
Status: `400 Bad Request`

**3. Bridge Not Found**
```json
{
  "detail": "Bridge not found"
}
```
Status: `404 Not Found`

**4. Bridge Already Allocated**
```json
{
  "detail": "Bridge is already allocated to another tenant"
}
```
Status: `400 Bad Request`