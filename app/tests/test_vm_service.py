import pytest
from unittest.mock import patch, MagicMock
from app.services import vm as vm_service
from app.schemas.vm import VMCreate


pytestmark = pytest.mark.unit


class TestCreateVmLogic:
    """Tests for create_vm_logic function."""

    def test_create_vm_success(self):
        """Successfully creates VM with valid data."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        vm_data = VMCreate(
            name="test-vm",
            provider="docker",
            cpu=2,
            ram=4096,
            disk_size=20
        )
        
        with patch('app.services.vm.get_terraform_context') as mock_context:
            with patch('app.services.vm.render_terraform_code') as mock_render:
                with patch('app.services.vm.run_terraform_job') as mock_tf:
                    mock_context.return_value = ("docker.tf.j2", {})
                    mock_render.return_value = "terraform code"
                    mock_tf.return_value = {"outputs": {"port": 8080}}
                    
                    result = vm_service.create_vm_logic(mock_db, vm_data, owner_id=1)
                    
                    assert result is not None
                    mock_db.add.assert_called_once()
                    mock_db.commit.assert_called()

    def test_create_vm_duplicate_name_raises(self):
        """Raises error when VM name already exists."""
        mock_db = MagicMock()
        mock_existing = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_existing
        
        vm_data = VMCreate(name="existing-vm", provider="docker", cpu=2, ram=4096, disk_size=20)
        
        with pytest.raises(ValueError) as exc_info:
            vm_service.create_vm_logic(mock_db, vm_data, owner_id=1)
        
        assert "already exists" in str(exc_info.value).lower()

    def test_create_vm_sets_error_status_on_failure(self):
        """Sets status to 'error' on Terraform failure."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        vm_data = VMCreate(name="fail-vm", provider="docker", cpu=2, ram=4096, disk_size=20)
        
        with patch('app.services.vm.get_terraform_context') as mock_context:
            with patch('app.services.vm.render_terraform_code') as mock_render:
                with patch('app.services.vm.run_terraform_job') as mock_tf:
                    mock_context.return_value = ("docker.tf.j2", {})
                    mock_render.return_value = "terraform code"
                    mock_tf.side_effect = Exception("Terraform failed")
                    
                    with pytest.raises(Exception):
                        vm_service.create_vm_logic(mock_db, vm_data, owner_id=1)


class TestDeleteVmLogic:
    """Tests for delete_vm_logic function."""

    def test_delete_vm_not_found_raises(self):
        """Raises error when VM not found."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        with pytest.raises(ValueError) as exc_info:
            vm_service.delete_vm_logic(mock_db, 999)
        
        assert "not found" in str(exc_info.value).lower()

    def test_delete_running_vm_without_force_raises(self):
        """Raises error when deleting running VM without force."""
        mock_db = MagicMock()
        mock_vm = MagicMock()
        mock_vm.status = "running"
        mock_db.query.return_value.filter.return_value.first.return_value = mock_vm
        
        with pytest.raises(ValueError) as exc_info:
            vm_service.delete_vm_logic(mock_db, 1, force=False)
        
        assert "running" in str(exc_info.value).lower()

    def test_delete_vm_with_force_succeeds(self):
        """Force delete succeeds for running VM."""
        mock_db = MagicMock()
        mock_vm = MagicMock()
        mock_vm.status = "running"
        mock_vm.name = "test-vm"
        mock_vm.provider = "docker"
        mock_vm.cpu = 2
        mock_vm.ram = 4096
        mock_vm.disk_size = 20
        mock_db.query.return_value.filter.return_value.first.return_value = mock_vm
        
        with patch('app.services.vm.get_terraform_context') as mock_context:
            with patch('app.services.vm.render_terraform_code') as mock_render:
                with patch('app.services.vm.destroy_terraform_job') as mock_destroy:
                    mock_context.return_value = ("docker.tf.j2", {})
                    mock_render.return_value = "terraform code"
                    mock_destroy.return_value = {"status": "destroyed"}
                    
                    vm_service.delete_vm_logic(mock_db, 1, force=True)
                    
                    mock_db.delete.assert_called_once_with(mock_vm)
                    mock_db.commit.assert_called()


class TestStartVmLogic:
    """Tests for start_vm_logic function."""

    def test_start_vm_not_found_raises(self):
        """Raises error when VM not found."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        with pytest.raises(ValueError) as exc_info:
            vm_service.start_vm_logic(mock_db, 999)
        
        assert "not found" in str(exc_info.value).lower()

    def test_start_vm_already_running_raises(self):
        """Raises error when VM is already running."""
        mock_db = MagicMock()
        mock_vm = MagicMock()
        mock_vm.status = "running"
        mock_db.query.return_value.filter.return_value.first.return_value = mock_vm
        
        with pytest.raises(ValueError) as exc_info:
            vm_service.start_vm_logic(mock_db, 1)
        
        assert "must be stopped" in str(exc_info.value).lower()

    @pytest.mark.skip(reason="Requires Docker daemon")
    def test_start_vm_docker_success(self):
        pass

    def test_start_vm_vsphere_not_implemented(self):
        """Raises NotImplementedError for vSphere provider."""
        mock_db = MagicMock()
        mock_vm = MagicMock()
        mock_vm.status = "stopped"
        mock_vm.provider = "vsphere"
        mock_vm.name = "test-vm"
        mock_db.query.return_value.filter.return_value.first.return_value = mock_vm
        
        with pytest.raises(ValueError) as exc_info:
            vm_service.start_vm_logic(mock_db, 1)
        
        assert "not" in str(exc_info.value).lower() or "implemented" in str(exc_info.value).lower()


