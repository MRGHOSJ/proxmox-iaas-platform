"""
Tenant Isolation and Security Tests

This module tests:
1. Tenant isolation - users from Tenant A cannot access Tenant B's resources
2. Authorization bypass - users cannot access resources they don't own
3. IDOR vulnerabilities - users cannot access resources by ID from other tenants
4. Role-based access control enforcement
5. Invitation system security
6. Role management security
"""

import pytest
from unittest.mock import patch, MagicMock
from app.models.tenant import Tenant
from app.models.user import User
from app.models.vm import VM
from app.models.network import Network
from app.models.iam import Role, UserRole
from app.core.security import hash_password
import random
import string


def random_suffix():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))


@pytest.fixture
def tenant_a(client, db_session):
    """Get or create Tenant A."""
    existing = db_session.query(Tenant).filter(Tenant.slug.like("tenant-a-%")).first()
    if existing:
        return existing
    tenant = Tenant(name="Tenant A", slug=f"tenant-a-{random_suffix()}", is_active=True, settings="{}")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    return tenant


@pytest.fixture
def tenant_b(client, db_session):
    """Get or create Tenant B."""
    existing = db_session.query(Tenant).filter(Tenant.slug.like("tenant-b-%")).first()
    if existing:
        return existing
    tenant = Tenant(name="Tenant B", slug=f"tenant-b-{random_suffix()}", is_active=True, settings="{}")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)
    return tenant


