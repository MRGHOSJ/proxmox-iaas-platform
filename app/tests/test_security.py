import pytest
from datetime import timedelta
from unittest.mock import MagicMock
from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    verify_token,
)
from jose import jwt
from app.core.config import settings


pytestmark = pytest.mark.unit


class TestPasswordHashing:
    """Tests for password hashing functions."""

    def test_hash_password_returns_string(self):
        """Hash password returns a string."""
        result = hash_password("testpassword")
        
        assert isinstance(result, str)
        assert len(result) > 0

    def test_hash_password_is_bcrypt_format(self):
        """Hash is in bcrypt format ($2b$)."""
        result = hash_password("testpassword")
        
        assert result.startswith("$2b$")

    def test_hash_password_different_salts(self):
        """Same password produces different hashes."""
        hash1 = hash_password("testpassword")
        hash2 = hash_password("testpassword")
        
        assert hash1 != hash2

    def test_hash_password_different_passwords(self):
        """Different passwords produce different hashes."""
        hash1 = hash_password("password1")
        hash2 = hash_password("password2")
        
        assert hash1 != hash2

    def test_hash_password_long_password(self):
        """Handles long passwords (bcrypt truncates at 72 bytes)."""
        long_password = "a" * 100
        result = hash_password(long_password)
        
        assert isinstance(result, str)


class TestVerifyPassword:
    """Tests for verify_password function."""

    def test_verify_password_correct(self):
        """Correct password verifies successfully."""
        hashed = hash_password("correctpassword")
        
        assert verify_password("correctpassword", hashed) is True

    def test_verify_password_incorrect(self):
        """Incorrect password fails verification."""
        hashed = hash_password("correctpassword")
        
        assert verify_password("wrongpassword", hashed) is False

    def test_verify_password_case_sensitive(self):
        """Password verification is case sensitive."""
        hashed = hash_password("Password")
        
        assert verify_password("password", hashed) is False
        assert verify_password("PASSWORD", hashed) is False

    def test_verify_password_empty(self):
        """Empty password handled correctly."""
        hashed = hash_password("")
        
        assert verify_password("", hashed) is True
        assert verify_password("notempty", hashed) is False


class TestCreateAccessToken:
    """Tests for create_access_token function."""

    def test_create_access_token_returns_string(self):
        """Token is returned as string."""
        token = create_access_token(data={"sub": "1"})
        
        assert isinstance(token, str)
        assert len(token) > 0

    def test_create_access_token_is_valid_jwt(self):
        """Token is a valid JWT that can be decoded."""
        token = create_access_token(data={"sub": "testuser"})
        
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.ALGORITHM])
        
        assert "sub" in payload
        assert payload["sub"] == "testuser"

    def test_create_access_token_has_expiry(self):
        """Token contains expiration claim."""
        token = create_access_token(data={"sub": "1"})
        
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.ALGORITHM])
        
        assert "exp" in payload

    def test_create_access_token_has_issued_at(self):
        """Token contains issued-at claim."""
        token = create_access_token(data={"sub": "1"})
        
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.ALGORITHM])
        
        assert "iat" in payload

    def test_create_access_token_custom_expiry(self):
        """Custom expiry delta is respected."""
        custom_delta = timedelta(hours=2)
        token = create_access_token(data={"sub": "1"}, expires_delta=custom_delta)
        
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.ALGORITHM])
        
        assert "exp" in payload

    def test_create_access_token_preserves_data(self):
        """Additional data is preserved in token."""
        token = create_access_token(data={"sub": "1", "role": "admin", "email": "test@test.com"})
        
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.ALGORITHM])
        
        assert payload["role"] == "admin"
        assert payload["email"] == "test@test.com"


class TestVerifyToken:
    """Tests for verify_token function."""

    def test_verify_token_valid(self):
        """Valid token returns payload."""
        token = create_access_token(data={"sub": "testuser"})
        credentials_exception = Exception("Invalid credentials")
        
        payload = verify_token(token, credentials_exception)
        
        assert payload["sub"] == "testuser"

    def test_verify_token_missing_subject_raises(self):
        """Token without 'sub' raises exception."""
        token = jwt.encode({"exp": 9999999999}, settings.JWT_SECRET_KEY, algorithm=settings.ALGORITHM)
        credentials_exception = Exception("Invalid credentials")
        
        with pytest.raises(Exception) as exc_info:
            verify_token(token, credentials_exception)
        
        assert exc_info.value == credentials_exception

    def test_verify_token_invalid_signature_raises(self):
        """Token with invalid signature raises exception."""
        token = jwt.encode({"sub": "1"}, "wrong_secret", algorithm=settings.ALGORITHM)
        credentials_exception = Exception("Invalid credentials")
        
        with pytest.raises(Exception) as exc_info:
            verify_token(token, credentials_exception)
        
        assert exc_info.value == credentials_exception

    def test_verify_token_malformed_raises(self):
        """Malformed token raises exception."""
        credentials_exception = Exception("Invalid credentials")
        
        with pytest.raises(Exception) as exc_info:
            verify_token("not-a-valid-token", credentials_exception)
        
        assert exc_info.value == credentials_exception

    def test_verify_token_returns_full_payload(self):
        """Full payload dictionary is returned."""
        token = create_access_token(data={"sub": "1", "role": "admin", "email": "test@test.com"})
        credentials_exception = Exception("Invalid credentials")
        
        payload = verify_token(token, credentials_exception)
        
        assert "sub" in payload
        assert "exp" in payload
        assert "iat" in payload
        assert payload["role"] == "admin"
        assert payload["email"] == "test@test.com"
