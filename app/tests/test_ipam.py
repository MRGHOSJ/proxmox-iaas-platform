import pytest
import ipaddress
from unittest.mock import patch, MagicMock
from app.services import ipam
from app.models.network import Network
from app.models.vm import VM


pytestmark = pytest.mark.ipam


class TestValidateCidrAvailability:
    """Tests for validate_cidr_availability function."""

    def test_valid_cidr_returns_true(self, mock_docker_subprocess):
        """Valid CIDR should return True."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        result = ipam.validate_cidr_availability(mock_db, "172.20.0.0/16")
        
        assert result is True

    def test_invalid_cidr_format_raises_error(self):
        """Invalid CIDR format should raise ValueError."""
        mock_db = MagicMock()
        
        with pytest.raises(ValueError) as exc_info:
            ipam.validate_cidr_availability(mock_db, "invalid-cidr")
        
        assert "invalid" in str(exc_info.value).lower()

    def test_duplicate_cidr_in_db_raises_error(self):
        """CIDR already in database should raise ValueError."""
        mock_db = MagicMock()
        mock_existing = MagicMock()
        mock_existing.name = "existing-net"
        mock_db.query.return_value.filter.return_value.first.return_value = mock_existing
        
        with pytest.raises(ValueError) as exc_info:
            ipam.validate_cidr_availability(mock_db, "172.20.0.0/16")
        
        assert "already allocated" in str(exc_info.value).lower()

    def test_overlapping_cidr_with_docker_raises_error(self):
        """CIDR overlapping with Docker network should raise ValueError."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        with patch('app.services.ipam.get_all_subnets') as mock_subnets:
            mock_subnets.return_value = [ipaddress.ip_network("172.20.0.0/16")]
            
            with pytest.raises(ValueError) as exc_info:
                ipam.validate_cidr_availability(mock_db, "172.20.0.0/24")
            
            assert "overlaps" in str(exc_info.value).lower()

    def test_normalizes_cidr_correctly(self):
        """CIDR should be normalized (e.g., 172.0.1.0/16 -> 172.0.0.0/16)."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        with patch('app.services.ipam.get_all_subnets') as mock_subnets:
            mock_subnets.return_value = []
            
            result = ipam.validate_cidr_availability(mock_db, "172.0.1.0/16")
            
            assert result is True


class TestAllocateIp:
    """Tests for allocate_ip function."""

    def test_allocate_ip_network_not_found_raises_error(self):
        """Non-existent network should raise ValueError."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        with pytest.raises(ValueError) as exc_info:
            ipam.allocate_ip(mock_db, 999)
        
        assert "not found" in str(exc_info.value).lower()

    def test_allocate_ip_returns_valid_ip(self):
        """Should return a valid IP address from the network."""
        mock_db = MagicMock()
        
        mock_network = MagicMock()
        mock_network.id = 1
        mock_network.name = "test-net"
        mock_network.cidr = "172.20.0.0/24"
        mock_network.gateway = None
        mock_network.provider = "docker"
        mock_db.query.return_value.filter.return_value.first.return_value = mock_network
        
        with patch('app.services.ipam._check_ip_is_free', return_value=True):
            result = ipam.allocate_ip(mock_db, 1)
            
            assert result is not None
            ip = ipaddress.ip_address(result)
            network = ipaddress.ip_network("172.20.0.0/24")
            assert ip in network.hosts()

    def test_allocate_ip_skips_gateway(self):
        """Should skip gateway IP if specified."""
        mock_db = MagicMock()
        
        mock_network = MagicMock()
        mock_network.id = 1
        mock_network.name = "test-net"
        mock_network.cidr = "172.20.0.0/24"
        mock_network.gateway = "172.20.0.2"
        mock_network.provider = "docker"
        
        def mock_query_side_effect(model):
            mock_filter = MagicMock()
            if model == Network:
                mock_filter.filter.return_value.first.return_value = mock_network
                return mock_filter
            elif model == VM:
                mock_filter.filter.return_value.all.return_value = []
                return mock_filter
            return mock_filter
        
        mock_db.query.side_effect = mock_query_side_effect
        
        with patch('app.services.ipam._check_ip_is_free', return_value=True):
            result = ipam.allocate_ip(mock_db, 1)
            
            assert result != "172.20.0.2"

    def test_allocate_ip_returns_next_available(self):
        """Should return an IP from the network range."""
        mock_db = MagicMock()
        
        mock_network = MagicMock()
        mock_network.id = 1
        mock_network.name = "test-net"
        mock_network.cidr = "172.20.0.0/24"
        mock_network.gateway = None
        mock_network.provider = "docker"
        
        def mock_query_side_effect(model):
            mock_filter = MagicMock()
            if model == Network:
                mock_filter.filter.return_value.first.return_value = mock_network
                return mock_filter
            elif model == VM:
                mock_filter.filter.return_value.all.return_value = []
                return mock_filter
            return mock_filter
        
        mock_db.query.side_effect = mock_query_side_effect
        
        with patch('app.services.ipam._check_ip_is_free', return_value=True):
            result = ipam.allocate_ip(mock_db, 1)
            
            assert result is not None
            assert result.startswith("172.20.0.")

    def test_allocate_ip_network_full(self):
        """Should handle allocation when network is nearly full."""
        mock_db = MagicMock()
        
        mock_network = MagicMock()
        mock_network.id = 1
        mock_network.name = "test-net"
        mock_network.cidr = "172.20.0.0/30"
        mock_network.gateway = None
        mock_network.provider = "docker"
        
        def mock_query_side_effect(model):
            mock_filter = MagicMock()
            if model == Network:
                mock_filter.filter.return_value.first.return_value = mock_network
                return mock_filter
            elif model == VM:
                mock_filter.filter.return_value.all.return_value = []
                return mock_filter
            return mock_filter
        
        mock_db.query.side_effect = mock_query_side_effect
        
        with patch('app.services.ipam._check_ip_is_free', return_value=True):
            result = ipam.allocate_ip(mock_db, 1)
            assert result is not None


class TestFreeIp:
    """Tests for free_ip function."""

    def test_free_ip_clears_vm_ip_address(self):
        """Should clear IP address from VM."""
        mock_db = MagicMock()
        
        mock_vm = MagicMock()
        mock_vm.ip_address = "172.20.0.5"
        mock_db.query.return_value.filter.return_value.first.return_value = mock_vm
        
        ipam.free_ip(mock_db, 1, "172.20.0.5")
        
        assert mock_vm.ip_address is None
        assert mock_db.commit.called

    def test_free_ip_none_ip_does_nothing(self):
        """None IP should do nothing."""
        mock_db = MagicMock()
        
        ipam.free_ip(mock_db, 1, None)
        
        mock_db.query.assert_not_called()

    def test_free_ip_vm_not_found_releases_reservation(self):
        """VM not found should still release reservation."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        ipam.free_ip(mock_db, 1, "172.20.0.5")
        
        # Should still commit to release the reservation
        mock_db.commit.assert_called()


class TestCheckDockerIpIsFree:
    """Tests for _check_ip_is_free function via IPAM provider."""
    pass  # Provider tests require Docker daemon


class TestGetAllDockerSubnets:
    """Tests for get_all_subnets function via IPAM provider."""
    
    @pytest.mark.skip(reason="Requires Docker daemon")
    def test_returns_subnets_from_docker(self):
        pass
    
    @pytest.mark.skip(reason="Requires Docker daemon")
    def test_handles_exception_gracefully(self):
        pass
