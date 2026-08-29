# Firewall API Documentation

This module handles firewall rule management for tenants via the OPNsense firewall appliance. It uses a **two-phase commit** pattern: rules are saved to the database first, then bulk-applied to OPNsense via Celery tasks.

## Table of Contents
- [Concepts](#concepts)
- [Authorization](#authorization)
- [Endpoints](#endpoints)

---

## Concepts

### Two-Phase Commit Pattern

```
Phase 1: Save to DB (immediate)
  POST /firewall/opnsense/rules → apply_status="pending"

Phase 2: Apply to OPNsense (async)
  POST /firewall/opnsense/apply → Celery task → OPNsense REST API
```

All rule changes are stored locally first. The user must explicitly click "Apply" to push changes to OPNsense.

### OPNsense Firewall Model

| Field | Type | Description |
|-------|------|-------------|
| `uuid` | string | OPNsense rule UUID |
| `sequence` | integer | Rule ordering (lower = higher priority) |
| `enabled` | string | "1" or "0" |
| `interface` | string | Network interface (WAN, LAN, OPT1, etc.) |
| `action` | string | "pass", "block", or "reject" |
| `direction` | string | "in" or "out" |
| `ipprotocol` | string | "inet" (IPv4), "inet6" (IPv6), "inet46" (both) |
| `protocol` | string | "tcp", "udp", "icmp", "any", etc. |
| `source_net` | string | Source CIDR (e.g., "any", "10.0.0.0/8") |
| `source_port` | string | Source port or range |
| `destination_net` | string | Destination CIDR |
| `destination_port` | string | Destination port or range |
| `gateway` | string | Gateway routing |
| `log` | string | "1" to log, "0" to disable |
| `statetype` | string | "keep", "sloppy", "synproxy", or "none" |
| `description` | string | Rule description |

### Interface Types

| Interface | Source | Description |
|-----------|--------|-------------|
| WAN | Physical | Internet-facing interface |
| LAN | Bridge | Tenant LAN bridge (vmbrN) |
| OPT1+ | VLAN/WireGuard | Additional networks (VLANs, WireGuard tunnels) |

### Apply Status

| Status | Description |
|--------|-------------|
| `synced` | Rule is in sync with OPNsense |
| `pending` | Rule created/modified, awaiting apply |
| `pending_delete` | Rule marked for deletion, awaiting apply |
| `failed` | Apply failed (check `apply_error`) |

---

## Authorization

### Permissions

| Permission | Description |
|------------|-------------|
| `firewall:create` | Create/update/reorder firewall rules |
| `firewall:read` | View firewall rules and interfaces |
| `firewall:delete` | Delete firewall rules |

### Endpoint Authorization

| Endpoint | Super Admin | Tenant Admin | Regular User |
|----------|:-----------:|:------------:|:--------:|
| `GET /firewall/providers` | ✅ | ✅ | ✅ |
| `GET /firewall/{provider}/rules` | ✅ | ✅ | ✅ |
| `POST /firewall/{provider}/rules` | ✅ | ✅ | ❌ |
| `PUT /firewall/{provider}/rules/{uuid}` | ✅ | ✅ | ❌ |
| `DELETE /firewall/{provider}/rules/{uuid}` | ✅ | ✅ | ❌ |
| `POST /firewall/{provider}/apply` | ✅ | ✅ | ❌ |
| `POST /firewall/{provider}/sync` | ✅ | ✅ | ❌ |

---

## Endpoints

### 1. List Firewall Providers

Returns available firewall providers for the current tenant.

**Endpoint:** `GET /v1/firewall/providers`

**Success Response:** `200 OK`
```json
[
  {
    "type": "opnsense",
    "name": "OPNsense",
    "active": true,
    "connected": true,
    "rule_count": 15,
    "last_sync": "2026-04-23T12:00:00Z"
  }
]
```

---

### 2. Provider Status

Returns provider status including pending rule count.

**Endpoint:** `GET /v1/firewall/providers/status`

**Success Response:** `200 OK`
```json
{
  "opnsense": {
    "active": true,
    "connected": true,
    "pending_rules": 3,
    "last_sync": "2026-04-23T12:00:00Z"
  }
}
```

---

### 3. List Firewall Rules

Lists all rules from the database (fast, no API call to OPNsense).

**Endpoint:** `GET /v1/firewall/{provider_type}/rules`
**Query Parameters:**
| Param | Type | Description |
|-------|------|-------------|
| `interface` | string | Filter by interface name |

**Success Response:** `200 OK`
```json
{
  "rules": [
    {
      "uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "sequence": 100,
      "enabled": "1",
      "interface": "WAN",
      "action": "pass",
      "direction": "in",
      "ipprotocol": "inet",
      "protocol": "tcp",
      "source_net": "any",
      "source_port": null,
      "destination_net": "172.16.0.0/24",
      "destination_port": "443",
      "description": "Allow HTTPS to web server",
      "apply_status": "synced",
      "log": "0"
    }
  ],
  "total": 1
}
```

---

### 4. List Interfaces

Lists available network interfaces for firewall rules.

**Endpoint:** `GET /v1/firewall/{provider_type}/interfaces`

**Success Response:** `200 OK`
```json
{
  "interfaces": [
    {"device": "WAN", "name": "WAN", "type": "wan"},
    {"device": "LAN", "name": "LAN", "type": "lan"},
    {"device": "OPT1", "name": "VLAN-100", "type": "vlan"},
    {"device": "OPT2", "name": "WireGuard-tunnel-1", "type": "wireguard"}
  ],
  "total": 4
}
```

---

### 5. Create Firewall Rule

Creates a rule in the database with `apply_status="pending"`. User must click Apply to push.

**Endpoint:** `POST /v1/firewall/{provider_type}/rules`
**Authorization:** `firewall:create` permission

**Request Body:**
```json
{
  "interface": "WAN",
  "action": "pass",
  "direction": "in",
  "ipprotocol": "inet",
  "protocol": "tcp",
  "source_net": "any",
  "destination_net": "172.16.0.5",
  "destination_port": "443",
  "description": "Allow HTTPS",
  "enabled": "1",
  "log": "0",
  "sequence": 100
}
```

**Success Response:** `201 Created`
```json
{
  "uuid": "new-uuid-here",
  "sequence": 100,
  "enabled": "1",
  "interface": "WAN",
  "action": "pass",
  "apply_status": "pending",
  "message": "Rule created. Click Apply to push to OPNsense."
}
```

---

### 6. Update Firewall Rule

Updates an existing rule. Sets `apply_status="pending"`.

**Endpoint:** `PUT /v1/firewall/{provider_type}/rules/{uuid}`
**Authorization:** `firewall:create` permission

**Request Body:** Same as create (all fields required)

**Success Response:** `200 OK`

---

### 7. Delete Firewall Rule

Marks a rule for deletion (`apply_status="pending_delete"`).

**Endpoint:** `DELETE /v1/firewall/{provider_type}/rules/{uuid}`
**Authorization:** `firewall:delete` permission

**Success Response:** `200 OK`
```json
{
  "message": "Rule marked for deletion. Click Apply to remove from OPNsense."
}
```

---

### 8. Move Rule Up

Swaps rule sequence with the rule above. Sets both to `apply_status="pending"`.

**Endpoint:** `POST /v1/firewall/{provider_type}/rules/{uuid}/move_up`
**Authorization:** `firewall:create` permission

**Success Response:** `200 OK`

---

### 9. Move Rule Down

Swaps rule sequence with the rule below. Sets both to `apply_status="pending"`.

**Endpoint:** `POST /v1/firewall/{provider_type}/rules/{uuid}/move_down`
**Authorization:** `firewall:create` permission

**Success Response:** `200 OK`

---

### 10. Toggle Rule

Toggles rule enabled/disabled. Sets `apply_status="pending"`.

**Endpoint:** `POST /v1/firewall/{provider_type}/rules/{uuid}/toggle`
**Authorization:** `firewall:create` permission

**Success Response:** `200 OK`

---

### 11. Apply All Pending Changes

Triggers a Celery task to push all pending rules to OPNsense.

**Endpoint:** `POST /v1/firewall/{provider_type}/apply`
**Authorization:** `firewall:create` permission

**Logic Flow:**
1. Dispatches `apply_all_pending_rules_task` to Celery
2. Task processes: deletes → updates → creates (in order)
3. Reorders rules to match desired sequence
4. Calls `apply_rules()` on OPNsense to reload firewall
5. Deletes successfully-deleted rules from DB

**Success Response:** `202 Accepted`
```json
{
  "message": "Firewall apply task dispatched",
  "task_id": "celery-task-id"
}
```

---

### 12. Sync from OPNsense

Pulls all rules from OPNsense into the local database.

**Endpoint:** `POST /v1/firewall/{provider_type}/sync`
**Authorization:** `firewall:create` permission

**Logic Flow:**
1. Dispatches `sync_firewall_rules_task` to Celery
2. Task fetches rules from OPNsense REST API
3. Compares with DB rules
4. Detects: externally added, modified, or deleted rules
5. Updates DB to match OPNsense state
6. Logs every change as an audit event

**Success Response:** `202 Accepted`
```json
{
  "message": "Firewall sync task dispatched",
  "task_id": "celery-task-id"
}
```

---

## Periodic Sync

Two Celery beat tasks run automatically:

| Task | Schedule | Purpose |
|------|----------|---------|
| `sync_all_wan_ips` | Every 5 min | Detect WAN IP changes (DHCP) |
| `sync_opnsense_firewall_rules` | Every 15 min | Detect external rule changes |

---

## Error Handling

### Common Errors

**1. Provider Not Found**
```json
{"detail": "Firewall provider 'xyz' not found"}
```
Status: `404 Not Found`

**2. Rule Not Found**
```json
{"detail": "Rule not found"}
```
Status: `404 Not Found`

**3. Permission Denied**
```json
{"detail": "Not enough permissions"}
```
Status: `403 Forbidden`

**4. Apply Failed**
```json
{
  "detail": "Firewall apply failed",
  "error": "OPNsense API timeout"
}
```
Status: `500 Internal Server Error`