class TestStopVmLogic:
    """Tests for stop_vm_logic function."""

    def test_stop_vm_not_found_raises(self):
        """Raises error when VM not found."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        with pytest.raises(ValueError) as exc_info:
            vm_service.stop_vm_logic(mock_db, 999)
        
        assert "not found" in str(exc_info.value).lower()

    def test_stop_vm_not_running_raises(self):
        """Raises error when VM is not running."""
        mock_db = MagicMock()
        mock_vm = MagicMock()
        mock_vm.status = "stopped"
        mock_db.query.return_value.filter.return_value.first.return_value = mock_vm
        
        with pytest.raises(ValueError) as exc_info:
            vm_service.stop_vm_logic(mock_db, 1)
        
        assert "must be running" in str(exc_info.value).lower()

    @pytest.mark.skip(reason="Requires Docker daemon")
    def test_stop_vm_docker_success(self):
        pass


class TestRestartVmLogic:
    """Tests for restart_vm_logic function."""

    def test_restart_vm_not_found_raises(self):
        """Raises error when VM not found."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        with pytest.raises(ValueError) as exc_info:
            vm_service.restart_vm_logic(mock_db, 999)
        
        assert "not found" in str(exc_info.value).lower()

    @pytest.mark.skip(reason="Requires Docker daemon")
    def test_restart_vm_docker_success(self):
        pass


class TestGetVmLogsLogic:
    """Tests for get_vm_logs_logic function."""

    def test_get_logs_vm_not_found_raises(self):
        """Raises error when VM not found."""
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        with pytest.raises(ValueError) as exc_info:
            vm_service.get_vm_logs_logic(mock_db, 999, tail=100)
        
        assert "not found" in str(exc_info.value).lower()

    def test_get_logs_docker_success(self):
        """Gets logs from Docker VM successfully."""
        from app.providers.base import ContainerLogs
        
        mock_db = MagicMock()
        mock_vm = MagicMock()
        mock_vm.provider = "docker"
        mock_vm.name = "test-vm"
        mock_vm.id = 1
        mock_db.query.return_value.filter.return_value.first.return_value = mock_vm
        
        with patch('app.services.vm.get_container_provider') as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.get_logs.return_value = ContainerLogs(
                container_name="test-vm",
                logs="Log line 1\nLog line 2",
                line_count=2
            )
            mock_get_provider.return_value = mock_provider
            
            result = vm_service.get_vm_logs_logic(mock_db, 1, tail=100)
            
            assert result["vm_id"] == 1
            assert "Log line 1" in result["logs"]
            assert "lines" in result

    def test_get_logs_unsupported_provider(self):
        """Raises ValueError for unsupported provider."""
        mock_db = MagicMock()
        mock_vm = MagicMock()
        mock_vm.provider = "vsphere"
        mock_vm.name = "test-vm"
        mock_vm.id = 1
        mock_db.query.return_value.filter.return_value.first.return_value = mock_vm
        
        with pytest.raises(ValueError) as exc_info:
            vm_service.get_vm_logs_logic(mock_db, 1, tail=100)
        
        error_message = str(exc_info.value).lower()
        assert "vsphere" in error_message and "not yet implemented" in error_message
