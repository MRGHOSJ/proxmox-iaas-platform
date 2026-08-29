import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


pytestmark = pytest.mark.unit


class TestValidateVmData:
    """Tests for _validate_vm_data function."""

    def test_valid_vm_data_proxmox(self):
        """Valid Proxmox VM data should pass validation."""
        from app.workers.tasks.helpers import _validate_vm_data
        
        vm_data = {
            "name": "test-vm",
            "provider": "proxmox",
            "cpu": 2,
            "ram": 4096,
            "disk_size": 20
        }
        
        _validate_vm_data(vm_data)

    def test_valid_vm_data_vsphere(self):
        """Valid vSphere VM data should pass validation."""
        from app.workers.tasks.helpers import _validate_vm_data
        
        vm_data = {
            "name": "test-vm",
            "provider": "vsphere",
            "cpu": 4,
            "ram": 8192,
            "disk_size": 50
        }
        
        _validate_vm_data(vm_data)

    def test_missing_name_raises(self):
        """Missing name should raise ValueError."""
        from app.workers.tasks.helpers import _validate_vm_data
        
        vm_data = {
            "provider": "proxmox",
            "cpu": 2,
            "ram": 4096,
            "disk_size": 20
        }
        
        with pytest.raises(ValueError) as exc_info:
            _validate_vm_data(vm_data)
        
        assert "name" in str(exc_info.value)

    def test_missing_provider_raises(self):
        """Missing provider should raise ValueError."""
        from app.workers.task_scheduler import _validate_vm_data
        
        vm_data = {
            "name": "test-vm",
            "cpu": 2,
            "ram": 4096,
            "disk_size": 20
        }
        
        with pytest.raises(ValueError) as exc_info:
            _validate_vm_data(vm_data)
        
        assert "provider" in str(exc_info.value)

    def test_invalid_provider_raises(self):
        """Invalid provider should raise ValueError."""
        from app.workers.task_scheduler import _validate_vm_data
        
        vm_data = {
            "name": "test-vm",
            "provider": "invalid",
            "cpu": 2,
            "ram": 4096,
            "disk_size": 20
        }
        
        with pytest.raises(ValueError) as exc_info:
            _validate_vm_data(vm_data)
        
        assert "provider" in str(exc_info.value)

    def test_invalid_cpu_type_raises(self):
        """Non-integer CPU should raise ValueError."""
        from app.workers.task_scheduler import _validate_vm_data
        
        vm_data = {
            "name": "test-vm",
            "provider": "proxmox",
            "cpu": "2",
            "ram": 4096,
            "disk_size": 20
        }
        
        with pytest.raises(ValueError) as exc_info:
            _validate_vm_data(vm_data)
        
        assert "CPU" in str(exc_info.value)

    def test_cpu_zero_raises(self):
        """Zero CPU should raise ValueError."""
        from app.workers.task_scheduler import _validate_vm_data
        
        vm_data = {
            "name": "test-vm",
            "provider": "proxmox",
            "cpu": 0,
            "ram": 4096,
            "disk_size": 20
        }
        
        with pytest.raises(ValueError) as exc_info:
            _validate_vm_data(vm_data)
        
        assert "CPU" in str(exc_info.value)

    def test_negative_cpu_raises(self):
        """Negative CPU should raise ValueError."""
        from app.workers.task_scheduler import _validate_vm_data
        
        vm_data = {
            "name": "test-vm",
            "provider": "proxmox",
            "cpu": -1,
            "ram": 4096,
            "disk_size": 20
        }
        
        with pytest.raises(ValueError) as exc_info:
            _validate_vm_data(vm_data)
        
        assert "CPU" in str(exc_info.value)

    def test_ram_too_low_raises(self):
        """RAM below 512MB should raise ValueError."""
        from app.workers.task_scheduler import _validate_vm_data
        
        vm_data = {
            "name": "test-vm",
            "provider": "proxmox",
            "cpu": 2,
            "ram": 256,
            "disk_size": 20
        }
        
        with pytest.raises(ValueError) as exc_info:
            _validate_vm_data(vm_data)
        
        assert "RAM" in str(exc_info.value)

    def test_disk_too_small_raises(self):
        """Disk below 10GB should raise ValueError."""
        from app.workers.task_scheduler import _validate_vm_data
        
        vm_data = {
            "name": "test-vm",
            "provider": "proxmox",
            "cpu": 2,
            "ram": 4096,
            "disk_size": 5
        }
        
        with pytest.raises(ValueError) as exc_info:
            _validate_vm_data(vm_data)
        
        assert "disk_size" in str(exc_info.value)

    def test_name_too_short_raises(self):
        """Name shorter than 3 chars should raise ValueError."""
        from app.workers.task_scheduler import _validate_vm_data
        
        vm_data = {
            "name": "ab",
            "provider": "proxmox",
            "cpu": 2,
            "ram": 4096,
            "disk_size": 20
        }
        
        with pytest.raises(ValueError) as exc_info:
            _validate_vm_data(vm_data)
        
        assert "name" in str(exc_info.value)

    def test_name_too_long_raises(self):
        """Name longer than 50 chars should raise ValueError."""
        from app.workers.task_scheduler import _validate_vm_data
        
        vm_data = {
            "name": "a" * 51,
            "provider": "proxmox",
            "cpu": 2,
            "ram": 4096,
            "disk_size": 20
        }
        
        with pytest.raises(ValueError) as exc_info:
            _validate_vm_data(vm_data)
        
        assert "name" in str(exc_info.value)

    def test_name_starts_with_number_raises(self):
        """Name starting with number should raise ValueError."""
        from app.workers.task_scheduler import _validate_vm_data
        
        vm_data = {
            "name": "123vm",
            "provider": "proxmox",
            "cpu": 2,
            "ram": 4096,
            "disk_size": 20
        }
        
        with pytest.raises(ValueError) as exc_info:
            _validate_vm_data(vm_data)
        
        assert "name" in str(exc_info.value).lower()

    def test_name_special_chars_raises(self):
        """Name with special characters should raise ValueError."""
        from app.workers.task_scheduler import _validate_vm_data
        
        vm_data = {
            "name": "test vm",
            "provider": "proxmox",
            "cpu": 2,
            "ram": 4096,
            "disk_size": 20
        }
        
        with pytest.raises(ValueError) as exc_info:
            _validate_vm_data(vm_data)
        
        assert "name" in str(exc_info.value)


