import pytest
from datetime import timedelta
from unittest.mock import patch, MagicMock
from conftest import AuthHelper
from app.core.security import create_access_token
from app.core.config import settings
from jose import jwt


pytestmark = pytest.mark.auth


class TestRegistration:
    """Tests for user registration."""

    def test_register_user_success(self, client):
        """Test successful user registration."""
        response = client.post("/v1/auth/register", json={
            "username": "newuser",
            "email": "new@example.com",
            "password": "SecurePass123",
            "full_name": "New User"
        })
        assert response.status_code == 201
        data = response.json()
        assert data["username"] == "newuser"
        assert data["email"] == "new@example.com"
        assert data["role"] == "viewer"
        assert "password" not in data

    def test_register_duplicate_username(self, client):
        """Test registering with existing username."""
        client.post("/v1/auth/register", json={
            "username": "dupuser", 
            "email": "dup@test.com", 
            "password": "TestPass123", 
            "full_name": "Dup"
        })
        
        response = client.post("/v1/auth/register", json={
            "username": "dupuser", 
            "email": "different@test.com", 
            "password": "TestPass123", 
            "full_name": "Dup2"
        })
        
        assert response.status_code == 400
        assert "already" in response.json()["detail"].lower()

    def test_register_duplicate_email(self, client):
        """Test registering with existing email."""
        client.post("/v1/auth/register", json={
            "username": "user1", 
            "email": "same@test.com", 
            "password": "TestPass123", 
            "full_name": "User1"
        })
        
        response = client.post("/v1/auth/register", json={
            "username": "user2", 
            "email": "same@test.com", 
            "password": "TestPass123", 
            "full_name": "User2"
        })
        
        assert response.status_code == 400
        assert "already" in response.json()["detail"].lower()

    def test_register_missing_fields(self, client):
        """Test registration with missing required fields."""
        response = client.post("/v1/auth/register", json={
            "username": "incomplete"
        })
        assert response.status_code == 422

    def test_register_invalid_email(self, client):
        """Test registration with invalid email format."""
        response = client.post("/v1/auth/register", json={
            "username": "bademail",
            "email": "not-an-email",
            "password": "TestPass123",
            "full_name": "Bad Email"
        })
        assert response.status_code == 422
    
    def test_register_weak_password(self, client):
        """Test registration with weak password."""
        response = client.post("/v1/auth/register", json={
            "username": "weakpass",
            "email": "weak@test.com",
            "password": "password",
            "full_name": "Weak Pass"
        })
        assert response.status_code == 422


class TestLogin:
    """Tests for user login."""

    def test_login_success(self, client):
        """Test successful login."""
        client.post("/v1/auth/register", json={
            "username": "loginuser", 
            "email": "login@test.com", 
            "password": "TestPass123", 
            "full_name": "Login"
        })
        
        response = client.post("/v1/auth/login", data={
            "username": "loginuser", 
            "password": "TestPass123"
        })
        
        assert response.status_code == 200
        assert "access_token" in response.json()
        assert response.json()["token_type"] == "bearer"

    def test_login_wrong_password(self, client):
        """Test login with incorrect password."""
        client.post("/v1/auth/register", json={
            "username": "wrongpassuser", 
            "email": "wrong@test.com", 
            "password": "TestPass123", 
            "full_name": "Wrong"
        })
        
        response = client.post("/v1/auth/login", data={
            "username": "wrongpassuser", 
            "password": "wrongpassword"
        })
        
        assert response.status_code == 401
        assert "incorrect" in response.json()["detail"].lower()

    def test_login_nonexistent_user(self, client):
        """Test login with non-existent user."""
        response = client.post("/v1/auth/login", data={
            "username": "nonexistent", 
            "password": "TestPass123"
        })
        assert response.status_code == 401

    def test_login_inactive_user(self, client, db_session):
        """Test login with deactivated user."""
        from app.models.user import User
        from app.core.security import hash_password
        
        inactive_user = User(
            username="inactiveuser",
            email="inactive@test.com",
            hashed_password=hash_password("TestPass123"),
            role="viewer",
            is_active=False
        )
        db_session.add(inactive_user)
        db_session.commit()
        
        response = client.post("/v1/auth/login", data={
            "username": "inactiveuser",
            "password": "TestPass123"
        })
        
        assert response.status_code == 403
        assert "inactive" in response.json()["detail"].lower()


