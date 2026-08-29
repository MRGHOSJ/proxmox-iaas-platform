import pytest
from unittest.mock import patch, MagicMock
from conftest import NetworkHelper


pytestmark = pytest.mark.network


def test_create_network_success(client, network_admin_headers, mock_deploy_network_task, mock_docker_subprocess):
    """Test network creation endpoint."""
    response = NetworkHelper.create_network(client, network_admin_headers, "test-net-1")
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "test-net-1"
    assert data["status"] == "pending"
    assert data["cidr"] == "172.20.0.0/16"
    assert "id" in data


def test_create_network_duplicate_name(client, network_admin_headers, mock_deploy_network_task, mock_docker_subprocess):
    """Test unique name constraint."""
    name = "dup-net"
    NetworkHelper.create_network(client, network_admin_headers, name, cidr="172.20.0.0/16")
    
    response = NetworkHelper.create_network(client, network_admin_headers, name, cidr="172.21.0.0/16")
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"].lower()


def test_create_network_invalid_cidr(client, network_admin_headers, mock_deploy_network_task):
    """Test invalid CIDR format."""
    response = NetworkHelper.create_network(
        client, network_admin_headers, "invalid-cidr-net", cidr="invalid-cidr"
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any("cidr" in str(err).lower() for err in detail)


def test_create_network_with_gateway(client, network_admin_headers, mock_deploy_network_task, mock_docker_subprocess):
    """Test network creation with custom gateway."""
    response = NetworkHelper.create_network(
        client, network_admin_headers, "gateway-net",
        cidr="172.21.0.0/16",
        gateway="172.21.0.1"
    )
    assert response.status_code == 201
    data = response.json()
    assert data["gateway"] == "172.21.0.1"


def test_list_networks(client, network_admin_headers, mock_deploy_network_task, mock_docker_subprocess):
    """Test listing networks."""
    NetworkHelper.create_network(client, network_admin_headers, "list-net-1", cidr="172.30.0.0/16")
    NetworkHelper.create_network(client, network_admin_headers, "list-net-2", cidr="172.31.0.0/16")
    
    response = NetworkHelper.list_networks(client, network_admin_headers)
    
    assert response.status_code == 200
    networks = response.json()
    assert len(networks) >= 2


def test_get_network_details(client, network_admin_headers, created_network):
    """Test fetching a specific network."""
    net_id = created_network["id"]
    response = NetworkHelper.get_network(client, network_admin_headers, net_id)
    
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == net_id
    assert data["name"] == created_network["name"]


def test_get_network_not_found(client, network_admin_headers):
    """Test fetching non-existent network."""
    response = NetworkHelper.get_network(client, network_admin_headers, 99999)
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_delete_network_success(client, network_admin_headers, mock_deploy_network_task, mock_destroy_network_task, mock_docker_subprocess):
    """Test deleting a network with no attached VMs."""
    response = NetworkHelper.create_network(client, network_admin_headers, "delete-net", cidr="172.40.0.0/16")
    net_id = response.json()["id"]
    
    del_response = NetworkHelper.delete_network(client, network_admin_headers, net_id)
    
    assert del_response.status_code == 202
    assert "deletion initiated" in del_response.json()["message"].lower()


def test_delete_network_not_found(client, network_admin_headers):
    """Test deleting non-existent network."""
    response = NetworkHelper.delete_network(client, network_admin_headers, 99999)
    assert response.status_code == 404


def test_delete_network_unauthorized(client):
    """Test deleting network without authentication."""
    response = client.delete("/v1/networks/1")
    assert response.status_code == 401


def test_create_network_unauthorized(client):
    """Test creating network without authentication."""
    response = client.post("/v1/networks/", json={
        "name": "unauth-net",
        "cidr": "172.50.0.0/16"
    })
    assert response.status_code == 401


def test_list_networks_unauthorized(client):
    """Test listing networks without authentication."""
    response = client.get("/v1/networks/")
    assert response.status_code == 401


def test_create_network_forbidden_for_non_network_admin(client, auth_headers):
    """Test that non-network-admin users cannot create networks."""
    response = NetworkHelper.create_network(client, auth_headers, "forbidden-net")
    assert response.status_code == 403


def test_delete_network_forbidden_for_non_network_admin(client, auth_headers, created_network):
    """Test that non-network-admin users cannot delete networks."""
    net_id = created_network["id"]
    response = NetworkHelper.delete_network(client, auth_headers, net_id)
    assert response.status_code == 403
