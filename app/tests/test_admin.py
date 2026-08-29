import pytest
from unittest.mock import patch, MagicMock
from conftest import NetworkHelper, VMHelper


pytestmark = pytest.mark.admin


class TestAuditEndpoint:
    """Tests for /v1/admin/audit endpoint."""

    def test_audit_empty_infrastructure(self, client, admin_headers):
        """Test audit when no VMs exist."""
        with patch('app.services.reconciler.Reconciler.audit') as mock_audit:
            mock_audit.return_value = {
                "orphans": [],
                "ghosts": [],
                "drift": [],
                "synced": []
            }
            
            response = client.get("/v1/admin/audit", headers=admin_headers)
            
            assert response.status_code == 200
            data = response.json()
            assert data["orphans"] == []
            assert data["ghosts"] == []
            assert data["drift"] == []
            assert data["synced"] == []

    def test_audit_detects_orphans(self, client, admin_headers):
        """Test audit detects orphaned containers."""
        with patch('app.services.reconciler.Reconciler.audit') as mock_audit:
            mock_audit.return_value = {
                "orphans": [{"name": "orphan-vm", "status": "running"}],
                "ghosts": [],
                "drift": [],
                "synced": []
            }
            
            response = client.get("/v1/admin/audit", headers=admin_headers)
            
            assert response.status_code == 200
            assert len(response.json()["orphans"]) == 1
            assert response.json()["orphans"][0]["name"] == "orphan-vm"

    def test_audit_detects_ghosts(self, client, admin_headers):
        """Test audit detects ghost VM records."""
        with patch('app.services.reconciler.Reconciler.audit') as mock_audit:
            mock_audit.return_value = {
                "orphans": [],
                "ghosts": [{"vm_id": 1, "name": "ghost-vm", "db_status": "running"}],
                "drift": [],
                "synced": []
            }
            
            response = client.get("/v1/admin/audit", headers=admin_headers)
            
            assert response.status_code == 200
            assert len(response.json()["ghosts"]) == 1

    def test_audit_detects_drift(self, client, admin_headers):
        """Test audit detects status drift."""
        with patch('app.services.reconciler.Reconciler.audit') as mock_audit:
            mock_audit.return_value = {
                "orphans": [],
                "ghosts": [],
                "drift": [{"vm_id": 1, "name": "drift-vm", "db_status": "running", "real_status": "stopped"}],
                "synced": []
            }
            
            response = client.get("/v1/admin/audit", headers=admin_headers)
            
            assert response.status_code == 200
            assert len(response.json()["drift"]) == 1

    def test_audit_unauthorized(self, client):
        """Test audit requires authentication."""
        response = client.get("/v1/admin/audit")
        assert response.status_code == 401

    def test_audit_forbidden_for_non_admin(self, client, auth_headers):
        """Test audit is forbidden for non-admin users."""
        response = client.get("/v1/admin/audit", headers=auth_headers)
        assert response.status_code == 403


class TestFixVmEndpoint:
    """Tests for /v1/admin/fix/{vm_id} endpoint."""

    def test_fix_ghost_vm_success(self, client, admin_headers, created_vm):
        """Test fixing a ghost VM."""
        vm_id = created_vm["id"]
        
        with patch('app.services.reconciler.Reconciler.fix_ghost_vm') as mock_fix:
            mock_fix.return_value = (True, "Re-provision task dispatched successfully")
            
            response = client.post(f"/v1/admin/fix/{vm_id}", headers=admin_headers)
            
            assert response.status_code == 200
            assert response.json()["status"] == "success"
            assert response.json()["vm_id"] == vm_id

    def test_fix_vm_not_found(self, client, admin_headers):
        """Test fixing non-existent VM."""
        with patch('app.services.reconciler.Reconciler.fix_ghost_vm') as mock_fix:
            mock_fix.return_value = (False, "VM not found")
            
            response = client.post("/v1/admin/fix/99999", headers=admin_headers)
            
            assert response.status_code == 404

    def test_fix_vm_failure(self, client, admin_headers, created_vm):
        """Test fix failure returns 400."""
        vm_id = created_vm["id"]
        
        with patch('app.services.reconciler.Reconciler.fix_ghost_vm') as mock_fix:
            mock_fix.return_value = (False, "Failed to re-provision")
            
            response = client.post(f"/v1/admin/fix/{vm_id}", headers=admin_headers)
            
            assert response.status_code == 400

    def test_fix_vm_unauthorized(self, client):
        """Test fix requires authentication."""
        response = client.post("/v1/admin/fix/1")
        assert response.status_code == 401

    def test_fix_vm_forbidden_for_non_admin(self, client, auth_headers, created_vm):
        """Test fix is forbidden for non-admin users."""
        vm_id = created_vm["id"]
        response = client.post(f"/v1/admin/fix/{vm_id}", headers=auth_headers)
        assert response.status_code == 403


class TestReconcileEndpoint:
    """Tests for /v1/admin/reconcile endpoint."""

    def test_reconcile_all_success(self, client, admin_headers):
        """Test full reconciliation."""
        with patch('app.services.reconciler.Reconciler.reconcile_all') as mock_reconcile:
            mock_reconcile.return_value = {
                "orphan_purged": ["orphan1"],
                "ghost_purged": ["ghost1"],
                "drift_corrected": [{"name": "drift1", "old_status": "running", "new_status": "stopped"}]
            }
            
            response = client.post("/v1/admin/reconcile", headers=admin_headers)
            
            assert response.status_code == 200
            assert response.json()["status"] == "reconciliation_complete"
            assert len(response.json()["actions_taken"]["orphan_purged"]) == 1

    def test_reconcile_no_changes_needed(self, client, admin_headers):
        """Test reconcile when everything is synced."""
        with patch('app.services.reconciler.Reconciler.reconcile_all') as mock_reconcile:
            mock_reconcile.return_value = {
                "orphan_purged": [],
                "ghost_purged": [],
                "drift_corrected": []
            }
            
            response = client.post("/v1/admin/reconcile", headers=admin_headers)
            
            assert response.status_code == 200
            assert response.json()["actions_taken"]["orphan_purged"] == []

    def test_reconcile_unauthorized(self, client):
        """Test reconcile requires authentication."""
        response = client.post("/v1/admin/reconcile")
        assert response.status_code == 401

    def test_reconcile_forbidden_for_non_admin(self, client, auth_headers):
        """Test reconcile is forbidden for non-admin users."""
        response = client.post("/v1/admin/reconcile", headers=auth_headers)
        assert response.status_code == 403