class TestTokenValidation:
    """Tests for JWT token validation."""

    def test_token_contains_user_id(self, client):
        """Test that JWT token contains user ID."""
        client.post("/v1/auth/register", json={
            "username": "tokenuser", 
            "email": "token@test.com", 
            "password": "TestPass123", 
            "full_name": "Token"
        })
        
        response = client.post("/v1/auth/login", data={
            "username": "tokenuser", 
            "password": "TestPass123"
        })
        
        token = response.json()["access_token"]
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.ALGORITHM])
        
        assert "sub" in payload
        assert "role" in payload

    def test_access_with_malformed_token(self, client):
        """Test with malformed token."""
        headers = {"Authorization": "Bearer malformed.token.here"}
        response = client.get("/v1/auth/me", headers=headers)
        assert response.status_code == 401

    def test_access_with_invalid_signature(self, client):
        """Test with token signed by wrong secret."""
        fake_token = jwt.encode({"sub": "1"}, "wrong_secret", algorithm=settings.ALGORITHM)
        headers = {"Authorization": f"Bearer {fake_token}"}
        
        response = client.get("/v1/auth/me", headers=headers)
        assert response.status_code == 401

    def test_access_with_expired_token(self, client):
        """Test with expired token."""
        expired_token = create_access_token(
            data={"sub": "999"},
            expires_delta=timedelta(seconds=-1)
        )
        headers = {"Authorization": f"Bearer {expired_token}"}
        
        response = client.get("/v1/auth/me", headers=headers)
        assert response.status_code == 401

    def test_access_without_bearer_prefix(self, client):
        """Test without Bearer prefix."""
        token = create_access_token(data={"sub": "1"})
        headers = {"Authorization": token}
        
        response = client.get("/v1/auth/me", headers=headers)
        assert response.status_code == 401

    def test_access_with_empty_token(self, client):
        """Test with empty token."""
        headers = {"Authorization": "Bearer "}
        response = client.get("/v1/auth/me", headers=headers)
        assert response.status_code == 401


