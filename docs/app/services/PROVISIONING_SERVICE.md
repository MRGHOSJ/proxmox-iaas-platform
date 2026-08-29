# Provisioning Service Documentation

This document describes the tenant provisioning service that handles OPNsense firewall deployment and infrastructure setup.

---

## Table of Contents
- [Overview](#overview)
- [Functions](#functions)
- [Provisioning Flow](#provisioning-flow)
- [Network Architecture](#network-architecture)

---

## Overview

**File:** `app/services/provisioning.py`

The provisioning service handles:
- Bridge allocation
- Pod assignment
- OPNsense VM creation
- Network setup
- Tenant lifecycle management

---

## Functions

### allocate_bridge

Allocates a bridge from the pool to a tenant.

```python
def allocate_bridge(db: Session, tenant_id: int) -> Optional[int]:
    bridge_entry = db.query(BridgePool).filter(
        BridgePool.status == "available"
    ).with_for_update().first()

    bridge_entry.status = "in_use"
    bridge_entry.tenant_id = tenant_id
    bridge_entry.allocated_at = datetime.utcnow()

    return bridge_entry.bridge_id
```

### release_bridge

Releases a bridge back to the pool.

```python
def release_bridge(db: Session, tenant_id: int) -> bool:
    # Sets status to available, clears tenant_id
```

### assign_pod

Assigns a pod using "fill before opening" strategy.

```python
def assign_pod(db: Session):
    # Picks pod with most tenants first
    # Uses SKIP LOCKED for concurrent signups
```

### create_proxmox_bridge

Creates a Linux bridge on Proxmox host.

```python
def create_proxmox_bridge(bridge_id: int, tenant_id: int) -> str:
    provider = get_hypervisor_provider()
    result = provider.create_bridge(bridge_id, tenant_id)
    return result.bridge_name
```

### approve_tenant

Main tenant approval function.

```python
def approve_tenant(db: Session, tenant_id: int, ...) -> dict:
    # 1. Assign pod
    # 2. Allocate bridge
    # 3. Allocate IP subnet
    # 4. Create TenantNetwork
    # 5. Create Proxmox bridge
    # 6. Queue provisioning task
    # 7. Return result
```

### destroy_tenant

Starts tenant deprovisioning.

```python
def destroy_tenant(db: Session, tenant_id: int) -> dict:
    destroy_tenant_task.delay(tenant_id=tenant_id)
    return {"status": "deprovisioning_started"}
```

---

## Provisioning Flow

### Step 1: Pod Assignment

```
assign_pod(db)
```

- Selects pod with most available capacity
- Uses `FOR UPDATE SKIP LOCKED` for concurrency
- Increments `tenant_count`

### Step 2: Bridge Allocation

```
allocate_bridge(db, tenant_id)
```

- Marks bridge as `in_use`
- Records tenant assignment
- Sets allocation timestamp

### Step 3: Subnet Allocation

```
allocate_subnet(db)
```

- Allocates IP subnet from global pool
- Sets gateway IP

### Step 4: Network Creation

```
TenantNetwork(
    tenant_id=tenant.id,
    pod_id=pod.id,
    ip_pool_id=subnet.id,
    cidr=subnet.cidr,
    gateway_ip=subnet.gateway_ip,
    name="default",
    is_default=True
)
```

### Step 5: Bridge Creation

```
create_proxmox_bridge(bridge_id, tenant_id)
```

- Creates Linux bridge: `vmbr{N}`
- Configures on Proxmox host

### Step 6: Async Provisioning Task

```
provision_tenant_task.delay(
    tenant_id=tenant_id,
    pod_id=pod_id,
    bridge_id=bridge_id,
    gateway_ip=gateway_ip,
    cidr=cidr
)
```

---

## Network Architecture

### Tenant Network Topology

```
Internet
    │
    ▼
┌─────────────────────────────────────────┐
│           WAN (vmbr0)                    │
│     (External network, no VLAN)         │
└─────────────────────────────────────────┘
    │
    ▼ (NAT)
┌─────────────────────────────────────────┐
│      OPNsense Firewall VM                │
│  (WAN: 203.0.113.x, LAN: 10.0.x.1)  │
└─────────────────────────────────────────┘
    │
    ▼ (Routing)
┌─────────────────────────────────────────┐
│        LAN Bridge (vmbrN)                │
│        (Tenant VLAN, tagged)             │
└─────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────┐
│      Tenant Network (10.0.x.0/24)         │
│      VMs connect here                     │
└─────────────────────────────────────────┘
```

### DHCP

The OPNsense VM provides DHCP for tenants:

| Field | Value |
|-------|-------|
| Range | Configurable per tenant |
| Gateway | Tenant LAN IP |
| DNS | Configurable |

---

## OPNsense Configuration

### VM Settings

| Setting | Value |
|---------|-------|
| VM ID | 10000 + (bridge_id - 100) |
| Name | VM_OPNsense_{tenant_id} |
| Network | VMbrN |
| vCPUs | 2 |
| RAM | 2048 MB |
| Disk | 8 GB |

### Network Interfaces

1. **WAN** - Connected to `vmbr0` (upstream)
2. **LAN** - Connected to `vmbr{bridge_id}` (tenant)

---

## Error Handling

### Common Errors

| Error | Cause | Resolution |
|-------|-------|-----------|
| No pod capacity available | All pods at max | Add new pod |
| No bridge capacity available | Bridge pool exhausted | Add bridges |
| Failed to create bridge | Proxmox API error | Check Proxmox connectivity |
| Tenant not in valid status | Invalid tenant state | Check tenant status |

### Status Values

| Status | Description |
|--------|-------------|
| `pending` | Initial state |
| `pending_approval` | Awaiting admin approval |
| `verified` | Verified, ready for provisioning |
| `provisioning` | Actively provisioning |
| `active` | Fully operational |
| `error` | Provisioning failed |