class TestCleanupExpiredReservationsTask:
    """Tests for cleanup_expired_reservations_task."""

    @patch('app.workers.tasks.SessionLocal')
    def test_cleanup_expired_reservations_success(self, mock_session):
        """Test cleanup of expired reservations."""
        mock_db = MagicMock()
        mock_session.return_value = mock_db
        
        past_time = datetime.now(timezone.utc)
        mock_expired = MagicMock()
        mock_expired.ip_address = "192.168.1.100"
        mock_expired.expires_at = past_time
        mock_expired.status = "reserved"
        
        mock_db.query.return_value.filter.return_value.all.return_value = [mock_expired]
        
        from app.workers.task_scheduler import cleanup_expired_reservations_task
        with patch('app.workers.tasks.get_db') as mock_get_db:
            mock_get_db.return_value = iter([mock_db])
            result = cleanup_expired_reservations_task()
        
        assert result["status"] == "success"

    @patch('app.workers.tasks.SessionLocal')
    def test_cleanup_expired_reservations_no_expired(self, mock_session):
        """Test when no expired reservations exist."""
        mock_db = MagicMock()
        mock_session.return_value = mock_db
        
        mock_db.query.return_value.filter.return_value.all.return_value = []
        
        from app.workers.task_scheduler import cleanup_expired_reservations_task
        with patch('app.workers.tasks.get_db') as mock_get_db:
            mock_get_db.return_value = iter([mock_db])
            result = cleanup_expired_reservations_task()
        
        assert result["status"] == "success"

    @patch('app.workers.tasks.SessionLocal')
    def test_cleanup_expired_reservations_error(self, mock_session):
        """Test error handling in cleanup."""
        mock_db = MagicMock()
        mock_session.return_value = mock_db
        mock_db.query.side_effect = Exception("DB error")
        
        from app.workers.task_scheduler import cleanup_expired_reservations_task
        with patch('app.workers.tasks.get_db') as mock_get_db:
            mock_get_db.return_value = iter([mock_db])
            result = cleanup_expired_reservations_task()
        
        assert result["status"] == "error"


