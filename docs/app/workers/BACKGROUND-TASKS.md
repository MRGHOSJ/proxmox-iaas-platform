# Background Tasks Documentation (Celery)

## Overview

The Platform uses **Celery** for asynchronous background tasks. Tasks are organized in `app/workers/` directory with central dispatch via `task_scheduler.py`.

**Key Responsibilities:**
- Decouple long-running operations from HTTP requests
- Manage VM and tenant provisioning
- Handle network and firewall operations
- Implement retry logic for transient failures

---

## Table of Contents
- [Directory Structure](#directory-structure)
- [Celery Configuration](#celery-configuration)
- [Task Scheduler](#task-scheduler)
- [Task Files](#task-files)
- [State Management](#state-management)
- [Error Handling](#error-handling)

---

## Directory Structure

```
app/workers/
├── celery_app.py              # Celery app configuration, beat schedule
├── task_scheduler.py          # Central task re-export hub
├── tasks/
│   ├── __init__.py
│   ├── vm.py                  # VM deploy and cloud-init provisioning (327 lines)
│   ├── tenant.py              # Tenant provisioning/destruction (705 lines)
│   ├── network.py             # Network deploy/destroy tasks
│   ├── firewall_manager.py    # Firewall sync, apply, reconcile (620 lines)
│   ├── vlan.py                # VLAN provisioning via in-VM PHP (457 lines)
│   ├── wireguard.py           # WireGuard tunnel/peer provisioning (654 lines)
│   ├── kea.py                 # Kea DHCP configuration
│   ├── images.py              # Image build and template conversion
│   └── helpers.py             # Shared: logging, validation, Proxmox/OPNsense clients
├── modules/
│   ├── opnsense_config_invm.py  # In-VM PHP config.xml manipulation (775 lines)
│   └── __init__.py

```

---

## Celery Configuration

**File:** `app/workers/celery_app.py`

### Configuration

| Setting | Value | Description |
|----------|-------|-------------|
| `broker` | `settings.CELERY_BROKER_URL` | Redis for queueing |
| `backend` | `settings.CELERY_RESULT_BACKEND` | Redis for results |
| `task_serializer` | `json` | Task serialization |
| `result_serializer` | `json` | Result serialization |
| `task_acks_late` | `True` | Ack after completion |
| `task_reject_on_worker_lost` | `True` | Reject on worker death |
| `task_time_limit` | `3600` | 1 hour hard limit |
| `task_soft_time_limit` | `3300` | 55 min soft limit |
| `worker_prefetch_multiplier` | `1` | Don't block queue |

### Task Modules Loaded

- `app.workers.task_scheduler`
- `app.workers.tasks.tenant`
- `app.workers.tasks.firewall_manager`
- `app.workers.tasks.images`
- `app.workers.tasks.wireguard`

### Beat Schedule (Periodic Tasks)

| Task | Schedule | Expiry |
|------|----------|--------|
| `tasks.sync_all_wan_ips` | Every 5 minutes | 240s |
| `tasks.sync_opnsense_firewall_rules` | Every 15 minutes | 300s |

---

## Task Scheduler

**File:** `app/workers/task_scheduler.py`

Central dispatch for all async tasks. This is a re-export hub that imports all tasks from `app/workers/tasks/` for backwards compatibility.

### Task Registry

| Task | Celery Name | Source | Description |
|------|-------------|--------|-------------|
| `deploy_vm_task` | `tasks.deploy_vm` | `tasks/vm.py` | Legacy Terraform-based VM deployment |
| `provision_vm_task` | `tasks.provision_vm` | `tasks/vm.py` | Proxmox cloud-init VM provisioning |
| `provision_tenant_task` | `tasks.provision_tenant` | `tasks/tenant.py` | Full OPNsense provisioning (7-step flow) |
| `destroy_tenant_task` | `tasks.destroy_tenant` | `tasks/tenant.py` | Destroy OPNsense VM + release bridge |
| `poll_opnsense_wan_ip_task` | `tasks.poll_opnsense_wan_ip` | `tasks/tenant.py` | Poll for WAN IP after provisioning |
| `sync_all_wan_ips_task` | `tasks.sync_all_wan_ips` | `tasks/tenant.py` | Periodic WAN IP drift correction |
| `create_opnsense_vlan` | `shared_task` | `tasks/vlan.py` | End-to-end VLAN provisioning |
| `remove_opnsense_vlan` | `shared_task` | `tasks/vlan.py` | VLAN removal |
| `configure_kea_dhcp_task` | `tasks.configure_kea_dhcp` | `tasks/kea.py` | Kea DHCP configuration |
| `create_build_vm_task` | `tasks.create_build_vm` | `tasks/images.py` | Create build VM from ISO |
| `convert_build_to_template_task` | `tasks.convert_build_to_template` | `tasks/images.py` | Convert build VM to template |
| `provision_wireguard_tunnel_task` | `tasks.provision_wireguard_tunnel` | `tasks/wireguard.py` | Full tunnel provisioning (8 steps) |
| `destroy_wireguard_tunnel_task` | `tasks.destroy_wireguard_tunnel` | `tasks/wireguard.py` | Tunnel teardown |
| `provision_wireguard_peer_task` | `tasks.provision_wireguard_peer` | `tasks/wireguard.py` | Peer provisioning (5 steps) |
| `destroy_wireguard_peer_task` | `tasks.destroy_wireguard_peer` | `tasks/wireguard.py` | Peer removal |
| `apply_all_pending_rules_task` | `tasks.apply_all_pending_rules` | `tasks/firewall_manager.py` | Bulk apply pending rules + reload |
| `sync_firewall_rules_task` | `tasks.sync_firewall_rules` | `tasks/firewall_manager.py` | Sync rules from OPNsense to DB |
| `apply_firewall_rule_task` | `tasks.apply_firewall_rule` | `tasks/firewall_manager.py` | Apply single rule change |
| `reconcile_firewall_rules_task` | `tasks.reconcile_firewall_rules` | `tasks/firewall_manager.py` | Full replace: wipe DB, re-import |
| `sync_all_firewall_rules_task` | `tasks.sync_all_firewall_rules` | `tasks/firewall_manager.py` | Dispatch sync for all tenants |

---

## Task Files

### VM Tasks (`tasks/vm.py`)

| Task | Description |
|------|-------------|
| `deploy_vm_task` | Legacy Terraform-based VM deployment (Docker containers) |
| `provision_vm_task` | Proxmox cloud-init VM provisioning (modern path) |

### Tenant Tasks (`tasks/tenant.py`)

| Task | Max Retries | Time Limit | Description |
|------|-------------|------------|-------------|
| `provision_tenant_task` | 0 | 600s | Full OPNsense provisioning (clone, WAN IP, LAN IP, API wait, rotate creds, Kea DHCP, mark active) |
| `destroy_tenant_task` | 0 | 300s | Destroy tenant OPNsense VM + release bridge |
| `poll_opnsense_wan_ip_task` | 30 | - | Poll for WAN IP after provisioning |
| `sync_all_wan_ips_task` | - | 600s | Periodic WAN IP drift correction for all tenants |

### Network Tasks (`tasks/network.py`)

| Task | Description |
|------|-------------|
| `deploy_network_task` | Docker network deployment (deprecated) |
| `destroy_network_task` | Docker network destruction (deprecated) |

### Firewall Tasks (`tasks/firewall_manager.py`)

| Task | Max Retries | Description |
|------|-------------|-------------|
| `sync_firewall_rules_task` | 3 | Sync rules from OPNsense to DB (drift detection) |
| `apply_firewall_rule_task` | 3 | Apply single rule change to OPNsense |
| `apply_opnsense_firewall_task` | 5 | Trigger OPNsense firewall reload |
| `apply_all_pending_rules_task` | 3 | Bulk apply all pending rules + reorder + reload |
| `reconcile_firewall_rules_task` | 1 | Full replace: wipe DB, re-import from OPNsense |
| `sync_all_firewall_rules_task` | 3 | Dispatch sync for all active tenants (beat task) |

### VLAN Tasks (`tasks/vlan.py`)

| Task | Max Retries | Description |
|------|-------------|-------------|
| `create_opnsense_vlan` | 3 | End-to-end VLAN provisioning (add VLAN, OPT interface, ifconfig, Kea DHCP) |
| `remove_opnsense_vlan` | 2 | VLAN removal |

### WireGuard Tasks (`tasks/wireguard.py`)

| Task | Max Retries | Description |
|------|-------------|-------------|
| `provision_wireguard_tunnel_task` | 3 | Full tunnel provisioning (keypair, subnet, OPNsense server, endpoint, firewall rules) |
| `destroy_wireguard_tunnel_task` | 3 | Tunnel teardown |
| `provision_wireguard_peer_task` | 3 | Peer provisioning (keypair, PSK, OPNsense client) |
| `destroy_wireguard_peer_task` | 3 | Peer removal |

### DHCP Tasks (`tasks/kea.py`)

| Task | Max Retries | Description |
|------|-------------|-------------|
| `configure_kea_dhcp_task` | 3 | Configure Kea DHCP for tenant |

### Image Tasks (`tasks/images.py`)

| Task | Max Retries | Description |
|------|-------------|-------------|
| `create_build_vm_task` | 3 | Create build VM from ISO or disk image |
| `convert_build_to_template_task` | 3 | Stop VM, convert to template, register in DB |

### In-VM Configuration (`modules/opnsense_config_invm.py`)

The `OPNsenseConfigInVM` class (775 lines) modifies OPNsense `config.xml` **inside the running VM** using small PHP scripts:

- Writes tiny PHP scripts (~1KB) to `/tmp/` inside the VM via `exec_in_vm`
- Uses PHP `flock()` for serialization (30s lock timeout)
- Atomic writes: temp file + `rename()`
- Verification reads after every write

Key methods: `add_vlan_device()`, `add_opt_interface()`, `set_lan_ip()`, `set_wan_ip()`, `add_kea_subnet()`, `assign_wg_interface()`, `reload_config()`

---

## State Management

### VM Status Flow

```
[Create Request]
       │
       ▼
   ┌──────────┐
   │ creating │
   └────┬─────┘
        │ [Task Dispatch]
        ▼
   ┌─────────┐
   │ pending │
   └────┬────┘
        ▼
  ┌─────────────┐
  │ provisioning│
  └─────┬───┬───┘
        │   │
   ┌────┘   └────┐
   ▼             ▼
┌────────┐   ┌─────────┐
│ running│◄──┤  error  │
└────────┘   └─────────┘
```

### Tenant Status Flow

```
[Register Tenant]
       │
       ▼
   ┌────────────────┐
   │ pending_approval│
   └───────┬────────┘
           │ [Admin Verify]
           ▼
   ┌─────────────┐
   │ provisioning│
   └─────┬─────┘
         │
         ▼
   ┌──────────┐
   │  active  │
   └─────────┘
```

---

## Error Handling

### Retry Policy

| Task | Max Retries | Delay |
|------|-------------|-------|
| `deploy_vm_task` | 3 | 5s |
| `provision_vm_task` | 0 | - |
| `provision_tenant_task` | 0 | - |
| `destroy_tenant_task` | 0 | - |
| `create_opnsense_vlan` | 3 | - |
| `remove_opnsense_vlan` | 2 | 10s |
| `apply_all_pending_rules_task` | 3 | - |
| `sync_firewall_rules_task` | 3 | - |
| `provision_wireguard_tunnel_task` | 3 | - |
| `provision_wireguard_peer_task` | 3 | - |
| `create_build_vm_task` | 3 | - |
| `convert_build_to_template_task` | 3 | - |

### Cleanup on Failure

Tasks call cleanup functions on failure:

```python
def _cleanup_on_failure(db, vm, error):
    vm.status = "error"
    vm.error = str(error)
    # Release IP if allocated
    # Release resource locks
    db.commit()
```

### Logging

| Level | Event |
|-------|-------|
| `INFO` | Task start/end, state transitions |
| `DEBUG` | Detailed operations |
| `WARNING` | Non-critical failures, retries |
| `ERROR` | Task failures, unrecoverable errors |

### Log Examples

```
INFO: Task started: provision_vm for vm_id=5
INFO: Cloning template 100 to new VM
INFO: Configuring cloud-init for vm_id=5
INFO: Task completed: VM 5 is running
ERROR: Task failed for VM 5: Proxmox API error
WARNING: Retrying task (attempt 2/3)...
```