# Proxmox Orchestration & Infrastructure Automation - Technical Documentation

This document provides comprehensive technical documentation for the Platform architecture, API, and infrastructure.

---

## Table of Contents
- [Overview](#overview)
- [Architecture](#architecture)
- [API Endpoints](#api-endpoints)
- [Data Models](#data-models)
- [Services](#services)
- [Security](#security)
- [Directory Structure](#directory-structure)

---

## Overview

This is a multi-tenant Infrastructure Automation platform built on:

- **FastAPI** - RESTful API framework
- **SQLAlchemy + PostgreSQL** - Database ORM
- **Celery + Redis** - Background task processing
- **Proxmox VE** - Virtualization provider (KVM-based)
- **OPNsense** - Per-tenant firewall appliance
- **Terraform** - Infrastructure-as-Code provisioning
- **Packer** - Image/template building
- **JWT + bcrypt** - Authentication

### Key Features

| Feature | Implementation |
|---------|----------------|
| Multi-tenancy | Tenant-based isolation with OPNsense firewalls, per-tenant bridges, and IP subnets |
| VM Provisioning | Proxmox with cloud-init, SSH key injection, snapshot management |
| Access Control | Permission-based IAM with 30+ granular permissions, multi-role per user per tenant |
| Networking | TenantNetworks, VLANs, IPAM with PostgreSQL advisory locks |
| Firewalls | OPNsense-based with two-phase commit (pending → apply) |
| VPN | WireGuard tunnels with per-tenant IP pools |
| Real-time | WebSocket updates via Redis pub/sub bridge |
| Secrets | HashiCorp Vault integration with env-var fallback |
| Image Management | Packer-based template builds, Proxmox template registry |

---

## Architecture

### Multi-Tenant Model

```
Tenant
    ├── Users (owned)
    │   └── UserRoles (IAM)
    ├── VMs (owned)
    ├── TenantNetworks (owned)
    │   └── GlobalIPPool
    ├── OPNsense Firewall VM (per-tenant)
    ├── Pod (assigned)
    │   └── VlanAllocations
    ├── WireGuard Tunnels (owned)
    │   └── WireGuardPeers
    └── OPNsenseFirewallRules (owned)
```

### Network Topology (Per Tenant)

```
Internet
    │
    ▼
[Shared WAN Bridge vmbr0] ─── DHCP WAN IP
    │
    ▼
[OPNsense Firewall VM] ─── NAT/Firewall
    │
    ▼
[Tenant LAN Bridge vmbrN] ─── 172.x.x.0/24
    │
    ▼
[Tenant VMs] ─── Static/DHCP IPs
```

### Status Lifecycle

| Resource | States |
|----------|--------|
| VM | pending → creating → provisioning → running → stopped → error |
| Tenant | pending → pending_approval → verified → provisioning → active |
| Tenant | → suspended → deprovisioned → error |
| WireGuard Tunnel | pending → provisioning → active → error |
| Network | active → deleted |

---

## API Endpoints

### Authentication (`/v1/auth`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/v1/auth/register` | Register user + tenant |
| POST | `/v1/auth/login` | Get JWT token |
| POST | `/v1/auth/logout` | Blacklist token |
| GET | `/v1/auth/me` | Current user profile |
| PATCH | `/v1/auth/me` | Update profile |
| POST | `/v1/auth/me/change-password` | Change password |

### Virtual Machines (`/v1/vm`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/vm/list` | List VMs (paginated) |
| POST | `/v1/vm/provision` | Provision Proxmox VM (cloud-init) |
| POST | `/v1/vm/create` | Legacy Terraform-based VM |
| GET | `/v1/vm/stats/summary` | VM statistics dashboard |
| GET | `/v1/vm/{id}` | Get VM details |
| PATCH | `/v1/vm/{id}` | Update VM metadata |
| DELETE | `/v1/vm/{id}` | Delete VM (supports `?force=true`) |
| POST | `/v1/vm/{id}/start` | Start VM |
| POST | `/v1/vm/{id}/stop` | Stop VM |
| POST | `/v1/vm/{id}/restart` | Restart VM |
| GET | `/v1/vm/{id}/logs` | Get VM logs |
| POST | `/v1/vm/{id}/snapshots` | Create snapshot |
| GET | `/v1/vm/{id}/snapshots` | List snapshots |
| POST | `/v1/vm/{id}/snapshots/{sid}/restore` | Restore snapshot |
| DELETE | `/v1/vm/{id}/snapshots/{sid}` | Delete snapshot |
| GET | `/v1/vm/{id}/resources` | Get live CPU/RAM/disk |
| POST | `/v1/vm/{id}/resize-cpu` | Resize CPU cores |
| POST | `/v1/vm/{id}/resize-ram` | Resize RAM |
| POST | `/v1/vm/{id}/resize-disk` | Resize disk |
| GET | `/v1/vm/{id}/disk-info` | Get disk configuration |
| GET | `/v1/vm/storage-info` | Get storage pool info |
| POST | `/v1/vm/{id}/console` | Create VNC/serial console session |
| DELETE | `/v1/vm/{id}/console` | Disconnect console session |
| WS | `/v1/vm/ws/console/{token}` | WebSocket console proxy |
| GET | `/v1/vm/{id}/ssh-info` | Get SSH connection info |
| GET | `/v1/vm/{id}/ssh-key` | Download SSH private key |
| POST | `/v1/vm/{id}/ssh-key/regenerate` | Regenerate SSH keypair |

### Networks (`/v1/networks`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/networks/` | List tenant networks |
| GET | `/v1/networks/{id}` | Get network details |
| GET | `/v1/networks/{id}/logs` | Get network logs |
| POST | `/v1/networks/` | Create additional network |
| DELETE | `/v1/networks/{id}` | Delete non-default network |

### Firewall (`/v1/firewall`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/firewall/providers` | List firewall providers |
| GET | `/v1/firewall/providers/status` | Provider status + pending count |
| GET | `/v1/firewall/{provider}/rules` | List rules (DB, fast) |
| GET | `/v1/firewall/{provider}/interfaces` | List interfaces (WAN/LAN/VLAN/WG) |
| POST | `/v1/firewall/{provider}/rules` | Create rule (pending) |
| PUT | `/v1/firewall/{provider}/rules/{uuid}` | Update rule (pending) |
| DELETE | `/v1/firewall/{provider}/rules/{uuid}` | Delete rule (pending) |
| POST | `/v1/firewall/{provider}/rules/{uuid}/move_up` | Reorder rule up |
| POST | `/v1/firewall/{provider}/rules/{uuid}/move_down` | Reorder rule down |
| POST | `/v1/firewall/{provider}/rules/{uuid}/toggle` | Toggle enabled/disabled |
| POST | `/v1/firewall/{provider}/apply` | Apply all pending changes |
| POST | `/v1/firewall/{provider}/sync` | Sync from OPNsense to DB |

### WireGuard VPN (`/v1/wireguard`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/wireguard/tunnels` | List tunnels |
| POST | `/v1/wireguard/tunnels` | Create tunnel |
| GET | `/v1/wireguard/tunnels/{id}` | Get tunnel details |
| PATCH | `/v1/wireguard/tunnels/{id}` | Update tunnel |
| DELETE | `/v1/wireguard/tunnels/{id}` | Delete tunnel |
| GET | `/v1/wireguard/tunnels/{id}/peers` | List peers |
| POST | `/v1/wireguard/tunnels/{id}/peers` | Add peer |
| PATCH | `/v1/wireguard/tunnels/{id}/peers/{pid}` | Update peer |
| DELETE | `/v1/wireguard/tunnels/{id}/peers/{pid}` | Delete peer |
| GET | `/v1/wireguard/tunnels/{id}/peers/{pid}/config` | Get peer .conf |

### Images (`/v1/images`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/images` | List images |
| GET | `/v1/images/{id}` | Get image details |
| POST | `/v1/images` | Register image |
| PATCH | `/v1/images/{id}` | Update image |
| DELETE | `/v1/images/{id}` | Delete image |
| POST | `/v1/images/{id}/tenants` | Assign to tenants |
| POST | `/v1/images/build` | Start template build |
| GET | `/v1/images/builds` | List builds |
| GET | `/v1/images/templates` | List Proxmox templates |
| GET | `/v1/images/categories` | List categories |

### Tenants (`/v1/tenants`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/tenants/my-tenants` | Get my tenants |
| GET | `/v1/tenants/` | List all tenants (admin) |
| GET | `/v1/tenants/{id}` | Get tenant details |
| POST | `/v1/tenants/` | Create tenant |
| PATCH | `/v1/tenants/{id}` | Update tenant |
| DELETE | `/v1/tenants/{id}` | Delete tenant |
| POST | `/v1/tenants/{id}/verify` | Verify + provision tenant |
| GET | `/v1/tenants/{id}/quota` | Get quota |
| PATCH | `/v1/tenants/{id}/quota` | Update quota |
| GET | `/v1/tenants/{id}/topology` | Full network topology |
| GET | `/v1/tenants/{id}/networks` | Tenant networks |
| GET | `/v1/tenants/{id}/vms` | Tenant VMs |
| GET | `/v1/tenants/users` | All users (admin) |
| PATCH | `/v1/tenants/users/{id}/ban` | Ban/unban user |
| GET | `/v1/tenants/unverified` | Unverified tenants |

### IAM (`/v1/iam`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/iam/permissions` | List all permissions |
| GET | `/v1/iam/tenants/{id}/roles` | List tenant roles |
| POST | `/v1/iam/tenants/{id}/roles` | Create custom role |
| PATCH | `/v1/iam/tenants/{id}/roles/{rid}` | Update role |
| DELETE | `/v1/iam/tenants/{id}/roles/{rid}` | Delete role |
| POST | `/v1/iam/tenants/{id}/users/{uid}/roles` | Assign role |
| DELETE | `/v1/iam/tenants/{id}/users/{uid}/roles/{rid}` | Remove role |

### Invitations (`/v1/invitations`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/invitations/validate/{token}` | Validate invitation token |
| POST | `/v1/invitations/accept` | Accept invitation |

### Admin (`/v1/admin`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/admin/audit` | Audit infrastructure state |
| GET | `/v1/admin/audit-logs` | Query audit logs |
| POST | `/v1/admin/reconcile` | Full reconciliation |
| POST | `/v1/admin/fix/{vm_id}` | Fix ghost/error VM |
| PATCH | `/v1/admin/vm/{vm_id}/status` | Override VM status |
| POST | `/v1/admin/tenant/{id}/approve` | Approve tenant (provisions OPNsense) |
| DELETE | `/v1/admin/tenant/{id}` | Delete tenant + destroy VM |
| GET | `/v1/admin/resources` | System resource usage |
| GET | `/v1/admin/health` | Infrastructure health |
| GET | `/v1/admin/activity` | Recent activity feed |
| POST | `/v1/admin/impersonate/start` | Start impersonation |
| POST | `/v1/admin/impersonate/end` | End impersonation |

### Bridge Pool (`/v1/bridges`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/v1/bridges` | List bridges |
| GET | `/v1/bridges/stats` | Pool statistics |
| POST | `/v1/bridges/{id}/release` | Release bridge |

### Health & WebSocket

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/` | API info |
| GET | `/health` | Basic health check |
| GET | `/health/ready` | Readiness (DB check) |
| GET | `/health/live` | Liveness check |
| GET | `/health/full` | Full health (admin only) |
| WS | `/ws` | Real-time status updates |

---

## Data Models

### Core Models

| Model | Table | Description |
|-------|-------|-------------|
| User | `users` | Authentication, owned by tenant |
| Tenant | `tenants` | Organization with OPNsense firewall |
| VM | `vms` | Virtual machine with lifecycle state |
| VMSnapshot | `vm_snapshots` | VM backup snapshots |
| VMDiskResize | `vm_disk_resizes` | Disk resize audit trail |
| TenantNetwork | `tenant_networks` | Virtual network per tenant |
| Pod | `pods` | Infrastructure host group |

### Network Models

| Model | Table | Description |
|-------|-------|-------------|
| GlobalIPPool | `global_ip_pool` | IP address pool (172.16.x.x + 10.x.x.x) |
| VlanAllocation | `vlan_allocations` | VLAN IDs per pod (10-4094) |
| IPReservation | `ip_reservations` | Reserved/confirmed IP assignments |
| BridgePool | `bridge_pool` | Linux bridge IDs (100-4094) |

### IAM Models

| Model | Table | Description |
|-------|-------|-------------|
| Permission | `permissions` | Granular resource:action permissions |
| Role | `iam_roles` | Preset + custom tenant roles |
| UserRole | `user_roles` | Multi-role assignment per tenant |

### Firewall Models

| Model | Table | Description |
|-------|-------|-------------|
| OPNsenseFirewallRule | `opnsense_firewall_rules` | OPNsense firewall rules |
| FirewallProviderConfig | `firewall_provider_configs` | Provider API credentials |

### WireGuard Models

| Model | Table | Description |
|-------|-------|-------------|
| WireGuardPool | `wireguard_ip_pool` | /24 subnets for tunnels |
| WireGuardTunnel | `wireguard_tunnels` | Per-tenant WireGuard tunnels |
| WireGuardPeer | `wireguard_peers` | Tunnel peers with encrypted keys |

### Image Models

| Model | Table | Description |
|-------|-------|-------------|
| ImageTemplate | `image_templates` | Proxmox template registry |
| TenantImage | `tenant_images` | Tenant-to-image assignments |
| ImageBuild | `image_builds` | Build pipeline tracking |

### Audit Models

| Model | Table | Description |
|-------|-------|-------------|
| AuditLog | `audit_logs` | Immutable audit trail |
| Invitation | `invitations` | Tenant invitations with tokens |

---

## Services

| Service | File | Purpose |
|---------|------|---------|
| VM Logic | `services/vm.py` | VM CRUD, lifecycle ops, snapshots |
| IPAM | `services/ipam.py` | Subnet and VLAN allocation from pools |
| WireGuard IPAM | `services/wireguard_ipam.py` | WireGuard tunnel/peer IP allocation |
| Quota | `services/quota.py` | Per-tenant resource quota enforcement |
| Provisioning | `services/provisioning.py` | Tenant approval/destroy orchestration |
| Terraform | `services/terraform.py` | HCL rendering, job execution, cleanup |
| Reconciler | `services/reconciler.py` | Drift detection and auto-repair |
| Seed | `services/seed.py` | Database seed data (pods, pools) |

---

## Security

### Authentication

- **JWT** tokens with `jti` for blacklisting (30min expiry)
- **SHA256 pre-hash + bcrypt** password hashing (prevents 72-char truncation)
- **Token blacklist** dual-backend: Redis (distributed) + in-memory (fallback)

### Authorization

- **Permission-based** access control (PBAC) with 30+ permissions
- **Multi-role** per user per tenant
- **Wildcard** `*` for super_admin
- Preset roles: `super_admin`, `tenant_admin`, `vm_admin`, `vm_operator`, `viewer`
- Custom roles per tenant

### Secrets

- **HashiCorp Vault** integration (optional, AppRole auth)
- **Environment variable** fallback for all config values
- **Fernet encryption** for WireGuard private keys at rest
- **ED25519 SSH** keypairs for VM access

### Rate Limiting

- Redis sorted sets for distributed rate limiting
- Fail-closed in production (503 if Redis down)
- Tiered: admin (stricter), resource-intensive (half limits), default

### Audit

- 50+ action types tracked
- Immutable audit logs with request ID correlation
- Actor, target, old/new values, IP address

---

## Directory Structure

```
app/
├── api/                      # FastAPI routers
│   ├── auth.py               # Authentication (register, login, logout, profile)
│   ├── vm.py                 # VM CRUD, lifecycle, snapshots, console, SSH
│   ├── networks.py           # Tenant network management
│   ├── firewall_manager.py   # OPNsense firewall rules (two-phase commit)
│   ├── wireguard.py          # WireGuard tunnels and peers
│   ├── tenant.py             # Tenant CRUD, verification, topology
│   ├── iam.py                # IAM roles, permissions, user-role assignments
│   ├── invitations.py        # Invitation validation and acceptance
│   ├── bridge_pool.py        # Bridge allocation pool
│   ├── images.py             # Image/template management and builds
│   ├── pods.py               # Pod management
│   └── admin.py              # Admin audit, reconcile, impersonation
├── core/                     # Core infrastructure
│   ├── config.py             # Settings (Vault → env var resolution)
│   ├── database.py           # SQLAlchemy engine, sessions, health checks
│   ├── security.py           # bcrypt hashing, JWT create/verify
│   ├── dependencies.py       # FastAPI deps (auth, tenant context, impersonation)
│   ├── audit.py              # Audit logging (50+ action types)
│   ├── cache.py              # In-memory console session stores
│   ├── celery.py             # Deprecated: re-exports from workers
│   ├── crypto.py             # Fernet field-level encryption
│   ├── exceptions.py         # Custom exception classes
│   ├── rate_limit.py         # Redis + in-memory rate limiting
│   ├── ssh.py                # ED25519 SSH keypair generation
│   ├── token_blacklist.py    # JWT blacklist (Redis + in-memory)
│   ├── vault.py              # HashiCorp Vault integration
│   ├── websocket.py          # WebSocket manager + Redis pub/sub bridge
│   └── iam/                  # IAM system
│       ├── __init__.py       # Permission checking, FastAPI dependencies
│       ├── permissions.py    # Permission definitions (30+ strings)
│       └── seed.py           # Role/permission seeding
├── models/                   # SQLAlchemy models
│   ├── user.py               # User
│   ├── tenant.py             # Tenant (with OPNsense fields)
│   ├── vm.py                 # VM, VMSnapshot, VMDiskResize
│   ├── network.py            # Pod, TenantNetwork, GlobalIPPool, VlanAllocation
│   ├── ip_reservation.py     # IPReservation
│   ├── iam.py                # Permission, Role, UserRole
│   ├── invitation.py         # Invitation
│   ├── audit_log.py          # AuditLog
│   ├── wireguard.py          # WireGuardPool, WireGuardTunnel, WireGuardPeer
│   ├── opnsense_firewall_rule.py  # OPNsenseFirewallRule
│   ├── firewall_provider_config.py # FirewallProviderConfig
│   ├── bridge_pool.py        # BridgePool
│   └── image.py              # ImageTemplate, TenantImage, ImageBuild
├── schemas/                  # Pydantic request/response schemas
│   ├── vm.py                 # VMCreate, VMProvisionRequest, VMResponse, etc.
│   ├── user.py               # UserCreate, UserResponse, Token, etc.
│   ├── tenant.py             # TenantResponse, QuotaSettings, etc.
│   ├── network.py            # TenantNetworkCreate, TenantNetworkResponse
│   ├── opnsense_firewall.py  # OPNsenseRuleCreate/Update/Response
│   ├── wireguard.py          # Tunnel/Peer create/update/response
│   ├── pod.py                # PodCreate/Update/Response
│   └── invitation.py         # InvitationCreate/Accept
├── services/                 # Business logic
│   ├── vm.py                 # VM CRUD, lifecycle ops
│   ├── ipam.py               # Subnet/VLAN allocation from pools
│   ├── wireguard_ipam.py     # WireGuard IP allocation
│   ├── quota.py              # Resource quota enforcement
│   ├── provisioning.py       # Tenant approval/destroy orchestration
│   ├── terraform.py          # HCL rendering, Terraform execution
│   ├── reconciler.py         # Drift detection, auto-repair
│   └── seed.py               # Database seeding (pods, IP pools, VLANs)
├── workers/                  # Celery background tasks
│   ├── celery_app.py         # Celery configuration, beat schedule
│   ├── task_scheduler.py     # Central task re-export hub
│   ├── tasks/
│   │   ├── tenant.py         # Tenant provisioning/destruction (705 lines)
│   │   ├── vm.py             # VM deploy and cloud-init provisioning
│   │   ├── network.py        # Network deploy/destroy tasks
│   │   ├── firewall_manager.py # Firewall sync, apply, reconcile (620 lines)
│   │   ├── vlan.py           # VLAN provisioning via in-VM PHP (457 lines)
│   │   ├── wireguard.py      # WireGuard tunnel/peer provisioning (654 lines)
│   │   ├── kea.py            # Kea DHCP configuration
│   │   ├── images.py         # Image build and template conversion
│   │   └── helpers.py        # Shared: logging, validation, Proxmox/OPNsense clients
│   └── modules/
│       └── opnsense_config_invm.py  # In-VM PHP config.xml manipulation (775 lines)
├── providers/                # Provider abstraction layer
│   ├── __init__.py           # Factory functions (get_hypervisor_provider, etc.)
│   ├── base.py               # Abstract classes: HypervisorProvider, ContainerProvider, etc.
│   ├── proxmox.py            # ProxmoxProvider (1546 lines, raw HTTP API)
│   └── firewall_provider.py  # OPNsenseFirewallProvider (REST API via exec_in_vm)
├── terraform/                # Terraform templates and init scripts
│   ├── templates/            # Jinja2 HCL templates
│   └── init/                 # Cloud-init scripts
└── tests/                    # Test suite (~230+ tests)
    ├── conftest.py           # Fixtures, DB setup, auth helpers
    ├── test_auth.py          # Auth integration tests
    ├── test_vm.py            # VM CRUD integration tests
    ├── test_network.py       # Network integration tests
    ├── test_admin.py         # Admin integration tests
    ├── test_tenant_isolation.py  # Multi-tenant isolation tests
    ├── test_schemas.py       # Pydantic validation tests
    ├── test_security.py      # bcrypt/JWT unit tests
    ├── test_ipam.py          # IP allocation unit tests
    ├── test_terraform.py     # Terraform utility tests
    ├── test_vm_service.py    # VM service logic tests
    ├── test_reconciler.py    # Reconciler tests
    ├── test_tasks.py         # Celery task tests
    ├── test_factory.py       # Provider factory tests
    ├── test_rate_limit.py    # Rate limiting tests
    ├── test_token_blacklist.py   # Token blacklist tests
    └── test_exceptions.py    # Custom exception tests
```

---

## Environment Variables

### Core (Required)

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | - | PostgreSQL connection string |
| `JWT_SECRET_KEY` | - | JWT signing key (HS256) |
| `CELERY_BROKER_URL` | - | Redis broker URL |
| `CELERY_RESULT_BACKEND` | - | Redis result backend URL |

### Proxmox (Required)

| Variable | Default | Description |
|----------|---------|-------------|
| `PROXMOX_URL` | `https://YOUR_PROXMOX_HOST:8006` | Proxmox API URL |
| `PROXMOX_USERNAME` | `root@pam` | Proxmox API token user |
| `PROXMOX_TOKEN` | - | Proxmox API token secret |
| `PROXMOX_NODE` | `pve` | Proxmox node name |
| `PROXMOX_STORAGE` | `local-lvm` | Default storage pool |

### OPNsense (Required for tenant provisioning)

| Variable | Default | Description |
|----------|---------|-------------|
| `OPNSENSE_TEMPLATE_ID` | `9000` | VM ID of OPNsense template |
| `OPNSENSE_BOOTSTRAP_KEY` | - | Initial API key for OPNsense |
| `OPNSENSE_BOOTSTRAP_SECRET` | - | Initial API secret for OPNsense |

### Security

| Variable | Default | Description |
|----------|---------|-------------|
| `WIREGUARD_FIELD_ENCRYPTION_KEY` | - | Fernet key for WireGuard secrets |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | JWT token lifetime |

### Application

| Variable | Default | Description |
|----------|---------|-------------|
| `ENVIRONMENT` | `development` | `development` or `production` |
| `ALLOW_REGISTRATION` | `false` | Enable public registration |
| `ALLOWED_EMAIL_DOMAINS` | - | Comma-separated email whitelist |
| `CREATE_DEFAULT_ADMIN` | `false` | Auto-create admin on first run |
| `DEFAULT_ADMIN_USERNAME` | `admin` | Default admin username |
| `DEFAULT_ADMIN_PASSWORD` | - | Default admin password |
| `DEFAULT_ADMIN_EMAIL` | - | Default admin email |
| `REQUEST_MAX_BODY_SIZE` | `10485760` | Max request body (10MB) |

### Rate Limiting

| Variable | Default | Description |
|----------|---------|-------------|
| `RATE_LIMIT_ENABLED` | `true` | Enable rate limiting |
| `RATE_LIMIT_REQUESTS` | `5` | Max requests per window |
| `RATE_LIMIT_PERIOD_SECONDS` | `60` | Rate limit window |
| `RATE_LIMIT_ADMIN_REQUESTS` | `10` | Stricter admin limit |

### Database Pool

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_POOL_SIZE` | `10` | Connection pool size |
| `DATABASE_MAX_OVERFLOW` | `20` | Max overflow connections |
| `DATABASE_POOL_RECYCLE` | `3600` | Recycle time (seconds) |

### WireGuard

| Variable | Default | Description |
|----------|---------|-------------|
| `WIREGUARD_DEFAULT_LISTEN_PORT` | `51820` | Default listen port |
| `WIREGUARD_DEFAULT_MTU` | `1420` | Default MTU |
| `WIREGUARD_GLOBAL_POOL_CIDR` | `10.200.0.0/14` | Global WireGuard pool |

### HashiCorp Vault (Optional)

| Variable | Default | Description |
|----------|---------|-------------|
| `VAULT_ADDR` | - | Vault server address |
| `VAULT_ROLE_ID` | - | AppRole role ID |
| `VAULT_SECRET_ID` | - | AppRole secret ID |

---

## Error Responses

Standard HTTP status codes:

| Code | Meaning |
|-------|---------|
| 200 | OK |
| 201 | Created |
| 202 | Accepted (async task dispatched) |
| 204 | No Content |
| 400 | Bad Request |
| 401 | Unauthorized |
| 403 | Forbidden |
| 404 | Not Found |
| 409 | Conflict |
| 413 | Request Entity Too Large |
| 429 | Too Many Requests |
| 500 | Internal Server Error |
| 503 | Service Unavailable |
