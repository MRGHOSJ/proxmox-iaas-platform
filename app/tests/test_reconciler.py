import pytest
import json
from unittest.mock import patch, MagicMock
from app.services.reconciler import Reconciler
from app.models.vm import VM
from app.providers.base import ContainerInfo


pytestmark = pytest.mark.reconciler


class TestGetContainerStatus:
    """Tests for Reconciler.get_container_status method."""

    def test_empty_containers(self):
        """Returns empty dict when no containers."""
        reconciler = Reconciler()
        
        with patch('app.services.reconciler.get_container_provider') as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.list_containers.return_value = []
            mock_get_provider.return_value = mock_provider
            
            result = reconciler.get_container_status()
            
            assert result == {}

    def test_single_running_container(self):
        """Parses single running container correctly."""
        reconciler = Reconciler()
        
        with patch('app.services.reconciler.get_container_provider') as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.list_containers.return_value = [
                ContainerInfo(name="test-vm", status="running")
            ]
            mock_get_provider.return_value = mock_provider
            
            result = reconciler.get_container_status()
            
            assert "test-vm" in result
            assert result["test-vm"] == "running"

    def test_single_stopped_container(self):
        """Parses stopped container as 'stopped'."""
        reconciler = Reconciler()
        
        with patch('app.services.reconciler.get_container_provider') as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.list_containers.return_value = [
                ContainerInfo(name="test-vm", status="stopped")
            ]
            mock_get_provider.return_value = mock_provider
            
            result = reconciler.get_container_status()
            
            assert result["test-vm"] == "stopped"

    def test_multiple_containers(self):
        """Parses multiple containers."""
        reconciler = Reconciler()
        
        with patch('app.services.reconciler.get_container_provider') as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.list_containers.return_value = [
                ContainerInfo(name="vm1", status="running"),
                ContainerInfo(name="vm2", status="stopped"),
            ]
            mock_get_provider.return_value = mock_provider
            
            result = reconciler.get_container_status()
            
            assert len(result) == 2
            assert result["vm1"] == "running"
            assert result["vm2"] == "stopped"

    def test_provider_exception(self):
        """Returns empty dict on provider exception."""
        reconciler = Reconciler()

        from app.providers.base import ProviderException, ProviderType

        with patch('app.services.reconciler.get_container_provider') as mock_get_provider:
            mock_get_provider.side_effect = ProviderException("Error", ProviderType.DOCKER)

            result = reconciler.get_container_status()

            assert result == {}


class TestAudit:
    """Tests for Reconciler.audit method."""

    def test_audit_all_synced(self):
        """Returns synced list when all VMs match."""
        reconciler = Reconciler()
        
        mock_vm = MagicMock()
        mock_vm.name = "synced-vm"
        mock_vm.status = "running"
        mock_vm.provider = "docker"
        
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [mock_vm]
        
        with patch.object(reconciler, 'get_container_status') as mock_status:
            mock_status.return_value = {"synced-vm": "running"}
            
            result = reconciler.audit(mock_db)
            
            assert result["synced"] == ["synced-vm"]
            assert result["orphans"] == []
            assert result["ghosts"] == []
            assert result["drift"] == []

    def test_audit_detects_orphans(self):
        """Detects containers not in DB as orphans."""
        reconciler = Reconciler()
        
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []
        
        with patch.object(reconciler, 'get_container_status') as mock_status:
            mock_status.return_value = {"orphan-vm": "running"}
            
            result = reconciler.audit(mock_db)
            
            assert len(result["orphans"]) == 1
            assert result["orphans"][0]["name"] == "orphan-vm"

    def test_audit_detects_ghosts(self):
        """Detects VMs in DB but not in Docker as ghosts."""
        reconciler = Reconciler()
        
        mock_vm = MagicMock()
        mock_vm.id = 1
        mock_vm.name = "ghost-vm"
        mock_vm.status = "running"
        mock_vm.provider = "docker"
        
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [mock_vm]
        
        with patch.object(reconciler, 'get_container_status') as mock_status:
            mock_status.return_value = {}
            
            result = reconciler.audit(mock_db)
            
            assert len(result["ghosts"]) == 1
            assert result["ghosts"][0]["vm_id"] == 1

    def test_audit_detects_drift_running_to_stopped(self):
        """Detects drift: DB says running, Docker says stopped."""
        reconciler = Reconciler()
        
        mock_vm = MagicMock()
        mock_vm.id = 1
        mock_vm.name = "drift-vm"
        mock_vm.status = "running"
        mock_vm.provider = "docker"
        
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [mock_vm]
        
        with patch.object(reconciler, 'get_container_status') as mock_status:
            mock_status.return_value = {"drift-vm": "stopped"}
            
            result = reconciler.audit(mock_db)
            
            assert len(result["drift"]) == 1
            assert result["drift"][0]["db_status"] == "running"
            assert result["drift"][0]["real_status"] == "stopped"

    def test_audit_detects_drift_stopped_to_running(self):
        """Detects drift: DB says stopped, Docker says running."""
        reconciler = Reconciler()
        
        mock_vm = MagicMock()
        mock_vm.id = 1
        mock_vm.name = "drift-vm"
        mock_vm.status = "stopped"
        mock_vm.provider = "docker"
        
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = [mock_vm]
        
        with patch.object(reconciler, 'get_container_status') as mock_status:
            mock_status.return_value = {"drift-vm": "running"}
            
            result = reconciler.audit(mock_db)
            
            assert len(result["drift"]) == 1