@pytest.fixture
def user_tenant_a(client, db_session, tenant_a):
    """Create a regular user in Tenant A."""
    user = User(
        username=f"user-a-{random_suffix()}",
        email=f"usera{random_suffix()}@test.com",
        hashed_password=hash_password("Password123"),
        role="vm_operator",
        tenant_id=tenant_a.id
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    
    response = client.post("/v1/auth/login", data={"username": user.username, "password": "Password123"})
    token = response.json()["access_token"]
    return {"user": user, "headers": {"Authorization": f"Bearer {token}"}, "tenant": tenant_a}


@pytest.fixture
def user_tenant_b(client, db_session, tenant_b):
    """Create a regular user in Tenant B."""
    user = User(
        username=f"user-b-{random_suffix()}",
        email=f"userb{random_suffix()}@test.com",
        hashed_password=hash_password("Password123"),
        role="vm_operator",
        tenant_id=tenant_b.id
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    
    response = client.post("/v1/auth/login", data={"username": user.username, "password": "Password123"})
    token = response.json()["access_token"]
    return {"user": user, "headers": {"Authorization": f"Bearer {token}"}, "tenant": tenant_b}


@pytest.fixture
def admin_tenant_a(client, db_session, tenant_a):
    """Create an admin user in Tenant A."""
    user = User(
        username=f"admin-a-{random_suffix()}",
        email=f"admina{random_suffix()}@test.com",
        hashed_password=hash_password("Password123"),
        role="admin",
        tenant_id=tenant_a.id
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    
    response = client.post("/v1/auth/login", data={"username": user.username, "password": "Password123"})
    token = response.json()["access_token"]
    return {"user": user, "headers": {"Authorization": f"Bearer {token}"}, "tenant": tenant_a}


@pytest.fixture
def super_admin_user(client, db_session):
    """Create a super admin user (system-level admin)."""
    role = Role(name="super_admin", description="Super Admin", is_system=True, is_preset=True)
    db_session.add(role)
    db_session.commit()
    db_session.refresh(role)
    
    user = User(
        username=f"superadmin-{random_suffix()}",
        email=f"superadmin{random_suffix()}@test.com",
        hashed_password=hash_password("Password123"),
        role="admin",
        tenant_id=1
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    
    user_role = UserRole(user_id=user.id, tenant_id=1, role_id=role.id)
    db_session.add(user_role)
    db_session.commit()
    
    response = client.post("/v1/auth/login", data={"username": user.username, "password": "Password123"})
    token = response.json()["access_token"]
    return {"user": user, "headers": {"Authorization": f"Bearer {token}"}}


@pytest.fixture
def vm_tenant_a(client, db_session, user_tenant_a, mock_terraform_dynamic):
    """Create a VM in Tenant A."""
    response = client.post("/v1/vm/create", 
        headers=user_tenant_a["headers"], 
        json={"name": f"vm-a-{random_suffix()}", "provider": "docker", "cpu": 1, "ram": 512, "disk_size": 10}
    )
    vm = db_session.query(VM).filter(VM.name == response.json()["name"]).first()
    return response.json()


@pytest.fixture
def vm_tenant_b(client, db_session, user_tenant_b, mock_terraform_dynamic):
    """Create a VM in Tenant B."""
    response = client.post("/v1/vm/create", 
        headers=user_tenant_b["headers"], 
        json={"name": f"vm-b-{random_suffix()}", "provider": "docker", "cpu": 1, "ram": 512, "disk_size": 10}
    )
    return response.json()


@pytest.fixture
def network_tenant_a(client, db_session, user_tenant_a, mock_deploy_network_task, mock_docker_subprocess):
    """Create a network in Tenant A."""
    with patch('app.api.networks.deploy_network_task.delay') as mock_task:
        mock_task.return_value = MagicMock(id="fake-task-id")
        response = client.post("/v1/networks/", 
            headers=user_tenant_a["headers"], 
            json={"name": f"net-a-{random_suffix()}", "cidr": "172.30.0.0/16", "provider": "docker"}
        )
    return response.json()


@pytest.fixture
def network_tenant_b(client, db_session, user_tenant_b, mock_deploy_network_task, mock_docker_subprocess):
    """Create a network in Tenant B."""
    with patch('app.api.networks.deploy_network_task.delay') as mock_task:
        mock_task.return_value = MagicMock(id="fake-task-id")
        response = client.post("/v1/networks/", 
            headers=user_tenant_b["headers"], 
            json={"name": f"net-b-{random_suffix()}", "cidr": "172.31.0.0/16", "provider": "docker"}
        )
    return response.json()


# =============================================================================
# TENANT ISOLATION TESTS - VMs
# =============================================================================

class TestTenantIsolationVM:
    """Tests for VM tenant isolation."""

    def test_user_cannot_get_vm_from_another_tenant(self, client, user_tenant_a, user_tenant_b, vm_tenant_b, db_session):
        """User from Tenant A cannot get VM details from Tenant B."""
        vm_b_id = vm_tenant_b["id"]
        
        response = client.get(f"/v1/vm/{vm_b_id}", headers=user_tenant_a["headers"])
        
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_user_cannot_list_vms_from_another_tenant(self, client, user_tenant_a, user_tenant_b, vm_tenant_a, vm_tenant_b):
        """User from Tenant A cannot see Tenant B's VMs in listing."""
        response = client.get("/v1/vm/list", headers=user_tenant_a["headers"])
        
        assert response.status_code == 200
        vms = response.json()["vms"]
        vm_ids = [v["id"] for v in vms]
        
        assert vm_tenant_b["id"] not in vm_ids
        assert vm_tenant_a["id"] in vm_ids

    def test_user_cannot_update_vm_from_another_tenant(self, client, user_tenant_a, user_tenant_b, vm_tenant_b):
        """User from Tenant A cannot update Tenant B's VM."""
        vm_b_id = vm_tenant_b["id"]
        
        response = client.patch(f"/v1/vm/{vm_b_id}", 
            headers=user_tenant_a["headers"],
            json={"description": "Hacked description"}
        )
        
        assert response.status_code == 404

    def test_user_cannot_delete_vm_from_another_tenant(self, client, user_tenant_a, user_tenant_b, vm_tenant_b):
        """User from Tenant A cannot delete Tenant B's VM."""
        vm_b_id = vm_tenant_b["id"]
        
        response = client.delete(f"/v1/vm/{vm_b_id}?force=true", headers=user_tenant_a["headers"])
        
        assert response.status_code in [404, 403]

    @patch('app.services.vm.get_container_provider')
    def test_user_cannot_start_vm_from_another_tenant(self, mock_get, client, user_tenant_a, user_tenant_b, vm_tenant_b, db_session):
        """User from Tenant A cannot start Tenant B's VM."""
        vm_b = db_session.query(VM).filter(VM.id == vm_tenant_b["id"]).first()
        vm_b.status = "stopped"
        db_session.commit()
        
        mock_provider = MagicMock()
        mock_provider.start.return_value = True
        mock_get.return_value = mock_provider
        
        response = client.post(f"/v1/vm/{vm_tenant_b['id']}/start", headers=user_tenant_a["headers"])
        
        assert response.status_code == 404

    @patch('app.services.vm.get_container_provider')
    def test_user_cannot_stop_vm_from_another_tenant(self, mock_get, client, user_tenant_a, user_tenant_b, vm_tenant_b, db_session):
        """User from Tenant A cannot stop Tenant B's VM."""
        vm_b = db_session.query(VM).filter(VM.id == vm_tenant_b["id"]).first()
        vm_b.status = "running"
        db_session.commit()
        
        mock_provider = MagicMock()
        mock_provider.stop.return_value = True
        mock_get.return_value = mock_provider
        
        response = client.post(f"/v1/vm/{vm_tenant_b['id']}/stop", headers=user_tenant_a["headers"])
        
        assert response.status_code == 404

    def test_x_tenant_id_header_isolation(self, client, user_tenant_a, vm_tenant_a):
        """User cannot bypass tenant isolation by setting X-Tenant-ID header."""
        vm_a_id = vm_tenant_a["id"]
        
        headers = user_tenant_a["headers"].copy()
        headers["X-Tenant-ID"] = "999"
        
        response = client.get(f"/v1/vm/{vm_a_id}", headers=headers)
        
        assert response.status_code in [403, 404]


# =============================================================================
# TENANT ISOLATION TESTS - Networks
# =============================================================================

class TestTenantIsolationNetwork:
    """Tests for Network tenant isolation."""

    def test_user_cannot_get_network_from_another_tenant(self, client, user_tenant_a, user_tenant_b, network_tenant_b):
        """User from Tenant A cannot get network details from Tenant B."""
        net_b_id = network_tenant_b["id"]
        
        response = client.get(f"/v1/networks/{net_b_id}", headers=user_tenant_a["headers"])
        
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_user_cannot_list_networks_from_another_tenant(self, client, user_tenant_a, user_tenant_b, network_tenant_a, network_tenant_b):
        """User from Tenant A cannot see Tenant B's networks."""
        response = client.get("/v1/networks/", headers=user_tenant_a["headers"])
        
        assert response.status_code == 200
        networks = response.json()["networks"]
        net_ids = [n["id"] for n in networks]
        
        assert network_tenant_b["id"] not in net_ids

    def test_user_cannot_delete_network_from_another_tenant(self, client, user_tenant_a, user_tenant_b, network_tenant_b):
        """User from Tenant A cannot delete Tenant B's network."""
        net_b_id = network_tenant_b["id"]
        
        response = client.delete(f"/v1/networks/{net_b_id}", headers=user_tenant_a["headers"])
        
        assert response.status_code in [404, 403]


# =============================================================================
# TENANT ISOLATION TESTS - Firewalls
# =============================================================================

# AUTHORIZATION BYPASS TESTS
# =============================================================================

class TestAuthorizationBypass:
    """Tests for authorization bypass vulnerabilities."""

    def test_viewer_cannot_create_vm(self, client, db_session, tenant_a):
        """Viewer role cannot create VMs."""
        viewer = User(
            username=f"viewer-{random_suffix()}",
            email=f"viewer{random_suffix()}@test.com",
            hashed_password=hash_password("Password123"),
            role="viewer",
            tenant_id=tenant_a.id
        )
        db_session.add(viewer)
        db_session.commit()
        
        response = client.post("/v1/auth/login", data={"username": viewer.username, "password": "Password123"})
        token = response.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        
        response = client.post("/v1/vm/create", 
            headers=headers, 
            json={"name": f"unauthorized-vm-{random_suffix()}", "provider": "docker", "cpu": 1, "ram": 512, "disk_size": 10}
        )
        
        assert response.status_code == 403

    def test_viewer_cannot_delete_vm(self, client, db_session, tenant_a, user_tenant_a, mock_terraform_dynamic):
        """Viewer role cannot delete VMs."""
        viewer = User(
            username=f"viewer-del-{random_suffix()}",
            email=f"viewerdel{random_suffix()}@test.com",
            hashed_password=hash_password("Password123"),
            role="viewer",
            tenant_id=tenant_a.id
        )
        db_session.add(viewer)
        db_session.commit()
        
        response = client.post("/v1/auth/login", data={"username": viewer.username, "password": "Password123"})
        token = response.json()["access_token"]
        viewer_headers = {"Authorization": f"Bearer {token}"}
        
        response = client.delete(f"/v1/vm/{user_tenant_a['user'].id}?force=true", headers=viewer_headers)
        
        assert response.status_code == 403

    def test_regular_user_cannot_access_tenant_admin_endpoints(self, client, db_session, tenant_a, user_tenant_a):
        """Regular user cannot access tenant admin endpoints."""
        response = client.get("/v1/tenants", headers=user_tenant_a["headers"])
        
        assert response.status_code == 403

    def test_regular_user_cannot_create_tenant(self, client, db_session, tenant_a, user_tenant_a):
        """Regular user cannot create new tenants."""
        response = client.post("/v1/tenants", 
            headers=user_tenant_a["headers"],
            json={"name": "Unauthorized Tenant", "slug": "unauthorized-tenant"}
        )
        
        assert response.status_code == 403


# =============================================================================
# IDOR VULNERABILITY TESTS
# =============================================================================

class TestIDORVulnerability:
    """Tests for Insecure Direct Object Reference vulnerabilities."""

    def test_idor_vm_by_id(self, client, user_tenant_a, user_tenant_b, vm_tenant_b):
        """Test IDOR on VM by direct ID access."""
        response = client.get(f"/v1/vm/{vm_tenant_b['id']}", headers=user_tenant_a["headers"])
        assert response.status_code == 404

    def test_idor_network_by_id(self, client, user_tenant_a, user_tenant_b, network_tenant_b):
        """Test IDOR on Network by direct ID access."""
        response = client.get(f"/v1/networks/{network_tenant_b['id']}", headers=user_tenant_a["headers"])
        assert response.status_code == 404

    def test_idor_vm_logs(self, client, user_tenant_a, user_tenant_b, vm_tenant_b):
        """Test IDOR on VM logs."""
        response = client.get(f"/v1/vm/{vm_tenant_b['id']}/logs", headers=user_tenant_a["headers"])
        assert response.status_code == 404


# =============================================================================
# SUPER ADMIN TESTS
# =============================================================================

class TestSuperAdminAccess:
    """Tests for super admin access patterns."""

    def test_super_admin_can_access_any_tenant_vm(self, client, super_admin_user, user_tenant_b, vm_tenant_b, db_session):
        """Super admin can access VMs from any tenant."""
        vm_b = db_session.query(VM).filter(VM.id == vm_tenant_b["id"]).first()
        
        response = client.get(f"/v1/vm/{vm_b.id}", headers=super_admin_user["headers"])
        
        assert response.status_code == 200
        assert response.json()["id"] == vm_b.id

    def test_super_admin_can_list_all_tenants(self, client, super_admin_user):
        """Super admin can list all tenants."""
        response = client.get("/v1/tenants", headers=super_admin_user["headers"])
        
        assert response.status_code == 200
        tenants = response.json()
        assert len(tenants) >= 2

    def test_non_super_admin_cannot_list_tenants(self, client, user_tenant_a):
        """Non-super-admin cannot list tenants."""
        response = client.get("/v1/tenants", headers=user_tenant_a["headers"])
        
        assert response.status_code == 403


# =============================================================================
# ROLE MANAGEMENT SECURITY TESTS
# =============================================================================

class TestRoleManagementSecurity:
    """Tests for role management security."""

    def test_cannot_assign_system_role(self, client, admin_tenant_a, db_session, tenant_a):
        """Cannot assign system roles to users."""
        system_role = Role(name="super_admin", description="System", is_system=True, is_preset=True)
        db_session.add(system_role)
        db_session.commit()
        db_session.refresh(system_role)
        
        regular_user = User(
            username=f"role-test-{random_suffix()}",
            email=f"roletest{random_suffix()}@test.com",
            hashed_password=hash_password("Password123"),
            role="viewer",
            tenant_id=tenant_a.id
        )
        db_session.add(regular_user)
        db_session.commit()
        db_session.refresh(regular_user)
        
        response = client.post(f"/v1/tenants/{tenant_a.id}/users/{regular_user.id}/roles/{system_role.id}",
            headers=admin_tenant_a["headers"]
        )
        
        assert response.status_code == 403

    def test_cannot_assign_role_from_different_tenant(self, client, admin_tenant_a, db_session, tenant_a, tenant_b):
        """Cannot assign a role that belongs to a different tenant."""
        role_tenant_b = Role(name=f"role-b-{random_suffix()}", description="Tenant B role", tenant_id=tenant_b.id)
        db_session.add(role_tenant_b)
        db_session.commit()
        db_session.refresh(role_tenant_b)
        
        regular_user = User(
            username=f"cross-tenant-{random_suffix()}",
            email=f"crosstenant{random_suffix()}@test.com",
            hashed_password=hash_password("Password123"),
            role="viewer",
            tenant_id=tenant_a.id
        )
        db_session.add(regular_user)
        db_session.commit()
        db_session.refresh(regular_user)
        
        response = client.post(f"/v1/tenants/{tenant_a.id}/users/{regular_user.id}/roles/{role_tenant_b.id}",
            headers=admin_tenant_a["headers"]
        )
        
        assert response.status_code == 400
        assert "different tenant" in response.json()["detail"].lower() or "not belong" in response.json()["detail"].lower()


# =============================================================================
# INVITATION SYSTEM TESTS
# =============================================================================

class TestInvitationSecurity:
    """Tests for invitation system security."""

    def test_cannot_accept_expired_invitation(self, client, db_session, tenant_a):
        """Cannot accept an expired invitation."""
        from datetime import datetime, timedelta, timezone
        from app.models.invitation import Invitation
        
        invitation = Invitation(
            email=f"expired{random_suffix()}@test.com",
            tenant_id=tenant_a.id,
            token="expired-token-123",
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
            invited_by=1
        )
        db_session.add(invitation)
        db_session.commit()
        
        response = client.post("/v1/auth/accept-invite",
            json={"token": "expired-token-123", "username": "newuser", "password": "Password123", "full_name": "New User"}
        )
        
        assert response.status_code == 400
        assert "expired" in response.json()["detail"].lower()

    def test_cannot_accept_used_invitation(self, client, db_session, tenant_a):
        """Cannot accept an already used invitation."""
        from datetime import datetime, timedelta, timezone
        from app.models.invitation import Invitation
        
        invitation = Invitation(
            email=f"used{random_suffix()}@test.com",
            tenant_id=tenant_a.id,
            token="used-token-456",
            expires_at=datetime.now(timezone.utc) + timedelta(days=7),
            is_used=True,
            invited_by=1
        )
        db_session.add(invitation)
        db_session.commit()
        
        response = client.post("/v1/auth/accept-invite",
            json={"token": "used-token-456", "username": "newuser", "password": "Password123", "full_name": "New User"}
        )
        
        assert response.status_code == 404

    def test_only_tenant_admin_can_create_invitation(self, client, user_tenant_a):
        """Only tenant admins can create invitations."""
        response = client.post("/v1/auth/invite",
            headers=user_tenant_a["headers"],
            json={"email": f"invite{random_suffix()}@test.com", "role_id": 1}
        )
        
        assert response.status_code == 403


# =============================================================================
# INPUT VALIDATION TESTS
# =============================================================================

class TestInputValidation:
    """Tests for input validation security."""

    def test_sql_injection_in_vm_name(self, client, user_tenant_a, mock_terraform_dynamic):
        """Test SQL injection in VM name."""
        response = client.post("/v1/vm/create", 
            headers=user_tenant_a["headers"], 
            json={"name": "test' OR '1'='1", "provider": "docker", "cpu": 1, "ram": 512, "disk_size": 10}
        )
        
        assert response.status_code == 201

    def test_invalid_pagination_values(self, client, user_tenant_a):
        """Test invalid pagination values."""
        response = client.get("/v1/vm/list?limit=-1", headers=user_tenant_a["headers"])
        assert response.status_code == 422
        
        response = client.get("/v1/vm/list?offset=-1", headers=user_tenant_a["headers"])
        assert response.status_code == 422

    def test_excessive_pagination_limit(self, client, user_tenant_a):
        """Test excessive pagination limit is capped."""
        response = client.get("/v1/vm/list?limit=10000", headers=user_tenant_a["headers"])
        assert response.status_code == 200
        data = response.json()
        assert data["limit"] <= 1000


# =============================================================================
# AUTHENTICATION TESTS
# =============================================================================

class TestAuthenticationSecurity:
    """Tests for authentication security."""

    def test_login_rate_limiting(self, client):
        """Test that login is rate limited."""
        for i in range(6):
            response = client.post("/v1/auth/login", data={"username": "nonexistent", "password": "wrong"})
        
        assert response.status_code == 429

    def test_logout_invalidates_token(self, client, user_tenant_a):
        """Test that logout blacklists the token."""
        response = client.post("/v1/auth/logout", headers=user_tenant_a["headers"])
        assert response.status_code == 204
        
        response = client.get("/v1/auth/me", headers=user_tenant_a["headers"])
        assert response.status_code == 401
