# Pydantic Schemas Documentation

This document describes the Pydantic request/response schemas used for API validation.

---

## Table of Contents
- [Overview](#overview)
- [VM Schemas](#vm-schemas)
- [User Schemas](#user-schemas)
- [Tenant Schemas](#tenant-schemas)
- [Network Schemas](#network-schemas)
- [Invitation Schemas](#invitation-schemas)
- [Firewall Rule Schemas](#firewall-rule-schemas)

---

## Overview

**Location:** `app/schemas/`

Schemas provide:
- Request validation
- Response serialization
- OpenAPI documentation
- Type safety

---

## VM Schemas

### VMBase

Base schema with common VM fields.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `name` | string | 3-50 chars | VM name (unique) |
| `description` | string | max 500 | VM description |
| `cpu` | integer | 1-32 | Number of CPU cores |
| `ram` | integer | 512-65536 MB | RAM in MB |
| `disk_size` | integer | 1-1000 GB | Disk size in GB |
| `provider` | string | docker/vsphere/proxmox | Provider |
| `image` | string | max 255 | Container/VM image |
| `firewall_rules` | list | - | Firewall rules |

### VMCreate

Schema for creating a new VM.

```python
from app.schemas.vm import VMCreate

vm_data = VMCreate(
    name="web-server",
    cpu=2,
    ram=4096,
    disk_size=20,
    provider="proxmox",
    network_id=1
)
```

### VMProvisionRequest

Schema for provisioning a Proxmox VM with cloud-init.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | Required | VM name |
| `template_id` | int | Required | Proxmox template ID |
| `cpu` | int | 1 | CPU cores |
| `ram` | int | 1024 | RAM in MB |
| `username` | string | "ubuntu" | Cloud-init username |
| `password` | string | Optional | Cloud-init password |
| `ip_mode` | string | "dhcp" | "dhcp" or "static" |
| `ip_address` | string | Optional | Static IP |
| `dns_nameservers` | list | ["8.8.8.8", "8.8.4.4"] | DNS servers |
| `ssh_public_key` | string | Optional | SSH key |
| `auto_start` | bool | true | Auto-start VM |
| `network_id` | int | Optional | Tenant network |

### VMResponse

Schema for VM responses.

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | VM ID |
| `name` | string | VM name |
| `ip_address` | string | Assigned IP |
| `status` | string | VM status |
| `provider` | string | Provider |
| `cpu` | int | CPU cores |
| `ram` | int | RAM in MB |
| `owner_id` | int | Owner user ID |
| `created_at` | datetime | Creation timestamp |

### VMSnapshotCreate

Schema for creating a VM snapshot.

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Snapshot name |
| `description` | string | Optional description |

---

## User Schemas

### UserCreate

Schema for user registration.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `username` | string | Yes | Username |
| `email` | string | Yes | Email |
| `full_name` | string | No | Full name |
| `password` | string | Yes | Password |

### UserUpdate

Schema for updating user profile.

| Field | Type | Description |
|-------|------|-------------|
| `email` | string | Email |
| `full_name` | string | Full name |

---

## Tenant Schemas

### TenantCreate

Schema for creating a tenant.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Organization name |
| `slug` | string | No | URL-friendly slug |

### TenantUpdate

Schema for updating tenant.

| Field | Type | Description |
|-------|------|-------------|
| `name` | string | Organization name |
| `is_active` | bool | Active status |

---

## Network Schemas

### NetworkCreate

Schema for creating a network.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `cidr` | string | Yes | Network CIDR |
| `gateway` | string | No | Gateway IP |
| `name` | string | No | Network name |

---

## Invitation Schemas

### InvitationCreate

Schema for creating an invitation.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `email` | string | Yes | Invited email |
| `role_id` | int | Yes | Role to assign |
| `expires_in_days` | int | No | Days until expiry |

### InvitationAccept

Schema for accepting an invitation.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `token` | string | Yes | Invitation token |
| `username` | string | No* | Username (new users only) |
| `password` | string | Yes | Password |
| `full_name` | string | No | Full name |

---

## Firewall Rule Schemas

### OPNsenseRuleBase

Base schema for OPNsense firewall rules.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | string | `"1"` | Rule enabled (`"1"`) or disabled (`"0"`) |
| `sequence` | string | null | Sort order/priority. Lower = higher in list |
| `description` | string | Required | Rule label/description |
| `interface` | string | `"lan"` | Interface key: `lan`, `wan`, `opt1`, `opt2`, ... |
| `interfacenot` | string | `"0"` | Invert interface match |
| `quick` | string | `"1"` | Stop evaluating further rules on match |
| `action` | string | `"pass"` | `pass`=allow, `block`=drop, `reject`=deny |
| `direction` | string | `"in"` | `in`=filter arriving packets, `out`=outgoing |
| `ipprotocol` | string | `"inet"` | `inet`=IPv4, `inet6`=IPv6, `inet46`=both |
| `protocol` | string | `"tcp"` | `TCP`, `UDP`, `ICMP`, or `any` |
| `source_not` | string | `"0"` | Invert source match |
| `source_net` | string | `"any"` | Source address: `any`, alias name, or CIDR |
| `source_port` | string | `"any"` | Source port: `any`, number, alias name, or range |
| `destination_not` | string | `"0"` | Invert destination match |
| `destination_net` | string | `"any"` | Destination address: `any`, alias name, or CIDR |
| `destination_port` | string | `"any"` | Destination port: `any`, number, alias, or range |
| `gateway` | string | `""` | Policy-based routing gateway (empty=default) |
| `log` | string | `"0"` | Log matching packets to firewall log |
| `statetype` | string | `"keep"` | State handling mode: `keep`, `sloppy`, `synproxy`, `none` |

### OPNsenseRuleCreate

Extends `OPNsenseRuleBase` with optional `uuid` for updates.

### OPNsenseRuleResponse

Extends `OPNsenseRuleBase` with required `uuid` field.

### OPNsenseRuleList

| Field | Type | Description |
|-------|------|-------------|
| `rules` | list[OPNsenseRuleResponse] | List of firewall rules |
| `total` | int | Total rule count |

### OPNsenseInterface

| Field | Type | Description |
|-------|------|-------------|
| `device` | string | Interface device name |
| `name` | string | Display name |
| `mac` | string | MAC address |
| `ipaddr` | string | IP address |
| `status` | string | Interface status |

---

## Validation

### VM Name Validation

VM names must:
- Start with lowercase letter
- Contain only lowercase letters, numbers, hyphens, underscores
- Be 3-50 characters
- Not start or end with hyphen/underscore
- Not contain consecutive hyphens/underscores

### Static IP Validation

If `ip_mode` is "static", `ip_address` is required.

### Provider Validation

Valid providers: `docker`, `vsphere`, `proxmox`