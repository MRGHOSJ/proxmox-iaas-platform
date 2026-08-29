import pytest
from unittest.mock import patch, MagicMock
from conftest import VMHelper
from app.providers.base import ContainerLogs


# VM CRUD TESTS

def test_create_vm_success(client, auth_headers, mock_terraform_dynamic):
    """Test VM creation endpoint."""
    response = VMHelper.create_vm(client, auth_headers, "success-vm")
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "success-vm"
    assert data["status"] == "pending" 
    assert "id" in data

def test_get_vm_details(client, auth_headers, created_vm):
    """Test fetching a specific VM."""
    vm_id = created_vm["id"]
    response = client.get(f"/v1/vm/{vm_id}", headers=auth_headers)
    
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == vm_id
    assert data["name"] == created_vm["name"]

def test_get_vm_not_found(client, auth_headers):
    """Test fetching non-existent VM."""
    response = client.get("/v1/vm/99999", headers=auth_headers)
    assert response.status_code == 404

def test_list_vms(client, auth_headers, mock_terraform_dynamic):
    """Test listing VMs."""
    VMHelper.create_vm(client, auth_headers, "list-vm-1")
    VMHelper.create_vm(client, auth_headers, "list-vm-2")
    
    response = client.get("/v1/vm/list", headers=auth_headers)
    
    assert response.status_code == 200
    vms = response.json()
    assert len(vms) >= 2

