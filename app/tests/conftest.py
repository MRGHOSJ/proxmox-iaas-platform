import pytest
import random
import string
import os

os.environ["TESTING"] = "true"
os.environ["DATABASE_URL"] = "sqlite:///./test.db"

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.core.database import Base, get_db
from app.main import app
from app.models.user import User
from app.models.vm import VM
from app.models.network import Network
from app.core.rate_limit import rate_limiter


SQLALCHEMY_TEST_DATABASE_URL = "sqlite:///./test.db"

test_engine = create_engine(
    SQLALCHEMY_TEST_DATABASE_URL,
    connect_args={"check_same_thread": False}
)

test_session_factory = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

# Creates and Drops tables for every test
@pytest.fixture(scope="function", autouse=True)
def setup_db():
    rate_limiter.reset()
    # Drop all tables first to clear any seeded data from lifespan
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)
    rate_limiter.reset()


@pytest.fixture(scope="function", autouse=True)
def mock_sqlite_compatibility():
    """
    Auto-mock PostgreSQL advisory locks for SQLite testing.
    This fixture runs automatically for every test.
    """
    with patch('app.api.vm.acquire_vm_name_lock') as mock_lock, \
         patch('app.api.networks._acquire_network_modification_lock') as mock_net_lock, \
         patch('app.services.ipam._acquire_network_lock') as mock_ipam_lock, \
         patch('app.providers.docker.DockerContainerProvider.start') as mock_docker_start, \
         patch('app.providers.docker.DockerContainerProvider.stop') as mock_docker_stop, \
         patch('app.providers.docker.DockerContainerProvider.restart') as mock_docker_restart, \
         patch('app.providers.docker.DockerContainerProvider.get_status') as mock_docker_status, \
         patch('app.providers.docker.DockerContainerProvider.remove') as mock_docker_remove, \
         patch('app.providers.docker.DockerContainerProvider.get_logs') as mock_docker_logs:
        mock_lock.return_value = True
        mock_net_lock.return_value = True
        mock_ipam_lock.return_value = True
        
        mock_docker_start.return_value = True
        mock_docker_stop.return_value = True
        mock_docker_restart.return_value = True
        
        from app.providers.base import ContainerInfo, ContainerLogs
        mock_docker_status.return_value = ContainerInfo(
            name="test-vm",
            status="running",
            ip_address="172.20.0.10"
        )
        mock_docker_remove.return_value = True
        mock_docker_logs.return_value = ContainerLogs(
            container_name="test-vm",
            logs="test log output",
            line_count=1
        )
        
        yield

@pytest.fixture(scope="function")
def client():
    def override_get_db():
        try:
            db = test_session_factory()
            yield db
        finally:
            db.close()
    
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def db_session():
    """Provide a database session for direct DB operations in tests."""
    db = test_session_factory()
    try:
        yield db
    finally:
        db.close()


# HELPERS

class HelperUtils:
    @staticmethod
    def random_suffix():
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))

class AuthHelper:
    @staticmethod
    def create_authenticated_user(client, db_session=None, role="vm_operator"):
        suffix = HelperUtils.random_suffix()
        username = f"test_{suffix}"
        
        response = client.post("/v1/auth/register", json={
            "username": username, 
            "email": f"{username}@test.com", 
            "password": "TestPass123", 
            "full_name": "Test User"
        })
        
        if response.status_code != 201:
            raise Exception(f"Failed to register user: {response.text}")
        
        if db_session:
            user = db_session.query(User).filter(User.username == username).first()
            if user:
                user.role = role
                db_session.commit()
        
        response = client.post("/v1/auth/login", data={"username": username, "password": "TestPass123"})
        token = response.json()["access_token"]
        return {"Authorization": f"Bearer {token}"}
    
    @staticmethod
    def create_admin_user(client):
        return AuthHelper.create_authenticated_user(client, role="admin")
    
    @staticmethod
    def create_vm_operator_user(client):
        return AuthHelper.create_authenticated_user(client, role="vm_operator")
    
    @staticmethod
    def create_network_admin_user(client):
        return AuthHelper.create_authenticated_user(client, role="network_admin")

