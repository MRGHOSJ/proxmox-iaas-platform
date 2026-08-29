# Provider Abstraction Layer

This module provides abstract interfaces and implementations for infrastructure providers, enabling support for multiple hypervisor platforms. It decouples application logic from specific infrastructure (Proxmox, vSphere, KVM, Hyper-V) using abstract base classes and a factory pattern.

## Table of Contents
- [Overview](#overview)
- [Module Structure](#module-structure)
- [Base Interfaces](#base-interfaces)
  - [Provider Types](#provider-types)
  - [Abstract Classes](#abstract-classes)
  - [Data Models](#data-models)
- [Proxmox Implementation](#proxmox-implementation)
- [Firewall Providers](#firewall-providers)
- [Factory Methods](#factory-methods)
- [Error Handling](#error-handling)

---

## Overview

The Provider layer acts as a driver for infrastructure operations. It defines standard interfaces that must be implemented for any target platform.

**Key Benefits:**
- **Decoupling:** Business logic remains agnostic of the infrastructure
- **Extensibility:** New providers added by implementing abstract base classes
- **Testability:** Interfaces can be mocked for unit testing
- **Multi-provider:** Support multiple hypervisors simultaneously via Pod assignment

## Module Structure

```
app/providers/
├── __init__.py           # Factory functions, public API exports
├── base.py               # Abstract classes and data models
├── proxmox.py            # ProxmoxProvider (1546 lines)
└── firewall_provider.py  # OPNsenseFirewallProvider (450+ lines)
```

---

## Base Interfaces

**File:** `app/providers/base.py`

### Provider Types

An enumeration of supported hypervisor platforms.

| Type | String Value | Status |
| :--- | :--- | :--- |
| `PROXMOX` | `"proxmox"` | **Implemented** |
| `VSPHERE` | `"vsphere"` | Planned (stubs exist) |
| `KVM` | `"kvm"` | Planned |
| `HYPERV` | `"hyperv"` | Planned |
| `DOCKER` | `"docker"` | Legacy (deprecated) |
| `AWS` | `"aws"` | Planned |
| `AZURE` | `"azure"` | Planned |

### Abstract Classes

#### `HypervisorProvider`

The primary provider interface for hypervisor operations. 20+ abstract methods.

**Bridge Operations:**
- `create_bridge(bridge_id, tenant_id)` → Create Linux bridge (vmbrN)
- `delete_bridge(bridge_id)` → Remove Linux bridge

**VM Operations:**
- `clone_opnsense(template_id, new_vm_id, name, lan_bridge)` → Clone OPNsense template
- `clone_vm_with_cloudinit(...)` → Full cloud-init VM provisioning
- `delete_vm(vm_id, node)` → Stop and purge VM
- `start_vm(vm_id, node)` → Start VM
- `stop_vm(vm_id, node)` → Stop VM
- `get_vm_status(vm_id, node)` → Get VM status

**Guest Agent Operations:**
- `exec_in_vm(vm_id, command, timeout)` → Execute command via QEMU guest agent
- `_wait_for_guest_agent(vm_id, timeout)` → Poll guest agent readiness

**Network Operations:**
- `create_network(network)` → Create network bridge
- `attach_vm_to_network(vm_config, network)` → Build NIC config string
- `delete_network(network)` → Remove network bridge

**Disk/Storage Operations:**
- `resize_disk(vm_id, disk, size, node)` → Resize VM disk (relative +XG)
- `get_vm_resources(vm_id, node)` → Get live CPU/RAM/disk
- `update_vm_resources(vm_id, cores, memory, digest, node)` → Update CPU/RAM
- `get_storage_info(node)` → Get storage pool stats
- `get_vm_disk_info(vm_id, node)` → Get disk configuration

**Template/Image Operations:**
- `list_templates(node)` → List all Proxmox templates
- `list_all_vms(node)` → List all QEMU resources
- `create_build_vm(...)` → Create build VM from ISO
- `convert_to_template(vm_id, node)` → Convert VM to template
- `download_iso_url(...)` → Download ISO to Proxmox storage
- `list_storage_content(storage, node)` → List storage contents

**Console Operations:**
- `get_vnc_proxy(vm_id, node)` → Get VNC/serial console token
- `get_serial_console(vm_id, node)` → Get serial console details
- `stop_console_session(node, upid)` → Terminate console session

#### `ContainerProvider`

Abstract interface for container lifecycle operations (legacy Docker support).

**Methods:**
- `start(name) → bool`
- `stop(name) → bool`
- `restart(name) → bool`
- `remove(name, force=False) → bool`
- `get_logs(name, tail=100) → ContainerLogs`
- `get_status(name) → Optional[ContainerInfo]`
- `list_containers(label_filter=None) → List[ContainerInfo]`
- `create_snapshot(name, snapshot_name, description="") → bool`
- `restore_snapshot(name, snapshot_name) → bool`
- `delete_snapshot(name, snapshot_name) → bool`
- `list_snapshots(name) → List[SnapshotInfo]`

#### `NetworkProvider`

Abstract interface for network management.

**Methods:**
- `create_network(name, cidr, gateway=None) → NetworkInfo`
- `delete_network(name) → bool`
- `get_network(name) → Optional[NetworkInfo]`
- `list_networks() → List[NetworkInfo]`
- `check_ip_available(network_name, ip) → bool`
- `get_used_ips(network_name) → List[str]`

#### `IPAMProvider`

Abstract interface for IP address management.

**Methods:**
- `check_ip_is_free(ip) → bool`
- `get_all_subnets() → List`
- `get_used_ips(network_name) → List[str]`
- `wait_for_network(name, timeout=30) → bool`
- `validate_cidr_overlap(cidr) → bool`

### Data Models

Dataclasses used to standardize information returned by providers.

#### `ContainerInfo`

| Field | Type | Description |
| :--- | :--- | :--- |
| `name` | `str` | Container/VM name |
| `status` | `str` | Current state (running, stopped) |
| `ip_address` | `Optional[str]` | Primary IP address |
| `ports` | `Optional[Dict[int, int]]` | Port mappings |
| `created_at` | `Optional[str]` | Creation timestamp |

#### `NetworkInfo`

| Field | Type | Description |
| :--- | :--- | :--- |
| `name` | `str` | Network name |
| `cidr` | `str` | CIDR block |
| `gateway` | `Optional[str]` | Gateway IP |
| `status` | `str` | Network status |
| `container_count` | `int` | Attached containers |

#### `ContainerLogs`

| Field | Type | Description |
| :--- | :--- | :--- |
| `container_name` | `str` | Container name |
| `logs` | `str` | Raw log output |
| `line_count` | `int` | Approximate lines |

#### `BridgeResult`

| Field | Type | Description |
| :--- | :--- | :--- |
| `bridge_id` | `int` | Bridge number (e.g., 100) |
| `bridge_name` | `str` | Full name (e.g., vmbr100) |
| `success` | `bool` | Operation success |
| `error` | `Optional[str]` | Error message |

#### `VMResult`

| Field | Type | Description |
| :--- | :--- | :--- |
| `vm_id` | `int` | Proxmox VM ID |
| `name` | `str` | VM name |
| `status` | `str` | Current status |
| `ip_address` | `Optional[str]` | Assigned IP |
| `success` | `bool` | Operation success |
| `error` | `Optional[str]` | Error message |

#### `InterfaceInfo`

| Field | Type | Description |
| :--- | :--- | :--- |
| `name` | `str` | Interface name |
| `mac` | `Optional[str]` | MAC address |
| `ip_address` | `Optional[str]` | IP address |
| `status` | `str` | Interface status |

#### `NodeStatus`

| Field | Type | Description |
| :--- | :--- | :--- |
| `name` | `str` | Node name |
| `status` | `str` | Node status (online/offline) |
| `cpu` | `float` | CPU usage |
| `memory_total` | `int` | Total memory |
| `memory_used` | `int` | Used memory |
| `disk_total` | `int` | Total disk |
| `disk_used` | `int` | Used disk |

#### `SnapshotInfo`

| Field | Type | Description |
| :--- | :--- | :--- |
| `name` | `str` | Snapshot name |
| `description` | `Optional[str]` | Description |
| `created_at` | `Optional[str]` | Creation timestamp |

---

## Proxmox Implementation

**File:** `app/providers/proxmox.py` (1546 lines)

The `ProxmoxProvider` class implements `HypervisorProvider` using **raw HTTP requests** to the Proxmox API (bypasses the `proxmoxer` library).

### Authentication

API token-based authentication:
```
PVEAPIToken=<user>=<token>
```
TLS verification is disabled for internal Proxmox API access.

### Key Implementation Details

**Bridge Creation:**
- Creates VLAN-aware Linux bridges (`vmbrN`) with VIDs 2-4094
- Applies via `PUT /network` on the Proxmox node
- Supports autostart and comments

**VM Cloning (OPNsense):**
- Clones template VM with full clone
- Configures dual NICs: WAN on vmbr0, LAN on tenant bridge
- Uses lock-retry logic for concurrent operations

**Cloud-Init VM Provisioning:**
- Clone template → configure ciuser/cipassword/sshkeys/ipconfig
- Set CPU/RAM via API
- Password is SHA-512 hashed via `passlib.hash.sha512_crypt`
- Optional Windows support (skip cloud-init)

**Guest Agent Operations:**
- Polls `agent/ping` until 2 consecutive successes
- Maintains a `_stable_agents` cache to skip known-good VMs
- `exec_in_vm()` sends JSON commands via `agent/exec` API
- Retry logic for broken pipe / 500 / 596 errors

**Task Management:**
- `_wait_for_task(upid, timeout)` polls Proxmox task status
- `_wait_for_exec_output(vm_id, polls exec-status until completion`

---

## Firewall Providers

**File:** `app/providers/firewall_provider.py`

### `FirewallProvider` (Abstract Base)

| Method | Description |
|--------|-------------|
| `list_rules()` | List all firewall rules |
| `list_interfaces()` | List network interfaces |
| `add_rule(rule)` | Create a new rule |
| `set_rule(uuid, rule)` | Update existing rule |
| `del_rule(uuid)` | Delete rule |
| `apply_rules()` | Push changes to firewall |
| `get_interface_list()` | Get interface details |

### `OPNsenseFirewallProvider` (Implemented)

Full OPNsense integration via Proxmox `exec_in_vm`:

**Firewall Management:**
- CRUD operations via OPNsense REST API
- Rule sequencing and reordering
- Apply/reload firewall changes

**WireGuard Management:**
- `generate_keypair()` → Server/client key generation
- `wg_general_enable()` → Enable WireGuard service
- `add_wg_server()` → Register tunnel server
- `add_wg_client()` → Register peer (wizard endpoint)
- `del_wg_server()` / `del_wg_client()` → Remove entries
- `set_wg_server_endpoint()` → Set WAN endpoint
- `get_wg_device_name()` → Get WireGuard device name

**Route/DHCP Management:**
- Kea DHCP subnet configuration
- Static route management

### `PFSenseFirewallProvider` / `FortinetFirewallProvider`

Stub implementations that log warnings and return empty results. Planned for future implementation.

---

## Factory Methods

**File:** `app/providers/__init__.py`

### `get_hypervisor_provider(host=None)`

Returns a `ProxmoxProvider` instance configured from `app.core.config.settings`.

```python
from app.providers import get_hypervisor_provider

provider = get_hypervisor_provider()
provider.start_vm(vm_id=100, node="pve")
```

### `get_container_provider(provider_type)`

Returns a provider instance by type string. Currently only returns Proxmox for `"proxmox"`, `"kvm"`, `"hyperv"`.

### `get_network_provider(provider_type)`

Returns a network provider instance by type string.

### `get_provider_for_pod(pod)`

Returns the correct provider based on `pod.provider_type`. Supports `"proxmox"`, `"vsphere"`, `"kvm"`, `"hyperv"`.

### `get_firewall_provider(db, tenant, provider_type)`

Factory for firewall providers. For OPNsense, uses tenant credentials directly.

### `get_available_providers(db, tenant)`

Returns status info for all firewall providers (active, connected, rule count).

---

## Error Handling

### `ProviderException`

Custom exception raised when a provider operation fails.

**Attributes:**
- `message` (str): Human-readable error description
- `provider` (ProviderType): The provider type that caused the error
- `original_error` (Optional[Exception]): The underlying exception

**Example:**

```python
from app.providers import get_hypervisor_provider
from app.providers.base import ProviderException

provider = get_hypervisor_provider()

try:
    provider.start_vm(vm_id=999, node="pve")
except ProviderException as e:
    print(f"Error: {e.message}")
    print(f"Provider: {e.provider.value}")
```

---

## Extensibility

To add a new provider (e.g., vSphere):

1. **Create Implementation File:** Create `app/providers/vsphere.py`
2. **Implement Classes:**
   ```python
   from app.providers.base import HypervisorProvider, ProviderType

   class VSphereProvider(HypervisorProvider):
       @property
       def provider_type(self) -> ProviderType:
           return ProviderType.VSPHERE

       def create_bridge(self, bridge_id, tenant_id):
           # vSphere API logic here
           pass
       # ... implement all abstract methods
   ```
3. **Update Factory:** Modify `app/providers/__init__.py` to handle `ProviderType.VSPHERE`