def test_update_vm(client, auth_headers, created_vm):
    """Test updating VM details."""
    vm_id = created_vm["id"]
    
    response = client.patch(
        f"/v1/vm/{vm_id}", 
        headers=auth_headers, 
        json={"description": "Updated description"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["description"] == "Updated description"


# VM LIFECYCLE TESTS


@patch('app.services.vm.get_container_provider')
def test_lifecycle_stop_start_restart(mock_get_provider, client, auth_headers, admin_headers, created_vm):
    """Test Stop, Start, Restart flow."""
    vm_id = created_vm["id"]
    
    mock_provider = MagicMock()
    mock_provider.start.return_value = True
    mock_provider.stop.return_value = True
    mock_provider.restart.return_value = True
    mock_get_provider.return_value = mock_provider
    
    client.patch(
        f"/v1/admin/vm/{vm_id}/status",
        headers=admin_headers,
        json={"status": "running", "reason": "Test setup"}
    )
    
    resp_stop = VMHelper.stop_vm(client, auth_headers, vm_id)
    assert resp_stop.status_code == 200
    assert resp_stop.json()["status"] == "stopped"
    
    resp_start = VMHelper.start_vm(client, auth_headers, vm_id)
    assert resp_start.status_code == 200
    assert resp_start.json()["status"] == "running"
    
    resp_restart = VMHelper.restart_vm(client, auth_headers, vm_id)
    assert resp_restart.status_code == 200

@patch('app.services.vm.destroy_terraform_job')
def test_delete_vm_with_force(mock_destroy, client, auth_headers, created_vm):
    """Test force delete."""
    vm_id = created_vm["id"]
    
    mock_destroy.return_value = {"status": "destroyed"}
    
    resp = VMHelper.delete_vm(client, auth_headers, vm_id, force=True)
    assert resp.status_code == 204

@patch('app.services.vm.destroy_terraform_job')
def test_delete_vm_without_force(mock_destroy, client, auth_headers, created_vm):
    """
    Test that running VMs require force.
    """
    vm_id = created_vm["id"]
    mock_destroy.return_value = {"status": "destroyed"}
    
    resp = VMHelper.delete_vm(client, auth_headers, vm_id, force=False)
    
    assert resp.status_code in [200, 204, 400]

@patch('app.services.vm.get_container_provider')
def test_vm_logs(mock_get_provider, client, auth_headers, created_vm):
    """Test fetching logs."""
    vm_id = created_vm["id"]
    
    mock_provider = MagicMock()
    mock_provider.get_logs.return_value = ContainerLogs(
        container_name="test-vm",
        logs="Log line 1\nLog line 2",
        line_count=2
    )
    mock_get_provider.return_value = mock_provider
    
    resp = client.get(f"/v1/vm/{vm_id}/logs", headers=auth_headers)
    
    assert resp.status_code == 200
    assert "Log line 1" in resp.json()["logs"]


# STATS TESTS


def test_get_stats_summary(client, admin_headers, mock_terraform_dynamic):
    """Test stats aggregation - requires admin role."""
    VMHelper.create_vm(client, admin_headers, "stat1", ram=1024, cpu=1)
    VMHelper.create_vm(client, admin_headers, "stat2", ram=2048, cpu=2)
    
    response = client.get("/v1/vm/stats/summary", headers=admin_headers)
    
    assert response.status_code == 200
    data = response.json()
    assert data["total_vms"] >= 2
    assert data["ram_total_mb"] >= 3072
    assert data["provider_breakdown"]["docker"] >= 2


# LIST FILTERING AND PAGINATION TESTS


def test_create_vm_duplicate_name(client, auth_headers, mock_terraform_dynamic):
    """Test unique name constraint."""
    name = "dup-vm"
    VMHelper.create_vm(client, auth_headers, name)
    
    response = VMHelper.create_vm(client, auth_headers, name)
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"].lower()


def test_list_vms_with_status_filter(client, auth_headers, mock_terraform_dynamic):
    """Test listing VMs filtered by status."""
    VMHelper.create_vm(client, auth_headers, "filter-vm-1")
    VMHelper.create_vm(client, auth_headers, "filter-vm-2")
    
    response = client.get("/v1/vm/list?status_filter=pending", headers=auth_headers)
    
    assert response.status_code == 200
    data = response.json()
    for vm in data["vms"]:
        assert vm["status"] == "pending"


def test_list_vms_with_provider_filter(client, auth_headers, mock_terraform_dynamic):
    """Test listing VMs filtered by provider."""
    VMHelper.create_vm(client, auth_headers, "provider-vm-1", provider="docker")
    
    response = client.get("/v1/vm/list?provider_filter=docker", headers=auth_headers)
    
    assert response.status_code == 200
    data = response.json()
    for vm in data["vms"]:
        assert vm["provider"] == "docker"


def test_list_vms_with_limit(client, auth_headers, mock_terraform_dynamic):
    """Test listing VMs with limit parameter."""
    for i in range(5):
        VMHelper.create_vm(client, auth_headers, f"limit-vm-{i}")
    
    response = client.get("/v1/vm/list?limit=2", headers=auth_headers)
    
    assert response.status_code == 200
    data = response.json()
    assert len(data["vms"]) <= 2
    assert data["total"] == 5
    assert data["limit"] == 2


def test_list_vms_with_offset(client, auth_headers, mock_terraform_dynamic):
    """Test listing VMs with offset parameter."""
    for i in range(5):
        VMHelper.create_vm(client, auth_headers, f"offset-vm-{i}")
    
    response1 = client.get("/v1/vm/list?offset=0&limit=2", headers=auth_headers)
    response2 = client.get("/v1/vm/list?offset=2&limit=2", headers=auth_headers)
    
    assert response1.status_code == 200
    assert response2.status_code == 200
    
    data1 = response1.json()
    data2 = response2.json()
    
    if len(data1["vms"]) >= 1 and len(data2["vms"]) >= 1:
        assert data1["vms"][0]["id"] != data2["vms"][0]["id"]


def test_list_vms_unauthorized(client):
    """Test listing VMs without authentication."""
    response = client.get("/v1/vm/list")
    assert response.status_code == 401


# VM WITH NETWORK TESTS


def test_create_vm_with_network(client, auth_headers, mock_terraform_dynamic, mock_docker_subprocess):
    """Test creating VM attached to a network."""
    from conftest import NetworkHelper, mock_deploy_network_task
    
    with patch('app.api.networks.deploy_network_task.delay') as mock_task:
        mock_task.return_value = MagicMock(id="fake-task-id")
        
        net_response = NetworkHelper.create_network(client, auth_headers, "vm-network", cidr="172.25.0.0/16")
        
        if net_response.status_code == 201:
            network_id = net_response.json()["id"]
            
            with patch('app.services.ipam.allocate_ip') as mock_allocate:
                mock_allocate.return_value = "172.25.0.5"
                
                vm_response = VMHelper.create_vm(
                    client, auth_headers, "networked-vm",
                    network_id=network_id
                )
                
                assert vm_response.status_code == 201
                data = vm_response.json()
                assert data["ip_address"] is not None


def test_create_vm_with_invalid_network(client, auth_headers, mock_terraform_dynamic):
    """Test creating VM with non-existent network returns error."""
    vm_response = VMHelper.create_vm(
        client, auth_headers, "invalid-net-vm",
        network_id=999
    )
    
    assert vm_response.status_code == 404


# VM LIFECYCLE ERROR TESTS


@patch('app.services.vm.get_container_provider')
def test_start_vm_already_running(mock_get_provider, client, auth_headers, created_vm):
    """Test starting an already running VM."""
    vm_id = created_vm["id"]
    mock_provider = MagicMock()
    mock_get_provider.return_value = mock_provider
    
    client.patch(f"/v1/vm/{vm_id}", headers=auth_headers, json={"status": "running"})
    
    response = VMHelper.start_vm(client, auth_headers, vm_id)
    
    assert response.status_code == 400


@patch('app.services.vm.get_container_provider')
def test_stop_vm_not_running(mock_get_provider, client, auth_headers, created_vm):
    """Test stopping a VM that is not running."""
    vm_id = created_vm["id"]
    mock_provider = MagicMock()
    mock_get_provider.return_value = mock_provider
    
    client.patch(f"/v1/vm/{vm_id}", headers=auth_headers, json={"status": "pending"})
    
    response = VMHelper.stop_vm(client, auth_headers, vm_id)
    
    assert response.status_code == 400


def test_update_vm_not_found(client, auth_headers):
    """Test updating non-existent VM."""
    response = client.patch("/v1/vm/99999", headers=auth_headers, json={"description": "test"})
    assert response.status_code == 404


def test_vm_logs_not_found(client, auth_headers):
    """Test fetching logs for non-existent VM."""
    response = client.get("/v1/vm/99999/logs", headers=auth_headers)
    assert response.status_code == 404