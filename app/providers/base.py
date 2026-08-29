"""
Provider Abstraction Layer

This module provides abstract interfaces for infrastructure providers,
enabling support for multiple cloud/container platforms.
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import ipaddress


class ProviderType(str, Enum):
    DOCKER = "docker"
    VSPHERE = "vsphere"
    PROXMOX = "proxmox"
    AWS = "aws"
    AZURE = "azure"


@dataclass
class BridgeResult:
    """Result of bridge creation."""
    bridge_name: str
    bridge_id: int


@dataclass
class VMResult:
    """Result of VM creation/cloning."""
    vm_id: int
    node: str


@dataclass
class InterfaceInfo:
    """Network interface information."""
    name: str
    ip: Optional[str] = None


@dataclass
class NodeStatus:
    """Host node resource status."""
    total_memory_mb: int
    free_memory_mb: int
    cpu_usage: float
    vm_count: int


@dataclass
class ContainerInfo:
    """Container information returned by provider."""
    name: str
    status: str
    ip_address: Optional[str] = None
    ports: Optional[Dict[int, int]] = None
    created_at: Optional[str] = None


@dataclass
class NetworkInfo:
    """Network information returned by provider."""
    name: str
    cidr: str
    gateway: Optional[str] = None
    status: str = "active"
    container_count: int = 0


@dataclass
class ContainerLogs:
    """Container logs returned by provider."""
    container_name: str
    logs: str
    line_count: int


@dataclass
class SnapshotInfo:
    """Snapshot information returned by provider."""
    name: str
    image_tag: str
    created_at: Optional[str] = None
    size: Optional[int] = None


class ContainerProvider(ABC):
    """
    Abstract interface for container operations.
    
    Implement this interface to add support for a new container platform.
    """
    
    @property
    @abstractmethod
    def provider_type(self) -> ProviderType:
        """Return the provider type identifier."""
        pass
    
    @abstractmethod
    def start(self, name: str) -> bool:
        """
        Start a container.
        
        Args:
            name: Container name
            
        Returns:
            True if successful
            
        Raises:
            ProviderException: If operation fails
        """
        pass
    
    @abstractmethod
    def stop(self, name: str) -> bool:
        """
        Stop a container.
        
        Args:
            name: Container name
            
        Returns:
            True if successful
            
        Raises:
            ProviderException: If operation fails
        """
        pass
    
    @abstractmethod
    def restart(self, name: str) -> bool:
        """
        Restart a container.
        
        Args:
            name: Container name
            
        Returns:
            True if successful
            
        Raises:
            ProviderException: If operation fails
        """
        pass
    
    @abstractmethod
    def get_logs(self, name: str, tail: int = 100) -> ContainerLogs:
        """
        Get container logs.
        
        Args:
            name: Container name
            tail: Number of lines to retrieve
            
        Returns:
            ContainerLogs object
            
        Raises:
            ProviderException: If operation fails
        """
        pass
    
    @abstractmethod
    def get_status(self, name: str) -> Optional[ContainerInfo]:
        """
        Get container status.
        
        Args:
            name: Container name
            
        Returns:
            ContainerInfo or None if not found
        """
        pass
    
    @abstractmethod
    def list_containers(self, label_filter: Optional[Dict[str, str]] = None) -> List[ContainerInfo]:
        """
        List containers, optionally filtered by labels.
        
        Args:
            label_filter: Optional label key-value pairs to filter by
            
        Returns:
            List of ContainerInfo objects
        """
        pass
    
    @abstractmethod
    def remove(self, name: str, force: bool = False) -> bool:
        """
        Remove a container.
        
        Args:
            name: Container name
            force: Force removal even if running
            
        Returns:
            True if successful
            
        Raises:
            ProviderException: If operation fails
        """
        pass
    
    @abstractmethod
    def create_snapshot(self, container_name: str, snapshot_name: str) -> SnapshotInfo:
        """
        Create a snapshot of a container by committing it to an image.
        
        Args:
            container_name: Name of the container to snapshot
            snapshot_name: Name for the snapshot image
            
        Returns:
            SnapshotInfo object with snapshot details
            
        Raises:
            ProviderException: If operation fails
        """
        pass
    
    @abstractmethod
    def restore_snapshot(self, snapshot_image_tag: str, new_container_name: str) -> bool:
        """
        Restore a snapshot by creating a new container from the snapshot image.
        
        Args:
            snapshot_image_tag: The image tag to restore from
            new_container_name: Name for the new container
            
        Returns:
            True if successful
            
        Raises:
            ProviderException: If operation fails
        """
        pass
    
    @abstractmethod
    def delete_snapshot(self, snapshot_image_tag: str) -> bool:
        """
        Delete a snapshot image.
        
        Args:
            snapshot_image_tag: The image tag to delete
            
        Returns:
            True if successful
            
        Raises:
            ProviderException: If operation fails
        """
        pass
    
    @abstractmethod
    def list_snapshots(self, vm_name: Optional[str] = None) -> List[SnapshotInfo]:
        """
        List available snapshots.
        
        Args:
            vm_name: Optional filter by VM/container name
            
        Returns:
            List of SnapshotInfo objects
        """
        pass


class NetworkProvider(ABC):
    """
    Abstract interface for network operations.
    
    Implement this interface to add support for a new network platform.
    """
    
    @property
    @abstractmethod
    def provider_type(self) -> ProviderType:
        """Return the provider type identifier."""
        pass
    
    @abstractmethod
    def create_network(self, name: str, cidr: str, gateway: Optional[str] = None) -> NetworkInfo:
        """
        Create a network.
        
        Args:
            name: Network name
            cidr: Network CIDR
            gateway: Optional gateway IP
            
        Returns:
            NetworkInfo object
            
        Raises:
            ProviderException: If operation fails
        """
        pass
    
    @abstractmethod
    def delete_network(self, name: str) -> bool:
        """
        Delete a network.
        
        Args:
            name: Network name
            
        Returns:
            True if successful
            
        Raises:
            ProviderException: If operation fails
        """
        pass
    
    @abstractmethod
    def get_network(self, name: str) -> Optional[NetworkInfo]:
        """
        Get network information.
        
        Args:
            name: Network name
            
        Returns:
            NetworkInfo or None if not found
        """
        pass
    
    @abstractmethod
    def list_networks(self) -> List[NetworkInfo]:
        """
        List all networks.
        
        Returns:
            List of NetworkInfo objects
        """
        pass
    
    @abstractmethod
    def check_ip_available(self, network_name: str, ip: str) -> bool:
        """
        Check if an IP address is available in the network.
        
        Args:
            network_name: Network name
            ip: IP address to check
            
        Returns:
            True if IP is available
        """
        pass
    
    @abstractmethod
    def get_used_ips(self, network_name: str) -> List[str]:
        """
        Get list of IPs currently in use in the network.
        
        Args:
            network_name: Network name
            
        Returns:
            List of IP addresses in use
        """
        pass


class ProviderException(Exception):
    """Exception raised by provider operations."""
    
    def __init__(self, message: str, provider: ProviderType, original_error: Optional[Exception] = None):
        self.message = message
        self.provider = provider
        self.original_error = original_error
        super().__init__(f"[{provider.value}] {message}")


class IPAMProvider(ABC):
    """
    Abstract interface for IP Address Management operations.
    
    Implement this interface to add IPAM support for a new provider platform.
    """
    
    @property
    @abstractmethod
    def provider_type(self) -> ProviderType:
        """Return the provider type identifier."""
        pass
    
    @abstractmethod
    def check_ip_is_free(self, ip: str, network_name: str) -> bool:
        """
        Check if a specific IP is available in the network.
        
        Args:
            ip: IP address to check
            network_name: Network name to check against
            
        Returns:
            True if IP is available, False if in use
        """
        pass
    
    @abstractmethod
    def get_all_subnets(self) -> List[ipaddress.IPv4Network]:
        """
        Get all existing subnet CIDRs from the provider.
        
        Returns:
            List of IPv4Network objects
        """
        pass
    
    @abstractmethod
    def get_used_ips(self, network_name: str) -> List[str]:
        """
        Get list of IPs currently in use in the network.
        
        Args:
            network_name: Network name
            
        Returns:
            List of IP addresses in use
        """
        pass
    
    @abstractmethod
    def wait_for_network(self, network_name: str, timeout: int = 30) -> bool:
        """
        Wait for a network to be fully operational.
        
        Args:
            network_name: Network name to wait for
            timeout: Maximum wait time in seconds
            
        Returns:
            True if network is ready
            
        Raises:
            ProviderException: If network doesn't become ready in time
        """
        pass
    
    @abstractmethod
    def validate_cidr_overlap(self, cidr: str) -> Tuple[bool, Optional[str]]:
        """
        Check if a CIDR overlaps with existing networks.
        
        Args:
            cidr: CIDR to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        pass


