# IP Address Management (IPAM) Service Documentation

## Overview

The `app.services.ipam` module provides IP subnet and VLAN allocation from global database pools. It is a pure SQLAlchemy service (~63 lines) that uses `SELECT FOR UPDATE SKIP LOCKED` for concurrency-safe allocation without external dependencies.

**Key Responsibilities:**
1. **Subnet Allocation:** Assign `/24` subnets from global IP pools to tenant networks
2. **VLAN Allocation:** Assign VLAN IDs from per-pod pools for additional networks
3. **Concurrency Safety:** PostgreSQL row-level locking prevents allocation races

---

## Dependencies

| Library/Module | Usage |
|---------------|-------|
| `sqlalchemy.orm.Session` | Database session for pool queries |
| `app.models.network` | `GlobalIPPool`, `VlanAllocation` models |
| `logging` | Allocation/release logging |

---

## IP Pool Architecture

### Three-Tier Pool System

```
GlobalIPPool (Tier 1)
├── Safe Pool: 172.16.0.0/12 → 4,096 /24 subnets (172.16-31.x.0/24)
└── Overflow Pool: 10.0.0.0/8 → 65,536 /24 subnets

VlanAllocation (Tier 2)
└── Per-Pod: VLAN IDs 10-4094 (1-9 reserved)

WireGuardPool (Tier 3)
└── 10.200.0.0/14 → 1,024 /24 subnets
```

### Allocation Strategy

1. **Safe first:** Tries `172.16.x.x` subnets (RFC 1918 safe range)
2. **Overflow fallback:** Falls back to `10.x.x.x` if safe pool exhausted
3. **Concurrency:** `SELECT FOR UPDATE SKIP LOCKED` at PostgreSQL level

---

## Functions

### 1. Allocate Subnet

**Signature:** `def allocate_subnet(db: Session) -> dict`

**Description:**
Allocates the next free `/24` subnet from `GlobalIPPool`. Tries the `safe` pool first (`172.16-31.x.x`), falls back to `overflow` (`10.x.x.x`).

**Workflow:**
1. Queries `GlobalIPPool` where `status = 'free'` with `SELECT FOR UPDATE SKIP LOCKED`
2. Orders by `pool` (safe first) then `id`
3. Marks the row as `allocated` with timestamp
4. Returns the allocated subnet info

**Returns:** `{"id": int, "cidr": str, "gateway_ip": str, "pool": str}`

**Concurrency:** Uses `SKIP LOCKED` to skip rows locked by other transactions, preventing deadlocks.

---

### 2. Release Subnet

**Signature:** `def release_subnet(db: Session, ip_pool_id: int) -> None`

**Description:**
Returns a `/24` subnet to the `free` pool for reuse.

**Logic:**
1. Finds the `GlobalIPPool` row by `id`
2. Sets `status = 'free'`, clears `tenant_network_id` and `allocated_at`

---

### 3. Allocate VLAN

**Signature:** `def allocate_vlan(db: Session, pod_id: int) -> dict`

**Description:**
Allocates the next free VLAN ID for a specific pod from `VlanAllocation`. Only called for additional (non-default) networks.

**Logic:**
1. Queries `VlanAllocation` where `pod_id = pod_id` and `status = 'free'`
2. Uses `SELECT FOR UPDATE SKIP LOCKED`
3. Returns the allocated VLAN info

**Note:** The default LAN is untagged (`vlan_id=None`) and does NOT consume a VLAN ID.

---

### 4. Release VLAN

**Signature:** `def release_vlan(db: Session, pod_id: int, vlan_id: int) -> None`

**Description:**
Returns a VLAN ID to the pod's pool.

**Logic:**
1. Finds the `VlanAllocation` row by `pod_id` and `vlan_id`
2. Sets `status = 'free'`, clears `tenant_network_id`

---

## Concurrency Model

The service uses PostgreSQL row-level locking to prevent allocation races:

```sql
SELECT * FROM global_ip_pool
WHERE status = 'free'
ORDER BY pool, id
LIMIT 1
FOR UPDATE SKIP LOCKED
```

- **FOR UPDATE:** Locks the selected row until the transaction commits
- **SKIP LOCKED:** Skips rows already locked by other transactions
- **Result:** Multiple workers can allocate concurrently without conflicts

### Why Not Docker Subnets?

The previous implementation used `docker network inspect` to check IP availability. This was replaced with pure database allocation because:
1. Docker CLI calls are slow and unreliable under load
2. Database-level locking is more robust than OS-level checks
3. The pool is pre-seeded at startup, eliminating race conditions

---

## Seeding

The IP pool is seeded on first startup via `app/services/seed.py`:

| Pool | Range | Count |
|------|-------|-------|
| Safe | `172.16.0.0/12` → `/24` subnets | 4,096 |
| Overflow | `10.0.0.0/8` → `/24` subnets | 65,536 |
| VLANs | IDs 10-4094 per pod | 4,085/pod |
| WireGuard | `10.200.0.0/14` → `/24` subnets | 1,024 |

---

## Usage in Provisioning Flow

```
1. approve_tenant()
   └── allocate_subnet() → /24 for default LAN
   └── allocate_vlan() (only for additional networks)

2. create_network()
   └── allocate_subnet() → /24 for new network
   └── allocate_vlan() → VLAN ID for tagged network

3. delete_network()
   └── release_subnet() → return /24 to pool
   └── release_vlan() → return VLAN ID to pool
```

---

## Related Modules

| Module | Purpose |
|--------|---------|
| `app/services/wireguard_ipam.py` | WireGuard-specific IP allocation (10.200.0.0/14) |
| `app/services/quota.py` | Enforces per-tenant network count limits |
| `app/models/network.py` | `GlobalIPPool`, `VlanAllocation` model definitions |
| `app/services/seed.py` | Initial pool seeding |
