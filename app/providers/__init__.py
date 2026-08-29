"""
Provider Abstraction Layer

This package provides abstract interfaces and implementations for
infrastructure providers, enabling support for multiple cloud/container platforms.

Usage:
    from app.providers import get_hypervisor_provider
    
    hypervisor = get_hypervisor_provider()
    result = hypervisor.create_bridge(bridge_id=101, tenant_id=1)
"""

from app.providers.base import (
    ContainerProvider,
    NetworkProvider,
    IPAMProvider,
    ContainerInfo,
    NetworkInfo,
    ContainerLogs,
    ProviderType,
    ProviderException,
    HypervisorProvider,
    BridgeResult,
    VMResult,
    InterfaceInfo,
    NodeStatus,
)
from app.providers.factory import (
    get_container_provider,
    get_network_provider,
    register_container_provider,
    register_network_provider,
)
from app.providers.proxmox import ProxmoxProvider
from app.providers.firewall_provider import (
    FirewallProvider,
    OPNsenseFirewallProvider,
    PFSenseFirewallProvider,
    FortinetFirewallProvider,
    get_firewall_provider,
    get_available_providers,
)

_hypervisor_providers: dict[ProviderType, HypervisorProvider] = {}
_ipam_providers: dict[ProviderType, IPAMProvider] = {}

__all__ = [
    "ContainerProvider",
    "NetworkProvider",
    "IPAMProvider",
    "HypervisorProvider",
    "ContainerInfo",
    "NetworkInfo",
    "ContainerLogs",
    "ProviderType",
    "ProviderException",
    "BridgeResult",
    "VMResult",
    "InterfaceInfo",
    "NodeStatus",
    "get_container_provider",
    "get_network_provider",
    "get_hypervisor_provider",
    "register_container_provider",
    "register_network_provider",
    "register_ipam_provider",
    "ProxmoxProvider",
    "FirewallProvider",
    "OPNsenseFirewallProvider",
    "PFSenseFirewallProvider",
    "FortinetFirewallProvider",
    "get_firewall_provider",
    "get_available_providers",
]


def get_hypervisor_provider(host: str = None) -> HypervisorProvider:
    """
    Returns the hypervisor provider based on config.
    
    Args:
        host: Optional host URL. When multi-host support is added (Option A),
              pass tenant.host.address here. Currently uses global config.
    
    Returns:
        HypervisorProvider instance (ProxmoxProvider for now)
    
    Raises:
        ValueError: If provider type is not supported
    """
    from app.core.config import settings
    from app.providers.base import ProviderType
    
    try:
        provider_type = ProviderType(settings.HYPERVISOR_TYPE.lower())
    except ValueError:
        provider_type = ProviderType.PROXMOX
    
    if provider_type not in _hypervisor_providers:
        if provider_type == ProviderType.PROXMOX:
            _hypervisor_providers[provider_type] = ProxmoxProvider(host=host)
        else:
            raise ValueError(f"Unknown hypervisor type: {provider_type}")
    
    return _hypervisor_providers[provider_type]


def get_ipam_provider(provider_type: str) -> IPAMProvider:
    """
    Get an IPAM provider instance for the specified type.
    
    Args:
        provider_type: Provider type string (e.g., "proxmox", "vsphere")
        
    Returns:
        IPAMProvider instance
        
    Raises:
        ProviderException: If provider type is not supported
    """
    if not isinstance(provider_type, str):
        raise ProviderException(
            f"Provider type must be a string, got {type(provider_type).__name__}",
            ProviderType.PROXMOX
        )
    try:
        ptype = ProviderType(provider_type.lower())
    except ValueError:
        raise ProviderException(
            f"Unsupported provider type: {provider_type}",
            ProviderType.PROXMOX
        )
    
    if ptype not in _ipam_providers:
        raise ProviderException(
            f"IPAM provider for {ptype} not implemented",
            ptype
        )
    
    return _ipam_providers[ptype]


def register_ipam_provider(provider: IPAMProvider) -> None:
    """
    Register a custom IPAM provider.
    
    Args:
        provider: IPAMProvider instance to register
    """
    _ipam_providers[provider.provider_type] = provider
    import logging
    logging.getLogger(__name__).info(f"Registered IPAM provider: {provider.provider_type}")


def get_provider_for_pod(pod, db=None):
    """
    Returns the correct provider for a Pod's provider_type.
    
    Args:
        pod: Pod model instance
        db: Optional database session
        
    Returns:
        Provider instance (ProxmoxProvider, VSphereProvider, etc.)
    """
    if pod.provider_type == "proxmox":
        return ProxmoxProvider(db=db)
    elif pod.provider_type == "vsphere":
        from app.providers.vsphere import VSphereProvider
        return VSphereProvider(db=db)
    elif pod.provider_type == "kvm":
        from app.providers.kvm import KVMProvider
        return KVMProvider(db=db)
    elif pod.provider_type == "hyperv":
        from app.providers.hyperv import HyperVProvider
        return HyperVProvider(db=db)
    else:
        raise ValueError(f"Unknown provider type: {pod.provider_type}")
