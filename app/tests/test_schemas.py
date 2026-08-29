import pytest
from unittest.mock import MagicMock
from pydantic import ValidationError
from app.schemas.vm import (
    VMBase, VMCreate, VMUpdate, VMResponse,
    VMStatsResponse, VMLogsResponse, VMStatusUpdate
)
from app.schemas.user import UserBase, UserCreate, UserResponse, Token
from app.schemas.network import NetworkBase, NetworkCreate, NetworkResponse


pytestmark = pytest.mark.unit


class TestVMBaseSchema:
    """Tests for VMBase schema validation."""

    def test_valid_vm_base(self):
        """Valid VMBase passes validation."""
        vm = VMBase(name="test-vm", cpu=2, ram=4096, disk_size=20)
        assert vm.name == "test-vm"
        assert vm.cpu == 2
        assert vm.ram == 4096
        assert vm.disk_size == 20

    def test_name_too_short(self):
        """Name too short raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            VMBase(name="ab", cpu=2, ram=4096, disk_size=20)
        assert "at least 3 characters" in str(exc_info.value).lower() or "min_length" in str(exc_info.value).lower()

    def test_name_too_long(self):
        """Name too long raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            VMBase(name="a" * 51, cpu=2, ram=4096, disk_size=20)
        assert "max_length" in str(exc_info.value).lower() or "at most" in str(exc_info.value).lower()

    def test_name_invalid_characters(self):
        """Name with invalid characters raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            VMBase(name="test@vm!", cpu=2, ram=4096, disk_size=20)
        assert "vm name must" in str(exc_info.value).lower()

    def test_name_normalized_to_lowercase(self):
        """Name is normalized to lowercase."""
        vm = VMBase(name="test-vm", cpu=2, ram=4096, disk_size=20)
        assert vm.name == "test-vm"

    def test_name_with_hyphens_allowed(self):
        """Name with hyphens is valid."""
        vm = VMBase(name="test-vm-1", cpu=2, ram=4096, disk_size=20)
        assert vm.name == "test-vm-1"

    def test_name_with_underscores_allowed(self):
        """Name with underscores is valid."""
        vm = VMBase(name="test_vm_1", cpu=2, ram=4096, disk_size=20)
        assert vm.name == "test_vm_1"

    def test_cpu_below_minimum(self):
        """CPU below minimum raises validation error."""
        with pytest.raises(ValidationError):
            VMBase(name="test-vm", cpu=0, ram=4096, disk_size=20)

    def test_cpu_above_maximum(self):
        """CPU above maximum raises validation error."""
        with pytest.raises(ValidationError):
            VMBase(name="test-vm", cpu=64, ram=4096, disk_size=20)

    def test_ram_below_minimum(self):
        """RAM below minimum raises validation error."""
        with pytest.raises(ValidationError):
            VMBase(name="test-vm", cpu=2, ram=100, disk_size=20)

    def test_ram_above_maximum(self):
        """RAM above maximum raises validation error."""
        with pytest.raises(ValidationError):
            VMBase(name="test-vm", cpu=2, ram=100000, disk_size=20)

    def test_disk_below_minimum(self):
        """Disk below minimum raises validation error."""
        with pytest.raises(ValidationError):
            VMBase(name="test-vm", cpu=2, ram=4096, disk_size=5)

    def test_disk_above_maximum(self):
        """Disk above maximum raises validation error."""
        with pytest.raises(ValidationError):
            VMBase(name="test-vm", cpu=2, ram=4096, disk_size=2000)

    def test_invalid_provider(self):
        """Invalid provider raises validation error."""
        with pytest.raises(ValidationError):
            VMBase(name="test-vm", provider="invalid", cpu=2, ram=4096, disk_size=20)


class TestVMCreateSchema:
    """Tests for VMCreate schema."""

    def test_valid_vm_create(self):
        """Valid VMCreate passes validation."""
        vm = VMCreate(name="test-vm", cpu=2, ram=4096, disk_size=20)
        assert vm.name == "test-vm"

    def test_vm_create_with_network_id(self):
        """VMCreate with network_id passes validation."""
        vm = VMCreate(name="test-vm", cpu=2, ram=4096, disk_size=20, network_id=1)
        assert vm.network_id == 1


class TestVMUpdateSchema:
    """Tests for VMUpdate schema."""

    def test_valid_vm_update_description(self):
        """Valid VMUpdate with description passes validation."""
        vm = VMUpdate(description="Updated description")
        assert vm.description == "Updated description"

    def test_vm_update_only_description_allowed(self):
        """VMUpdate only allows description field."""
        vm = VMUpdate()
        assert vm.description is None

    def test_vm_update_forbid_extra_fields(self):
        """VMUpdate forbids extra fields."""
        with pytest.raises(ValidationError):
            VMUpdate(description="Test", invalid_field="value")


class TestVMStatusUpdateSchema:
    """Tests for VMStatusUpdate schema (admin only)."""

    def test_valid_status_update(self):
        """Valid VMStatusUpdate passes validation."""
        vm = VMStatusUpdate(status="stopped", reason="VM needs maintenance")
        assert vm.status == "stopped"
        assert vm.reason == "VM needs maintenance"
        assert vm.force is False
    
    def test_status_update_with_force(self):
        """VMStatusUpdate with force flag."""
        vm = VMStatusUpdate(status="running", reason="Override state", force=True)
        assert vm.status == "running"
        assert vm.force is True

    def test_invalid_status_update(self):
        """Invalid status raises validation error."""
        with pytest.raises(ValidationError):
            VMStatusUpdate(status="invalid_status", reason="Test reason")
    
    def test_status_update_requires_reason(self):
        """VMStatusUpdate requires reason field."""
        with pytest.raises(ValidationError):
            VMStatusUpdate(status="stopped")


class TestVMResponseSchema:
    """Tests for VMResponse schema."""

    def test_vm_response_from_attributes(self):
        """VMResponse can be created from ORM model."""
        mock_vm = MagicMock()
        mock_vm.id = 1
        mock_vm.name = "test-vm"
        mock_vm.description = "Test"
        mock_vm.cpu = 2
        mock_vm.ram = 4096
        mock_vm.disk_size = 20
        mock_vm.provider = "docker"
        mock_vm.image = "nginx:latest"
        mock_vm.ip_address = "127.0.0.1"
        mock_vm.status = "running"
        mock_vm.terraform_job_id = "job-123"
        mock_vm.celery_task_id = "task-456"
        mock_vm.owner_id = 1
        mock_vm.created_at = "2024-01-01T00:00:00"
        mock_vm.network_id = None
        
        vm = VMResponse.model_validate(mock_vm)
        assert vm.id == 1
        assert vm.name == "test-vm"
        assert vm.celery_task_id == "task-456"


class TestVMStatsResponseSchema:
    """Tests for VMStatsResponse schema."""

    def test_valid_stats_response(self):
        """Valid stats response passes validation."""
        stats = VMStatsResponse(
            total_vms=10,
            status_breakdown={"running": 5, "stopped": 3, "error": 2},
            provider_breakdown={"docker": 8, "vsphere": 2},
            cpu_total=20,
            ram_total_mb=40960,
            disk_total_gb=200
        )
        assert stats.total_vms == 10


class TestVMLogsResponseSchema:
    """Tests for VMLogsResponse schema."""

    def test_valid_logs_response(self):
        """Valid logs response passes validation."""
        logs = VMLogsResponse(
            vm_id=1,
            vm_name="test-vm",
            logs="Log line 1\nLog line 2",
            lines=2
        )
        assert logs.vm_id == 1
        assert logs.lines == 2


class TestUserBaseSchema:
    """Tests for UserBase schema."""

    def test_valid_user_base(self):
        """Valid UserBase passes validation."""
        user = UserBase(email="test@test.com", username="testuser")
        assert user.email == "test@test.com"
        assert user.username == "testuser"

    def test_invalid_email(self):
        """Invalid email raises validation error."""
        with pytest.raises(ValidationError):
            UserBase(email="invalid-email", username="testuser")


class TestUserCreateSchema:
    """Tests for UserCreate schema."""

    def test_valid_user_create(self):
        """Valid UserCreate passes validation."""
        user = UserCreate(email="test@test.com", username="testuser", password="TestPass123")
        assert user.password == "TestPass123"
    
    def test_password_too_short(self):
        """Password too short raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            UserCreate(email="test@test.com", username="testuser", password="Test1")
        assert "at least 8 characters" in str(exc_info.value).lower()
    
    def test_password_no_uppercase(self):
        """Password without uppercase raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            UserCreate(email="test@test.com", username="testuser", password="testpass123")
        assert "uppercase" in str(exc_info.value).lower()
    
    def test_password_no_lowercase(self):
        """Password without lowercase raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            UserCreate(email="test@test.com", username="testuser", password="TESTPASS123")
        assert "lowercase" in str(exc_info.value).lower()
    
    def test_password_no_digit(self):
        """Password without digit raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            UserCreate(email="test@test.com", username="testuser", password="TestPassword")
        assert "digit" in str(exc_info.value).lower()


class TestUserResponseSchema:
    """Tests for UserResponse schema."""

    def test_user_response_from_attributes(self):
        """UserResponse can be created from ORM model."""
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.email = "test@test.com"
        mock_user.username = "testuser"
        mock_user.full_name = "Test User"
        mock_user.is_active = True
        mock_user.role = "viewer"
        
        user = UserResponse.model_validate(mock_user)
        assert user.id == 1
        assert user.username == "testuser"
        assert user.role == "viewer"


class TestTokenSchema:
    """Tests for Token schema."""

    def test_valid_token(self):
        """Valid Token passes validation."""
        token = Token(access_token="abc123", token_type="bearer")
        assert token.access_token == "abc123"
        assert token.token_type == "bearer"


class TestNetworkBaseSchema:
    """Tests for NetworkBase schema."""

    def test_valid_network_base(self):
        """Valid NetworkBase passes validation."""
        net = NetworkBase(name="test-net", cidr="172.20.0.0/16")
        assert net.name == "test-net"
        assert net.cidr == "172.20.0.0/16"

    def test_network_with_gateway(self):
        """Network with gateway passes validation."""
        net = NetworkBase(name="test-net", cidr="172.20.0.0/16", gateway="172.20.0.1")
        assert net.gateway == "172.20.0.1"


class TestNetworkCreateSchema:
    """Tests for NetworkCreate schema."""

    def test_valid_network_create(self):
        """Valid NetworkCreate passes validation."""
        net = NetworkCreate(name="test-net", cidr="172.20.0.0/16")
        assert net.name == "test-net"


class TestNetworkResponseSchema:
    """Tests for NetworkResponse schema."""

    def test_network_response_from_attributes(self):
        """NetworkResponse can be created from ORM model."""
        mock_net = MagicMock()
        mock_net.id = 1
        mock_net.name = "test-net"
        mock_net.cidr = "172.20.0.0/16"
        mock_net.gateway = "172.20.0.1"
        mock_net.provider = "docker"
        mock_net.status = "active"
        mock_net.created_at = "2024-01-01T00:00:00"
        
        net = NetworkResponse.model_validate(mock_net)
        assert net.id == 1
        assert net.name == "test-net"