class TestMeEndpoint:
    """Tests for /v1/auth/me endpoint."""

    def test_me_unauthorized(self, client):
        """Test accessing /me without token."""
        response = client.get("/v1/auth/me")
        assert response.status_code == 401

    def test_me_authorized(self, client, auth_headers):
        """Test accessing /me with valid token."""
        response = client.get("/v1/auth/me", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "username" in data
        assert "email" in data
        assert "role" in data


class TestUserManagement:
    """Tests for user management endpoints (admin only)."""

    def test_list_users_success(self, client, admin_headers):
        """Test listing users as admin."""
        response = client.get("/v1/auth/users", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert "users" in data
        assert "total" in data
        assert isinstance(data["users"], list)

    def test_list_users_forbidden_for_non_admin(self, client, auth_headers):
        """Test that non-admins cannot list users."""
        response = client.get("/v1/auth/users", headers=auth_headers)
        assert response.status_code == 403

    def test_list_users_unauthorized(self, client):
        """Test listing users without authentication."""
        response = client.get("/v1/auth/users")
        assert response.status_code == 401

    def test_create_user_by_admin(self, client, admin_headers):
        """Test admin creating a new user."""
        response = client.post("/v1/auth/users", headers=admin_headers, json={
            "username": "admin_created_user",
            "email": "admin_created@test.com",
            "password": "TestPass123",
            "full_name": "Admin Created"
        })
        assert response.status_code == 201
        data = response.json()
        assert data["username"] == "admin_created_user"
        assert data["role"] == "viewer"

    def test_create_user_forbidden_for_non_admin(self, client, auth_headers):
        """Test that non-admins cannot create users via admin endpoint."""
        response = client.post("/v1/auth/users", headers=auth_headers, json={
            "username": "unauthorized_user",
            "email": "unauthorized@test.com",
            "password": "TestPass123"
        })
        assert response.status_code == 403

    def test_create_user_duplicate(self, client, admin_headers):
        """Test creating duplicate user via admin endpoint."""
        client.post("/v1/auth/users", headers=admin_headers, json={
            "username": "dup_admin_user",
            "email": "dup_admin@test.com",
            "password": "TestPass123"
        })
        
        response = client.post("/v1/auth/users", headers=admin_headers, json={
            "username": "dup_admin_user",
            "email": "different@test.com",
            "password": "TestPass123"
        })
        assert response.status_code == 400

    def test_update_user_role_success(self, client, admin_headers, db_session):
        """Test admin updating user role."""
        from app.models.user import User
        
        client.post("/v1/auth/users", headers=admin_headers, json={
            "username": "user_to_update",
            "email": "update@test.com",
            "password": "TestPass123"
        })
        
        user = db_session.query(User).filter(User.username == "user_to_update").first()
        
        response = client.patch(
            f"/v1/auth/users/{user.id}/role",
            headers=admin_headers,
            json={"role": "vm_admin"}
        )
        
        assert response.status_code == 200
        assert response.json()["role"] == "vm_admin"

    def test_update_role_forbidden_for_non_admin(self, client, auth_headers, admin_headers, db_session):
        """Test that non-admins cannot update roles."""
        from app.models.user import User
        
        client.post("/v1/auth/users", headers=admin_headers, json={
            "username": "role_test_user",
            "email": "role_test@test.com",
            "password": "TestPass123"
        })
        
        user = db_session.query(User).filter(User.username == "role_test_user").first()
        
        response = client.patch(
            f"/v1/auth/users/{user.id}/role",
            headers=auth_headers,
            json={"role": "admin"}
        )
        assert response.status_code == 403

    def test_update_role_cannot_change_own_role(self, client, admin_headers, db_session):
        """Test that admin cannot change their own role."""
        from app.models.user import User
        
        admin = db_session.query(User).filter(User.role == "admin").first()
        
        response = client.patch(
            f"/v1/auth/users/{admin.id}/role",
            headers=admin_headers,
            json={"role": "viewer"}
        )
        assert response.status_code == 400
        assert "own role" in response.json()["detail"].lower()

    def test_update_user_status_deactivate(self, client, admin_headers, db_session):
        """Test deactivating a user."""
        from app.models.user import User
        
        client.post("/v1/auth/users", headers=admin_headers, json={
            "username": "user_to_deactivate",
            "email": "deactivate@test.com",
            "password": "TestPass123"
        })
        
        user = db_session.query(User).filter(User.username == "user_to_deactivate").first()
        
        response = client.patch(
            f"/v1/auth/users/{user.id}/status",
            headers=admin_headers,
            json={"is_active": False}
        )
        
        assert response.status_code == 200
        assert response.json()["is_active"] is False

    def test_update_user_status_activate(self, client, admin_headers, db_session):
        """Test activating a deactivated user."""
        from app.models.user import User
        
        client.post("/v1/auth/users", headers=admin_headers, json={
            "username": "user_to_activate",
            "email": "activate@test.com",
            "password": "TestPass123"
        })
        
        user = db_session.query(User).filter(User.username == "user_to_activate").first()
        user.is_active = False
        db_session.commit()
        
        response = client.patch(
            f"/v1/auth/users/{user.id}/status",
            headers=admin_headers,
            json={"is_active": True}
        )
        
        assert response.status_code == 200
        assert response.json()["is_active"] is True

    def test_update_status_cannot_change_own(self, client, admin_headers, db_session):
        """Test that admin cannot deactivate themselves."""
        from app.models.user import User
        
        admin = db_session.query(User).filter(User.role == "admin").first()
        
        response = client.patch(
            f"/v1/auth/users/{admin.id}/status",
            headers=admin_headers,
            json={"is_active": False}
        )
        assert response.status_code == 400
        assert "own status" in response.json()["detail"].lower()

    def test_delete_user_success(self, client, admin_headers, db_session):
        """Test deleting a user."""
        from app.models.user import User
        
        client.post("/v1/auth/users", headers=admin_headers, json={
            "username": "user_to_delete",
            "email": "delete@test.com",
            "password": "TestPass123"
        })
        
        user = db_session.query(User).filter(User.username == "user_to_delete").first()
        user_id = user.id
        
        response = client.delete(f"/v1/auth/users/{user_id}", headers=admin_headers)
        assert response.status_code == 204
        
        deleted = db_session.query(User).filter(User.id == user_id).first()
        assert deleted is None

    def test_delete_user_forbidden_for_non_admin(self, client, auth_headers, admin_headers, db_session):
        """Test that non-admins cannot delete users."""
        from app.models.user import User
        
        client.post("/v1/auth/users", headers=admin_headers, json={
            "username": "user_delete_test",
            "email": "delete_test@test.com",
            "password": "TestPass123"
        })
        
        user = db_session.query(User).filter(User.username == "user_delete_test").first()
        
        response = client.delete(f"/v1/auth/users/{user.id}", headers=auth_headers)
        assert response.status_code == 403

    def test_delete_user_cannot_delete_self(self, client, admin_headers, db_session):
        """Test that admin cannot delete themselves."""
        from app.models.user import User
        
        admin = db_session.query(User).filter(User.role == "admin").first()
        
        response = client.delete(f"/v1/auth/users/{admin.id}", headers=admin_headers)
        assert response.status_code == 400
        assert "own account" in response.json()["detail"].lower()

    def test_delete_user_not_found(self, client, admin_headers):
        """Test deleting non-existent user."""
        response = client.delete("/v1/auth/users/99999", headers=admin_headers)
        assert response.status_code == 404
