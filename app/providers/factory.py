"""
Provider Factory

Factory for creating provider instances based on provider type.
"""
from typing import Dict, Optional
import logging

from app.providers.base import (
    ContainerProvider,
    NetworkProvider,
    ProviderType,
    ProviderException
)

logger = logging.getLogger(__name__)

_container_providers: Dict[ProviderType, ContainerProvider] = {}
_network_providers: Dict[ProviderType, NetworkProvider] = {}


def get_container_provider(provider_type: str) -> ContainerProvider:
    """
    Get a container provider instance for the specified type.
    
    Args:
        provider_type: Provider type string (e.g., "proxmox", "vsphere")
        
    Returns:
        ContainerProvider instance
        
    Raises:
        ProviderException: If provider type is not supported
    """
    try:
        ptype = ProviderType(provider_type.lower())
    except ValueError:
        raise ProviderException(
            f"Unsupported provider type: {provider_type}",
            ProviderType.PROXMOX
        )
    
    if ptype not in _container_providers:
        raise ProviderException(
            f"Container provider {ptype} not implemented",
            ptype
        )
    
    return _container_providers[ptype]


def get_network_provider(provider_type: str) -> NetworkProvider:
    """
    Get a network provider instance for the specified type.
    
    Args:
        provider_type: Provider type string (e.g., "proxmox", "vsphere")
        
    Returns:
        NetworkProvider instance
        
    Raises:
        ProviderException: If provider type is not supported
    """
    try:
        ptype = ProviderType(provider_type.lower())
    except ValueError:
        raise ProviderException(
            f"Unsupported provider type: {provider_type}",
            ProviderType.PROXMOX
        )
    
    if ptype not in _network_providers:
        raise ProviderException(
            f"Network provider {ptype} not implemented",
            ptype
        )
    
    return _network_providers[ptype]


def register_container_provider(provider: ContainerProvider) -> None:
    """
    Register a custom container provider.
    
    Args:
        provider: ContainerProvider instance to register
    """
    _container_providers[provider.provider_type] = provider
    logger.info(f"Registered container provider: {provider.provider_type}")


def register_network_provider(provider: NetworkProvider) -> None:
    """
    Register a custom network provider.
    
    Args:
        provider: NetworkProvider instance to register
    """
    _network_providers[provider.provider_type] = provider
    logger.info(f"Registered network provider: {provider.provider_type}")