class HypervisorProvider(ABC):
    """
    Abstract base for all hypervisor providers.
    Every method maps to one infrastructure operation.
    Your provisioning code talks only to this interface —
    never to Proxmox, vSphere, or Hyper-V directly.
    """

    @property
    @abstractmethod
    def provider_type(self) -> ProviderType:
        """Return the provider type identifier."""
        pass

    @abstractmethod
    def create_bridge(self, bridge_id: int, tenant_id: int) -> BridgeResult:
        """
        Create an isolated LAN bridge for a tenant.
        
        Args:
            bridge_id: The bridge ID (e.g., 105 for vmbr105)
            tenant_id: The tenant ID for comments/labeling
            
        Returns:
            BridgeResult with bridge_name and bridge_id
        """
        pass

    @abstractmethod
    def delete_bridge(self, bridge_id: int) -> None:
        """
        Remove a bridge. Called on deprovisioning and rollback.
        
        Args:
            bridge_id: The bridge ID to delete
        """
        pass

    @abstractmethod
    def clone_opnsense(
        self,
        template_id: int,
        new_vm_id: int,
        name: str,
        lan_bridge: str,
    ) -> VMResult:
        """
        Clone the OPNsense template, wire NICs, and boot.
        NIC0 -> WAN (vmbr0 or equivalent)
        NIC1 -> lan_bridge (isolated tenant bridge)
        
        This is OPNsense-specific - uses dual NICs (WAN + LAN).
        
        Args:
            template_id: The VM ID to clone from
            new_vm_id: The target VM ID
            name: Name for the new VM
            lan_bridge: The tenant's LAN bridge name
            
        Returns:
            VMResult with vm_id and node
        """
        pass

    @abstractmethod
    def clone_vm_with_cloudinit(
        self,
        template_id: int,
        new_vm_id: int,
        name: str,
        lan_bridge: str,
        username: str = "ubuntu",
        password: Optional[str] = None,
        ssh_public_key: Optional[str] = None,
        ip_mode: str = "dhcp",
        ip_address: Optional[str] = None,
        gateway: str = "10.0.0.1",
        dns_nameservers: Optional[List[str]] = None,
        dns_search: Optional[str] = None,
        cpu: int = 1,
        ram: int = 1024,
        auto_start: bool = True,
        skip_cloudinit: bool = False,
    ) -> VMResult:
        """
        Clone a VM template with cloud-init configuration.
        
        Generic VM provisioning - works for any hypervisor that supports cloud-init.
        Each hypervisor implements this differently:
        - Proxmox: Uses cloud-init via API config
        - vSphere: Uses guestinfo customization
        
        Args:
            template_id: The VM ID to clone from
            new_vm_id: The target VM ID
            name: Name for the new VM
            lan_bridge: The tenant's LAN bridge name
            username: Cloud-init username
            password: Cloud-init password (will be hashed)
            ssh_public_key: SSH public key for cloud-init
            ip_mode: "dhcp" or "static"
            ip_address: Static IP (required if ip_mode is static)
            gateway: Gateway IP
            dns_nameservers: DNS nameservers list
            dns_search: DNS search domain
            cpu: Number of CPU cores
            ram: RAM in MB
            auto_start: Whether to start the VM after provisioning
            skip_cloudinit: If True, skip cloud-init config (for Windows VMs)
            
        Returns:
            VMResult with vm_id and node
        """
        pass

    @abstractmethod
    def delete_vm(self, vm_id: int) -> None:
        """
        Stop and permanently delete a VM.
        
        Args:
            vm_id: The VM ID to delete
        """
        pass

    @abstractmethod
    def start_vm(self, vm_id: int) -> bool:
        """
        Start a VM.
        
        Args:
            vm_id: The VM ID to start
            
        Returns:
            True if successful
        """
        pass

    @abstractmethod
    def stop_vm(self, vm_id: int) -> bool:
        """
        Stop a VM.
        
        Args:
            vm_id: The VM ID to stop
            
        Returns:
            True if successful
        """
        pass

    @abstractmethod
    def get_vm_interfaces(self, vm_id: int) -> List[InterfaceInfo]:
        """
        Ask the hypervisor for the VM's current network interfaces.
        Requires guest agent installed in the VM.
        Used to detect WAN IP after boot.
        
        Args:
            vm_id: The VM ID to query
            
        Returns:
            List of InterfaceInfo objects
        """
        pass

    @abstractmethod
    def get_node_status(self) -> NodeStatus:
        """
        Return current host resource usage.
        Called before provisioning to check capacity.
        
        Returns:
            NodeStatus with memory, CPU, and VM count
        """
        pass

    @abstractmethod
    def list_templates(self) -> List[dict]:
        """
        List all VMs marked as templates in the hypervisor.
        
        Returns:
            List of dicts with vmid, name, os, cores, memory, disk
        """
        pass

    @abstractmethod
    def exec_in_vm(self, vm_id: int, command: str, timeout: int = 60, node: str = None) -> dict:
        """
        Execute a command inside a VM via guest agent.
        Used for OPNsense key rotation script.
        
        Args:
            vm_id: The VM ID to execute command in
            command: The command to run
            timeout: Timeout in seconds (default 60)
            node: The Proxmox node name (default None, uses provider's default)
            
        Returns:
            Dict with keys: "out" (stdout), "err" (stderr), "exitcode" (int)
            
        Raises:
            RuntimeError: If command execution fails
        """
        pass

    @abstractmethod
    def create_network(self, network) -> str:
        """
        Provisions the network on the platform.
        Returns provider_ref to store in TenantNetwork.provider_ref.
        
        Default (untagged) LAN: creates a dedicated bridge.
        Additional (tagged) networks: reuses the tenant's existing bridge.
        
        Args:
            network: TenantNetwork model instance
            
        Returns:
            provider_ref string (e.g., "vmbr101" for Proxmox)
        """
        pass

    @abstractmethod
    def attach_vm_to_network(self, vm_config: dict, network) -> dict:
        """
        Injects NIC config into vm_config for this network.
        Returns the modified vm_config dict.
        
        Untagged (default LAN):  virtio,bridge=vmbr101,firewall=1
        Tagged (extra network):  virtio,bridge=vmbr101,firewall=1,tag=20
        
        Args:
            vm_config: dict to inject NIC config into
            network: TenantNetwork model instance
            
        Returns:
            Modified vm_config dict with net0 set
        """
        pass

    @abstractmethod
    def delete_network(self, network) -> None:
        """
        Tears down the physical network construct.
        
        Deletes the bridge only for the default (untagged) network.
        Tagged networks have no dedicated bridge — nothing to tear down.
        
        Args:
            network: TenantNetwork model instance
        """
        pass

    @abstractmethod
    def resize_disk(self, vm_id: int, disk: str, size: str, node: str = None) -> bool:
        """
        Resize a VM disk.
        
        Args:
            vm_id: The VM ID
            disk: The disk identifier (e.g., "scsi0", "virtio0")
            size: Relative size to add (e.g., "+10G", "+512M"). Must start with +.
            node: Optional node name (auto-detected if not provided)
            
        Returns:
            True if successful
            
        Raises:
            ValueError: If size format is invalid (must start with +)
            RuntimeError: If resize fails
        """
        pass

    @abstractmethod
    def get_vm_resources(self, vm_id: int) -> dict:
        """
        Get current VM resource configuration (CPU, RAM, Disk).
        
        Args:
            vm_id: The VM ID
            
        Returns:
            Dict with cpu_cores, memory_mb, disks, digest
        """
        pass

    @abstractmethod
    def update_vm_resources(self, vm_id: int, cpu_cores: int = None, memory_mb: int = None) -> dict:
        """
        Update VM CPU and/or RAM resources.
        
        Args:
            vm_id: The VM ID
            cpu_cores: New CPU core count (None to keep current)
            memory_mb: New RAM in MB (None to keep current)
            
        Returns:
            Dict with success status and updated values
        """
        pass

    @abstractmethod
    def get_storage_info(self) -> dict:
        """
        Get storage utilization information.
        
        Returns:
            Dict with storage names as keys and {total_gb, free_gb, used_gb, content} as values
        """
        pass

    @abstractmethod
    def get_vm_disk_info(self, vm_id: int) -> List[dict]:
        """
        Get disk information for a VM.
        
        Args:
            vm_id: The VM ID
            
        Returns:
            List of dicts with {id, storage, volume, size_mib, size_gb, options}
        """
        pass

    @abstractmethod
    def get_vm_status(self, vm_id: int) -> dict:
        """
        Get current VM status including lock state.
        
        Args:
            vm_id: The VM ID
            
        Returns:
            Dict with VM status info including 'lock' field if VM is locked
        """
        pass

    @abstractmethod
    def download_iso_url(self, node: str, storage: str, url: str) -> dict:
        """Download an ISO from URL to Proxmox storage. Returns UPID for async tracking."""
        pass

    @abstractmethod
    def list_storage_content(self, node: str, storage: str, content_type: str = None) -> list:
        """List storage contents, optionally filtered by type."""
        pass

    @abstractmethod
    def get_task_status(self, node: str, upid: str) -> dict:
        """Get Proxmox task status by UPID."""
        pass

    @abstractmethod
    def get_task_log(self, node: str, upid: str, start: int = 0, limit: int = 100) -> dict:
        """Get task log lines."""
        pass

    @abstractmethod
    def create_build_vm(self, node: str, vmid: int, name: str, iso_volid: str,
                        cpu: int = 2, ram_mb: int = 4096, disk_gb: int = 20) -> dict:
        """Create a build VM from an ISO for template preparation."""
        pass

    @abstractmethod
    def convert_to_template(self, node: str, vmid: int) -> dict:
        """Convert a VM to a template."""
        pass