class TestCleanupVmOnFailure:
    """Tests for _cleanup_vm_on_failure function."""

    def test_cleanup_vm_with_ip_release(self):
        """Test cleanup with IP release."""
        mock_db = MagicMock()
        mock_vm = MagicMock()
        mock_vm.id = 1
        mock_vm.ip_address = "192.168.1.100"
        mock_vm.network_id = 1
        mock_vm.status = "provisioning"
        
        with patch('app.services.ipam.release_ip_reservation') as mock_release:
            from app.workers.task_scheduler import _cleanup_vm_on_failure
            _cleanup_vm_on_failure(mock_db, mock_vm, "Test error", release_ip=True)
        
        assert mock_vm.status == "error"
        assert mock_vm.ip_address is None
        mock_db.commit.assert_called()

    def test_cleanup_vm_without_ip_release(self):
        """Test cleanup without IP release."""
        mock_db = MagicMock()
        mock_vm = MagicMock()
        mock_vm.id = 1
        mock_vm.ip_address = "192.168.1.100"
        mock_vm.network_id = 1
        mock_vm.status = "provisioning"
        
        from app.workers.task_scheduler import _cleanup_vm_on_failure
        _cleanup_vm_on_failure(mock_db, mock_vm, "Test error", release_ip=False)
        
        assert mock_vm.status == "error"

    def test_cleanup_vm_exception_handling(self):
        """Test cleanup handles exceptions."""
        mock_db = MagicMock()
        mock_db.commit.side_effect = Exception("Commit failed")
        mock_vm = MagicMock()
        mock_vm.id = 1
        mock_vm.ip_address = None
        mock_vm.network_id = None
        
        from app.workers.task_scheduler import _cleanup_vm_on_failure
        _cleanup_vm_on_failure(mock_db, mock_vm, "Test error")
        
        mock_db.rollback.assert_called()


class TestAttemptTerraformRollback:
    """Tests for _attempt_terraform_rollback function."""

    @patch('app.workers.tasks.destroy_terraform_job')
    def test_terraform_rollback_success(self, mock_destroy):
        """Test successful Terraform rollback."""
        mock_destroy.return_value = {"status": "destroyed"}
        
        from app.workers.task_scheduler import _attempt_terraform_rollback
        _attempt_terraform_rollback(1, "test-vm", "tf code", {"var": "val"})
        
        mock_destroy.assert_called_once()

    @patch('app.workers.tasks.destroy_terraform_job')
    def test_terraform_rollback_failure(self, mock_destroy):
        """Test Terraform rollback handles failure."""
        mock_destroy.side_effect = Exception("Destroy failed")
        
        from app.workers.task_scheduler import _attempt_terraform_rollback
        _attempt_terraform_rollback(1, "test-vm", "tf code", {"var": "val"})


class TestDeployVmTask:
    """Tests for deploy_vm_task Celery task."""

    @pytest.mark.skip(reason="Celery tasks require complex mocking")
    def test_deploy_vm_task_vm_not_found(self):
        pass


class TestDeployNetworkTask:
    """Tests for deploy_network_task Celery task."""

    @pytest.mark.skip(reason="Celery tasks require complex mocking")
    def test_deploy_network_task_network_not_found(self):
        pass

    @pytest.mark.skip(reason="Celery tasks require complex mocking")
    def test_deploy_network_task_sets_error_on_failure(self):
        pass


class TestTasksConstants:
    """Tests for task constants."""

    def test_max_retries_value(self):
        """MAX_RETRIES should be 3."""
        from app.workers.task_scheduler import MAX_RETRIES, RETRY_DELAY, get_db
        assert MAX_RETRIES == 3

    def test_retry_delay_value(self):
        """RETRY_DELAY should be 5."""
        from app.workers.task_scheduler import RETRY_DELAY
        assert RETRY_DELAY == 5


class TestGetDbHelper:
    """Tests for get_db helper function."""

    @patch('app.workers.tasks.SessionLocal')
    def test_get_db_yields_session(self, mock_session):
        """get_db yields a session."""
        mock_db = MagicMock()
        mock_session.return_value = mock_db
        
        from app.workers.task_scheduler import get_db
        gen = get_db()
        result = next(gen)
        
        assert result == mock_db

    @patch('app.workers.tasks.SessionLocal')
    def test_get_db_closes_on_exit(self, mock_session):
        """get_db closes session on exit."""
        mock_db = MagicMock()
        mock_session.return_value = mock_db
        
        from app.workers.task_scheduler import get_db
        gen = get_db()
        next(gen)
        gen.close()
        
        mock_db.close.assert_called_once()
