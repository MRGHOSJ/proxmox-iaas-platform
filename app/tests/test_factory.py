import pytest
from unittest.mock import patch, MagicMock
from app.providers.factory import (
    get_container_provider,
    get_network_provider,
    register_container_provider,
    register_network_provider,
    _container_providers,
    _network_providers,
)
from app.providers.base import ProviderType, ProviderException
from app.providers.docker import DockerContainerProvider, DockerNetworkProvider


pytestmark = pytest.mark.unit


def setup_function():
    """Clear providers before each test."""
    _container_providers.clear()
    _network_providers.clear()


class TestGetContainerProvider:
    """Tests for get_container_provider function."""

    def test_get_docker_container_provider(self):
        """Returns Docker container provider."""
        provider = get_container_provider("docker")
        
        assert isinstance(provider, DockerContainerProvider)

    def test_get_docker_uppercase(self):
        """Handles uppercase provider type."""
        provider = get_container_provider("DOCKER")
        
        assert isinstance(provider, DockerContainerProvider)

    def test_get_vsphere_raises(self):
        """Raises for unsupported provider."""
        with pytest.raises(ProviderException) as exc_info:
            get_container_provider("vsphere")
        
        assert "not yet implemented" in str(exc_info.value)

    def test_get_invalid_raises(self):
        """Raises for invalid provider type."""
        with pytest.raises(ProviderException) as exc_info:
            get_container_provider("invalid")
        
        assert "Unsupported provider type" in str(exc_info.value)

    def test_caches_provider(self):
        """Returns cached provider instance."""
        provider1 = get_container_provider("docker")
        provider2 = get_container_provider("docker")
        
        assert provider1 is provider2


class TestGetNetworkProvider:
    """Tests for get_network_provider function."""

    def test_get_docker_network_provider(self):
        """Returns Docker network provider."""
        provider = get_network_provider("docker")
        
        assert isinstance(provider, DockerNetworkProvider)

    def test_get_docker_uppercase(self):
        """Handles uppercase provider type."""
        provider = get_network_provider("DOCKER")
        
        assert isinstance(provider, DockerNetworkProvider)

    def test_get_vsphere_raises(self):
        """Raises for unsupported provider."""
        with pytest.raises(ProviderException) as exc_info:
            get_network_provider("vsphere")
        
        assert "not yet implemented" in str(exc_info.value)

    def test_get_invalid_raises(self):
        """Raises for invalid provider type."""
        with pytest.raises(ProviderException) as exc_info:
            get_network_provider("invalid")
        
        assert "Unsupported provider type" in str(exc_info.value)

    def test_caches_provider(self):
        """Returns cached provider instance."""
        provider1 = get_network_provider("docker")
        provider2 = get_network_provider("docker")
        
        assert provider1 is provider2


class TestRegisterContainerProvider:
    """Tests for register_container_provider function."""

    def test_register_custom_provider(self):
        """Registers custom provider."""
        mock_provider = MagicMock()
        mock_provider.provider_type = ProviderType.DOCKER
        
        register_container_provider(mock_provider)
        
        assert ProviderType.DOCKER in _container_providers

    def test_register_overwrites_existing(self):
        """Overwrites existing provider."""
        original = DockerContainerProvider()
        mock_provider = MagicMock()
        mock_provider.provider_type = ProviderType.DOCKER
        
        _container_providers[ProviderType.DOCKER] = original
        register_container_provider(mock_provider)
        
        assert _container_providers[ProviderType.DOCKER] is mock_provider


class TestRegisterNetworkProvider:
    """Tests for register_network_provider function."""

    def test_register_custom_provider(self):
        """Registers custom provider."""
        mock_provider = MagicMock()
        mock_provider.provider_type = ProviderType.DOCKER
        
        register_network_provider(mock_provider)
        
        assert ProviderType.DOCKER in _network_providers

    def test_register_overwrites_existing(self):
        """Overwrites existing provider."""
        original = DockerNetworkProvider()
        mock_provider = MagicMock()
        mock_provider.provider_type = ProviderType.DOCKER
        
        _network_providers[ProviderType.DOCKER] = original
        register_network_provider(mock_provider)
        
        assert _network_providers[ProviderType.DOCKER] is mock_provider