class VMHelper:
    DEFAULT_DOCKER_VM = {
        "provider": "docker",
        "cpu": 2,
        "ram": 2048,
        "disk_size": 20
    }

    @staticmethod
    def create_vm(client, headers, name, **kwargs):
        vm_data = {"name": name, **VMHelper.DEFAULT_DOCKER_VM, **kwargs}
        return client.post("/v1/vm/create", headers=headers, json=vm_data)

    @staticmethod
    def stop_vm(client, headers, vm_id):
        return client.post(f"/v1/vm/{vm_id}/stop", headers=headers)
    
    @staticmethod
    def start_vm(client, headers, vm_id):
        return client.post(f"/v1/vm/{vm_id}/start", headers=headers)

    @staticmethod
    def restart_vm(client, headers, vm_id):
        return client.post(f"/v1/vm/{vm_id}/restart", headers=headers)

    @staticmethod
    def delete_vm(client, headers, vm_id, force=False):
        return client.delete(f"/v1/vm/{vm_id}?force={force}", headers=headers)


class NetworkHelper:
    DEFAULT_NETWORK = {
        "cidr": "172.20.0.0/16",
        "provider": "docker"
    }

    @staticmethod
    def create_network(client, headers, name, **kwargs):
        net_data = {"name": name, **NetworkHelper.DEFAULT_NETWORK, **kwargs}
        return client.post("/v1/networks/", headers=headers, json=net_data)

    @staticmethod
    def get_network(client, headers, network_id):
        return client.get(f"/v1/networks/{network_id}", headers=headers)

    @staticmethod
    def list_networks(client, headers):
        return client.get("/v1/networks/", headers=headers)

    @staticmethod
    def delete_network(client, headers, network_id):
        return client.delete(f"/v1/networks/{network_id}", headers=headers)


# MOCKS


@pytest.fixture
def mock_terraform_dynamic():
    """
    Mocks Terraform with RANDOM ports to allow parallel testing.
    Returns a 'success' state so the API logic proceeds.
    """
    with patch('app.services.terraform.run_terraform_job') as mock:
        random_port = random.randint(8000, 9000)
        mock.return_value = {
            "status": "success", 
            "outputs": {"port": random_port, "ip": "127.0.0.1"}
        }
        yield mock

@pytest.fixture
def mock_docker_commands():
    """Mocks subprocess.run for docker commands (start/stop/restart)."""
    with patch('subprocess.run') as mock:
        mock.return_value = MagicMock(returncode=0, stdout="", stderr="")
        yield mock


@pytest.fixture
def mock_deploy_network_task():
    """Mocks Celery deploy_network_task.delay()."""
    with patch('app.api.networks.deploy_network_task.delay') as mock:
        mock.return_value = MagicMock(id="fake-task-id")
        yield mock


@pytest.fixture
def mock_destroy_network_task():
    """Mocks Celery destroy_network_task.delay()."""
    with patch('app.api.networks.destroy_network_task.delay') as mock:
        mock.return_value = MagicMock(id="fake-task-id")
        yield mock


@pytest.fixture
def mock_docker_subprocess():
    """Mocks subprocess.run for docker network commands (IPAM)."""
    with patch('subprocess.run') as mock:
        mock.return_value = MagicMock(
            returncode=0, 
            stdout="[]", 
            stderr=""
        )
        yield mock


@pytest.fixture
def mock_advisory_lock():
    """
    Mocks PostgreSQL advisory locks for testing.
    SQLite doesn't support advisory locks, so we mock them.
    """
    with patch('app.api.vm.acquire_vm_name_lock') as mock_lock, \
         patch('app.api.networks._acquire_network_modification_lock') as mock_net_lock, \
         patch('app.services.ipam._acquire_network_lock') as mock_ipam_lock:
        mock_lock.return_value = True
        mock_net_lock.return_value = True
        mock_ipam_lock.return_value = True
        yield


