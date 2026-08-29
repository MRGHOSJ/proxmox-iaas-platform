# WireGuard API Documentation

This module provides WireGuard VPN tunnel and peer management for tenant networks. Tunnels are provisioned on OPNsense firewalls via asynchronous Celery tasks.

**Base URL:** `/v1/wireguard`

**Required Permissions:** `wireguard:read`, `wireguard:create`, `wireguard:update`, `wireguard:delete`

## Table of Contents
- [Tunnel CRUD](#tunnel-crud)
- [Peer CRUD](#peer-crud)
- [Status & Utilities](#status--utilities)
- [Request/Response Schemas](#requestresponse-schemas)

---

## Tunnel CRUD

### 1. List Tunnels

Lists all WireGuard tunnels for the current tenant.

**Endpoint:** `GET /wireguard/tunnels`  
**Authorization:** Authenticated user with `wireguard:read`

**Success Response:** `200 OK`
```json
{
  "total": 1,
  "tunnels": [
    {
      "id": 1,
      "tenant_id": 5,
      "name": "main-tunnel",
      "listen_port": 51820,
      "mtu": 1420,
      "dns": "1.1.1.1,8.8.8.8",
      "tunnel_address": "10.200.0.1/24",
      "cidr": "10.200.0.0/24",
      "gateway_ip": "10.200.0.1",
      "subnet_mask": 24,
      "public_key": "abc123...",
      "endpoint": "203.0.113.10:51820",
      "opt_interface": "wg1",
      "status": "active",
      "error": null,
      "peer_keepalive": 25,
      "is_enabled": true,
      "peer_count": 2,
      "allowed_network_ids": [1, 3],
      "created_at": "2025-01-15T10:30:00Z",
      "updated_at": "2025-01-15T10:30:00Z",
      "provisioned_at": "2025-01-15T10:31:00Z"
    }
  ]
}
```

---

### 2. Create Tunnel

Creates a new WireGuard tunnel and queues provisioning.

**Endpoint:** `POST /wireguard/tunnels`  
**Authorization:** Authenticated user with `wireguard:create`

**Request Body:**
```json
{
  "name": "main-tunnel",
  "listen_port": 51820,
  "mtu": 1420,
  "dns": "1.1.1.1,8.8.8.8",
  "endpoint": "203.0.113.10",
  "peer_keepalive": 25,
  "allowed_network_ids": [1, 3]
}
```

**Parameters:**
| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| `name` | string | Yes | 2-100 chars, lowercase a-z, 0-9, - | Tunnel name |
| `listen_port` | integer | No | 1024-65535 | UDP listen port (default: 51820, auto-assigned if blank) |
| `mtu` | integer | No | 1280-1500 | MTU size (default: 1420) |
| `dns` | string | No | | Comma-separated DNS servers |
| `endpoint` | string | No | | Public endpoint (auto-populated from tenant WAN IP) |
| `peer_keepalive` | integer | No | 0-65535 | Persistent keepalive seconds (default: 25) |
| `allowed_network_ids` | list[int] | Yes | min 1 | Tenant network IDs for firewall access |

**Success Response:** `201 Created`
```json
{
  "id": 1,
  "tenant_id": 5,
  "name": "main-tunnel",
  "listen_port": 51820,
  "mtu": 1420,
  "tunnel_address": "10.200.0.1/24",
  "cidr": "10.200.0.0/24",
  "gateway_ip": "10.200.0.1",
  "subnet_mask": 24,
  "public_key": "pending",
  "endpoint": "203.0.113.10:51820",
  "status": "pending",
  "peer_keepalive": 25,
  "is_enabled": true,
  "peer_count": 0,
  "allowed_network_ids": [1, 3]
}
```

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `409 Conflict` | Tunnel name already exists for this tenant |
| `429 Too Many Requests` | Network quota exceeded |
| `400 Bad Request` | Invalid or inactive network IDs, or OPNsense not configured |

---

### 3. Get Tunnel

Gets details for a specific tunnel.

**Endpoint:** `GET /wireguard/tunnels/{tunnel_id}`  
**Authorization:** Authenticated user with `wireguard:read`

**URL Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `tunnel_id` | integer | Tunnel ID |

**Success Response:** `200 OK` — Returns `WireGuardTunnelResponse` object.

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `404 Not Found` | Tunnel not found |

---

### 4. Update Tunnel

Updates tunnel configuration. Changing `allowed_network_ids` automatically reconciles OPNsense firewall rules.

**Endpoint:** `PATCH /wireguard/tunnels/{tunnel_id}`  
**Authorization:** Authenticated user with `wireguard:update`

**URL Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `tunnel_id` | integer | Tunnel ID |

**Request Body (all fields optional):**
```json
{
  "name": "renamed-tunnel",
  "mtu": 1400,
  "dns": "9.9.9.9",
  "endpoint": "203.0.113.11",
  "peer_keepalive": 30,
  "is_enabled": false,
  "allowed_network_ids": [1, 2, 3]
}
```

**Success Response:** `200 OK` — Returns updated `WireGuardTunnelResponse`.

---

### 5. Delete Tunnel

Queues async destruction of the WireGuard tunnel on OPNsense.

**Endpoint:** `DELETE /wireguard/tunnels/{tunnel_id}`  
**Authorization:** Authenticated user with `wireguard:delete`

**URL Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `tunnel_id` | integer | Tunnel ID |

**Success Response:** `202 Accepted`
```json
{
  "status": "destroying",
  "tunnel_id": 1
}
```

---

### 6. Get Tunnel Access

Returns the current effective network access for a tunnel by reading actual firewall rules on its OPT interface.

**Endpoint:** `GET /wireguard/tunnels/{tunnel_id}/access`  
**Authorization:** Authenticated user with `wireguard:read`

**URL Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `tunnel_id` | integer | Tunnel ID |

**Success Response:** `200 OK`
```json
{
  "tunnel_id": 1,
  "opt_interface": "wg1",
  "allowed_network_ids": [1, 3],
  "rules": [
    {
      "uuid": "pending-wg-1-net-1",
      "description": "WireGuard main-tunnel -> default",
      "destination_net": "10.0.1.0/24",
      "enabled": "1",
      "apply_status": "pending"
    }
  ]
}
```

---

### 7. Get Tunnel Logs

Returns logs for a tunnel (placeholder endpoint).

**Endpoint:** `GET /wireguard/tunnels/{tunnel_id}/logs`  
**Authorization:** Authenticated user with `wireguard:read`

**Success Response:** `200 OK`
```json
{
  "tunnel_id": 1,
  "logs": []
}
```

---

## Peer CRUD

### 8. List Peers

Lists all peers for a specific tunnel.

**Endpoint:** `GET /wireguard/tunnels/{tunnel_id}/peers`  
**Authorization:** Authenticated user with `wireguard:read`

**URL Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `tunnel_id` | integer | Tunnel ID |

**Success Response:** `200 OK`
```json
{
  "total": 2,
  "peers": [
    {
      "id": 1,
      "tunnel_id": 1,
      "name": "laptop",
      "public_key": "xyz789...",
      "allowed_ip": "10.200.0.2",
      "endpoint": null,
      "keepalive": 25,
      "is_enabled": true,
      "status": "active",
      "created_at": "2025-01-15T11:00:00Z",
      "updated_at": "2025-01-15T11:00:00Z"
    }
  ]
}
```

---

### 9. Create Peer

Creates a new WireGuard peer and queues provisioning. Returns a one-time `.conf` response with the full client configuration.

**Endpoint:** `POST /wireguard/tunnels/{tunnel_id}/peers`  
**Authorization:** Authenticated user with `wireguard:create`

**URL Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `tunnel_id` | integer | Tunnel ID |

**Request Body:**
```json
{
  "name": "laptop",
  "endpoint": "peer.example.com:51820",
  "keepalive": 25
}
```

**Parameters:**
| Field | Type | Required | Constraints | Description |
|-------|------|----------|-------------|-------------|
| `name` | string | Yes | 2-100 chars, lowercase a-z, 0-9, - | Peer name |
| `endpoint` | string | No | | Remote endpoint (host:port) |
| `keepalive` | integer | No | 0-65535 | Keepalive seconds (defaults to tunnel's `peer_keepalive`) |

**Success Response:** `201 Created` — Returns `WireGuardPeerResponse`.

**Error Responses:**
| Status Code | Description |
|-------------|-------------|
| `404 Not Found` | Tunnel not found |
| `400 Bad Request` | Tunnel not active |
| `409 Conflict` | Peer name already exists |

---

### 10. Get Peer

Gets details for a specific peer.

**Endpoint:** `GET /wireguard/tunnels/{tunnel_id}/peers/{peer_id}`  
**Authorization:** Authenticated user with `wireguard:read`

**Success Response:** `200 OK` — Returns `WireGuardPeerResponse` object.

---

### 11. Update Peer

Updates peer configuration.

**Endpoint:** `PATCH /wireguard/tunnels/{tunnel_id}/peers/{peer_id}`  
**Authorization:** Authenticated user with `wireguard:update`

**URL Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `tunnel_id` | integer | Tunnel ID |
| `peer_id` | integer | Peer ID |

**Request Body (all fields optional):**
```json
{
  "name": "laptop-updated",
  "endpoint": "new-endpoint.example.com:51820",
  "keepalive": 30,
  "is_enabled": false
}
```

**Success Response:** `200 OK` — Returns updated `WireGuardPeerResponse`.

---

### 12. Delete Peer

Queues async destruction of the WireGuard peer on OPNsense.

**Endpoint:** `DELETE /wireguard/tunnels/{tunnel_id}/peers/{peer_id}`  
**Authorization:** Authenticated user with `wireguard:delete`

**URL Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `tunnel_id` | integer | Tunnel ID |
| `peer_id` | integer | Peer ID |

**Success Response:** `202 Accepted`
```json
{
  "status": "destroying",
  "peer_id": 1,
  "tunnel_id": 1
}
```

---

### 13. Get Peer Config

Returns the full WireGuard client configuration for a peer. Can be called on demand to re-emit the `.conf`.

**Endpoint:** `GET /wireguard/tunnels/{tunnel_id}/peers/{peer_id}/config`  
**Authorization:** Authenticated user with `wireguard:read`

**URL Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `tunnel_id` | integer | Tunnel ID |
| `peer_id` | integer | Peer ID |

**Success Response:** `200 OK`
```json
{
  "peer_id": 1,
  "tunnel_id": 1,
  "name": "laptop",
  "config": "[Interface]\nPrivateKey = ...\nAddress = 10.200.0.2/32\nDNS = 1.1.1.1\n\n[Peer]\nPublicKey = ...\nPresharedKey = ...\nEndpoint = 203.0.113.10:51820\nAllowedIPs = 10.200.0.0/24, 10.0.1.0/24\nPersistentKeepalive = 25\n",
  "client_private_key": "...",
  "client_address": "10.200.0.2/32",
  "server_public_key": "...",
  "server_endpoint": "203.0.113.10:51820",
  "preshared_key": "...",
  "allowed_ips": "10.200.0.0/24, 10.0.1.0/24",
  "dns": "1.1.1.1"
}
```

---

## Status & Utilities

### 14. Get WireGuard Status

Returns aggregate WireGuard statistics for the current tenant.

**Endpoint:** `GET /wireguard/status`  
**Authorization:** Authenticated user with `wireguard:read`

**Success Response:** `200 OK`
```json
{
  "total_tunnels": 3,
  "active_tunnels": 2,
  "provisioning_tunnels": 1,
  "error_tunnels": 0,
  "total_peers": 5
}
```

---

### 15. List Available Networks

Lists active tenant networks available for VPN firewall access selection.

**Endpoint:** `GET /wireguard/available-networks`  
**Authorization:** Authenticated user with `wireguard:read`

**Success Response:** `200 OK`
```json
{
  "total": 2,
  "networks": [
    {
      "id": 1,
      "name": "default",
      "cidr": "10.0.1.0/24",
      "gateway_ip": "10.0.1.1",
      "vlan_id": 100,
      "is_default": true,
      "opnsense_interface": "lan"
    },
    {
      "id": 3,
      "name": "dmz",
      "cidr": "10.0.3.0/24",
      "gateway_ip": "10.0.3.1",
      "vlan_id": 300,
      "is_default": false,
      "opnsense_interface": "opt1"
    }
  ]
}
```

---

## Request/Response Schemas

### WireGuardTunnelCreate
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Tunnel name (lowercase a-z, 0-9, -) |
| `listen_port` | integer | No | UDP listen port |
| `mtu` | integer | No | MTU size |
| `dns` | string | No | DNS servers |
| `endpoint` | string | No | Public endpoint |
| `peer_keepalive` | integer | No | Keepalive seconds |
| `allowed_network_ids` | list[int] | Yes | Network IDs for firewall access |

### WireGuardTunnelUpdate
All fields optional.

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | New tunnel name |
| `mtu` | integer | New MTU |
| `dns` | string | New DNS servers |
| `endpoint` | string | New public endpoint |
| `peer_keepalive` | integer | New keepalive |
| `is_enabled` | boolean | Enable/disable tunnel |
| `allowed_network_ids` | list[int] | Replace allowed networks |

### WireGuardTunnelResponse
| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Tunnel ID |
| `tenant_id` | integer | Tenant ID |
| `name` | string | Tunnel name |
| `opnsense_server_uuid` | string | OPNsense server UUID |
| `listen_port` | integer | UDP listen port |
| `mtu` | integer | MTU |
| `dns` | string | DNS servers |
| `tunnel_address` | string | Server IP inside tunnel |
| `cidr` | string | Tunnel subnet |
| `gateway_ip` | string | Gateway IP |
| `subnet_mask` | integer | Subnet mask |
| `public_key` | string | Server public key |
| `endpoint` | string | Public endpoint |
| `opt_interface` | string | OPNsense OPT interface |
| `status` | string | `pending`, `provisioning`, `active`, `error` |
| `error` | string | Error message if status is error |
| `peer_keepalive` | integer | Default keepalive for peers |
| `is_enabled` | boolean | Enabled status |
| `peer_count` | integer | Number of peers |
| `allowed_network_ids` | list[int] | Allowed network IDs |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last update timestamp |
| `provisioned_at` | datetime | Provisioning timestamp |

### WireGuardPeerCreate
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Peer name (lowercase a-z, 0-9, -) |
| `endpoint` | string | No | Remote endpoint |
| `keepalive` | integer | No | Keepalive seconds |

### WireGuardPeerUpdate
All fields optional.

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | New peer name |
| `endpoint` | string | New endpoint |
| `keepalive` | integer | New keepalive |
| `is_enabled` | boolean | Enable/disable peer |

### WireGuardPeerResponse
| Field | Type | Description |
|-------|------|-------------|
| `id` | integer | Peer ID |
| `tunnel_id` | integer | Parent tunnel ID |
| `name` | string | Peer name |
| `public_key` | string | Peer public key |
| `allowed_ip` | string | Assigned IP |
| `endpoint` | string | Remote endpoint |
| `keepalive` | integer | Keepalive seconds |
| `is_enabled` | boolean | Enabled status |
| `status` | string | `pending`, `active`, `error` |
| `error` | string | Error message |
| `created_at` | datetime | Creation timestamp |
| `updated_at` | datetime | Last update timestamp |

### WireGuardPeerConfigResponse
| Field | Type | Description |
|-------|------|-------------|
| `peer_id` | integer | Peer ID |
| `tunnel_id` | integer | Tunnel ID |
| `name` | string | Peer name |
| `config` | string | Full WireGuard config text |
| `client_private_key` | string | Client private key |
| `client_address` | string | Client address (IP/32) |
| `server_public_key` | string | Server public key |
| `server_endpoint` | string | Server endpoint |
| `preshared_key` | string | Pre-shared key |
| `allowed_ips` | string | Allowed IPs (comma-separated) |
| `dns` | string | DNS servers |

### WireGuardStatusResponse
| Field | Type | Description |
|-------|------|-------------|
| `total_tunnels` | integer | Total tunnels |
| `active_tunnels` | integer | Active tunnels |
| `provisioning_tunnels` | integer | Tunnels being provisioned |
| `error_tunnels` | integer | Tunnels in error state |
| `total_peers` | integer | Total peers across all tunnels |