class TestReconcileAll:
    """Tests for Reconciler.reconcile_all method."""

    def test_reconcile_purges_orphans(self):
        """Deletes orphaned containers from Docker."""
        reconciler = Reconciler()
        
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []
        
        with patch.object(reconciler, 'audit') as mock_audit:
            mock_audit.return_value = {
                "orphans": [{"name": "orphan-vm", "status": "running"}],
                "ghosts": [],
                "drift": []
            }
            
            with patch('app.services.reconciler.get_container_provider') as mock_get_provider:
                mock_provider = MagicMock()
                mock_provider.remove.return_value = True
                mock_get_provider.return_value = mock_provider
                
                result = reconciler.reconcile_all(mock_db)
                
                assert "orphan-vm" in result["orphan_purged"]

    def test_reconcile_purges_ghosts(self):
        """Deletes ghost VM records from DB."""
        reconciler = Reconciler()
        
        mock_vm = MagicMock()
        mock_vm.id = 1
        mock_vm.name = "ghost-vm"
        
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []
        mock_db.query.return_value.filter.return_value.first.return_value = mock_vm
        
        with patch.object(reconciler, 'audit') as mock_audit:
            mock_audit.return_value = {
                "orphans": [],
                "ghosts": [{"vm_id": 1, "name": "ghost-vm", "db_status": "running"}],
                "drift": []
            }
            
            result = reconciler.reconcile_all(mock_db)
            
            assert "ghost-vm" in result["ghost_purged"]
            mock_db.delete.assert_called()

    def test_reconcile_corrects_drift(self):
        """Updates DB status to match Docker for drifted VMs."""
        reconciler = Reconciler()
        
        mock_vm = MagicMock()
        mock_vm.id = 1
        mock_vm.name = "drift-vm"
        mock_vm.status = "running"
        
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []
        mock_db.query.return_value.filter.return_value.first.return_value = mock_vm
        
        with patch.object(reconciler, 'audit') as mock_audit:
            mock_audit.return_value = {
                "orphans": [],
                "ghosts": [],
                "drift": [{"vm_id": 1, "name": "drift-vm", "db_status": "running", "real_status": "stopped"}]
            }
            
            result = reconciler.reconcile_all(mock_db)
            
            assert len(result["drift_corrected"]) == 1
            assert result["drift_corrected"][0]["new_status"] == "stopped"

    def test_reconcile_no_actions_needed(self):
        """Returns empty lists when no reconciliation needed."""
        reconciler = Reconciler()
        
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.all.return_value = []
        
        with patch.object(reconciler, 'audit') as mock_audit:
            mock_audit.return_value = {
                "orphans": [],
                "ghosts": [],
                "drift": []
            }
            
            result = reconciler.reconcile_all(mock_db)
            
            assert result["orphan_purged"] == []
            assert result["ghost_purged"] == []
            assert result["drift_corrected"] == []


class TestFixGhostVm:
    """Tests for Reconciler.fix_ghost_vm method."""

    def test_fix_ghost_vm_success(self):
        """Sets status to pending and dispatches deploy task."""
        reconciler = Reconciler()
        
        mock_vm = MagicMock()
        mock_vm.id = 1
        mock_vm.name = "ghost-vm"
        mock_vm.provider = "docker"
        mock_vm.cpu = 2
        mock_vm.ram = 2048
        mock_vm.disk_size = 20
        mock_vm.network_id = None
        
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_vm
        
        with patch('app.workers.tasks.deploy_vm_task') as mock_task:
            mock_task.delay = MagicMock()
            
            success, message = reconciler.fix_ghost_vm(mock_db, 1)
            
            assert success is True
            assert "dispatched" in message.lower()
            mock_vm.status = "pending"

    def test_fix_ghost_vm_not_found(self):
        """Returns failure when VM not found."""
        reconciler = Reconciler()
        
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None
        
        success, message = reconciler.fix_ghost_vm(mock_db, 999)
        
        assert success is False
        assert "not found" in message.lower()