@pytest.fixture
def mock_docker_ipam():
    """
    Mocks Docker IPAM operations for testing.
    """
    with patch('app.providers.docker.DockerIPAMProvider.check_ip_is_free') as mock_check, \
         patch('app.providers.docker.DockerIPAMProvider.get_all_subnets') as mock_subnets, \
         patch('app.providers.docker.DockerIPAMProvider.get_used_ips') as mock_used, \
         patch('app.providers.docker.DockerIPAMProvider.wait_for_network') as mock_wait, \
         patch('app.providers.docker.DockerIPAMProvider.validate_cidr_overlap') as mock_validate:
        mock_check.return_value = True
        mock_subnets.return_value = []
        mock_used.return_value = []
        mock_wait.return_value = True
        mock_validate.return_value = (True, None)
        yield


# AUTOMATED FIXTURES


@pytest.fixture(scope="function")
def auth_headers(client, db_session):
    """Auto-login as vm_operator and return headers."""
    return AuthHelper.create_authenticated_user(client, db_session=db_session, role="vm_operator")


@pytest.fixture(scope="function")
def admin_headers(client, db_session):
    """Auto-login as admin and return headers."""
    return AuthHelper.create_authenticated_user(client, db_session=db_session, role="admin")


@pytest.fixture(scope="function")
def network_admin_headers(client, db_session):
    """Auto-login as network_admin and return headers."""
    return AuthHelper.create_authenticated_user(client, db_session=db_session, role="network_admin")


@pytest.fixture(scope="function")
def viewer_headers(client, db_session):
    """Auto-login as viewer (read-only) and return headers."""
    return AuthHelper.create_authenticated_user(client, db_session=db_session, role="viewer")


@pytest.fixture(scope="function")
def created_vm(client, auth_headers, mock_terraform_dynamic, db_session):
    """
    AUTO-CREATE and AUTO-DELETE VM.
    Creates a VM, yields it, then attempts to delete it after test.
    """
    name = f"auto-vm-{HelperUtils.random_suffix()}"
    response = VMHelper.create_vm(client, auth_headers, name)
    
    if response.status_code != 201:
        raise Exception(f"Fixture failed to create VM: {response.text}")
    
    vm_data = response.json()
    vm_id = vm_data["id"]

    # 1. Yield to test
    yield vm_data

    # 2. Cleanup (Always runs)
    print(f"\n[Cleanup] Destroying VM {vm_id} ({name})...")
    
    with patch('app.services.vm.destroy_terraform_job') as mock_destroy:
        mock_destroy.return_value = {"status": "destroyed"}
        
        # Attempt delete
        del_resp = VMHelper.delete_vm(client, auth_headers, vm_id, force=True)
        
        if del_resp.status_code not in [204, 404]:
            print(f"[Cleanup Warning] Failed to delete VM {vm_id}: {del_resp.text}")


@pytest.fixture(scope="function")
def created_network(client, network_admin_headers, mock_deploy_network_task, mock_docker_subprocess, db_session):
    """
    AUTO-CREATE and AUTO-DELETE Network.
    Creates a Network, yields it, then attempts to delete it after test.
    """
    suffix = HelperUtils.random_suffix()
    name = f"auto-net-{suffix}"
    response = NetworkHelper.create_network(client, network_admin_headers, name)
    
    if response.status_code != 201:
        raise Exception(f"Fixture failed to create Network: {response.text}")
    
    net_data = response.json()
    net_id = net_data["id"]

    yield net_data

    print(f"\n[Cleanup] Destroying Network {net_id} ({name})...")
    
    with patch('app.api.networks.destroy_network_task.delay') as mock_destroy:
        mock_destroy.return_value = MagicMock(id="fake-task-id")
        
        del_resp = NetworkHelper.delete_network(client, network_admin_headers, net_id)
        
        if del_resp.status_code not in [202, 404]:
            print(f"[Cleanup Warning] Failed to delete Network {net_id}: {del_resp.text}")


# PYTEST CONFIGURATION


def pytest_configure(config):
    markers = [
        "unit: Unit tests",
        "integration: Integration tests",
        "auth: Authentication tests",
        "vm: VM CRUD tests",
        "network: Network CRUD tests",
        "ipam: IPAM service tests",
        "admin: Admin API tests",
        "reconciler: Reconciler service tests",
        "security: Security module tests",
    ]
    for marker in markers:
        config.addinivalue_line("markers", marker)