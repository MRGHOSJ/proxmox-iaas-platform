import pytest
import time
from unittest.mock import patch, MagicMock


pytestmark = pytest.mark.unit


class TestTokenBlacklist:
    """Tests for token blacklist functions."""

    def setup_method(self):
        """Clear blacklist before each test."""
        from app.core import token_blacklist
        token_blacklist._token_blacklist.clear()
        token_blacklist._redis_client = None
        token_blacklist._redis_available = None

    @patch('app.core.token_blacklist._get_redis')
    def test_add_token_to_blacklist_redis(self, mock_get_redis):
        """Add token to Redis blacklist."""
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis
        
        from app.core.token_blacklist import add_token_to_blacklist
        with patch('app.core.token_blacklist._check_redis_required'):
            add_token_to_blacklist("test-jti-123", int(time.time()) + 3600)
        
        mock_redis.setex.assert_called_once()

    @patch('app.core.token_blacklist._get_redis')
    def test_is_token_blacklisted_redis(self, mock_get_redis):
        """Check token in Redis blacklist."""
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 1
        mock_get_redis.return_value = mock_redis
        
        from app.core.token_blacklist import is_token_blacklisted
        with patch('app.core.token_blacklist._check_redis_required'):
            result = is_token_blacklisted("test-jti-123")
        
        assert result is True
        mock_redis.exists.assert_called_once()

    @patch('app.core.token_blacklist._get_redis')
    def test_is_token_not_blacklisted_redis(self, mock_get_redis):
        """Check token not in Redis blacklist."""
        mock_redis = MagicMock()
        mock_redis.exists.return_value = 0
        mock_get_redis.return_value = mock_redis
        
        from app.core.token_blacklist import is_token_blacklisted
        with patch('app.core.token_blacklist._check_redis_required'):
            result = is_token_blacklisted("test-jti-123")
        
        assert result is False

    def test_add_token_in_memory(self):
        """Add token to in-memory blacklist."""
        from app.core.token_blacklist import add_token_to_blacklist, _token_blacklist
        with patch('app.core.token_blacklist._check_redis_required'):
            with patch('app.core.token_blacklist._get_redis', return_value=None):
                add_token_to_blacklist("test-jti", int(time.time()) + 3600)
        
        assert "test-jti" in _token_blacklist

    def test_is_token_in_memory(self):
        """Check token in in-memory blacklist."""
        from app.core.token_blacklist import add_token_to_blacklist, is_token_blacklisted
        with patch('app.core.token_blacklist._check_redis_required'):
            with patch('app.core.token_blacklist._get_redis', return_value=None):
                add_token_to_blacklist("test-jti", int(time.time()) + 3600)
                result = is_token_blacklisted("test-jti")
        
        assert result is True

    def test_is_token_not_in_memory(self):
        """Check token not in in-memory blacklist."""
        from app.core.token_blacklist import is_token_blacklisted
        with patch('app.core.token_blacklist._check_redis_required'):
            with patch('app.core.token_blacklist._get_redis', return_value=None):
                result = is_token_blacklisted("nonexistent-jti")
        
        assert result is False


class TestCleanupExpiredTokens:
    """Tests for cleanup of expired tokens."""

    def setup_method(self):
        """Clear blacklist before each test."""
        from app.core import token_blacklist
        token_blacklist._token_blacklist.clear()

    def test_cleanup_removes_expired_tokens(self):
        """Expired tokens are removed."""
        from app.core.token_blacklist import _token_blacklist, _cleanup_expired_tokens
        
        _token_blacklist["expired-jti"] = int(time.time()) - 100
        _token_blacklist["valid-jti"] = int(time.time()) + 3600
        
        _cleanup_expired_tokens()
        
        assert "expired-jti" not in _token_blacklist
        assert "valid-jti" in _token_blacklist

    def test_cleanup_no_expired_tokens(self):
        """No error when no expired tokens."""
        from app.core.token_blacklist import _token_blacklist, _cleanup_expired_tokens
        
        _token_blacklist["valid-jti"] = int(time.time()) + 3600
        
        _cleanup_expired_tokens()
        
        assert len(_token_blacklist) == 1


class TestClearBlacklist:
    """Tests for clear_blacklist function."""

    def setup_method(self):
        """Clear blacklist before each test."""
        from app.core import token_blacklist
        token_blacklist._token_blacklist.clear()
        token_blacklist._redis_client = None

    @patch('app.core.token_blacklist._get_redis')
    def test_clear_redis_blacklist(self, mock_get_redis):
        """Clear Redis blacklist."""
        mock_redis = MagicMock()
        mock_redis.scan_iter.return_value = ["blacklist:1", "blacklist:2"]
        mock_get_redis.return_value = mock_redis
        
        from app.core.token_blacklist import clear_blacklist
        clear_blacklist()
        
        mock_redis.delete.assert_called()

    def test_clear_in_memory_blacklist(self):
        """Clear in-memory blacklist."""
        from app.core.token_blacklist import _token_blacklist, clear_blacklist
        
        _token_blacklist["test-jti"] = int(time.time()) + 3600
        
        with patch('app.core.token_blacklist._get_redis', return_value=None):
            clear_blacklist()
        
        assert len(_token_blacklist) == 0


class TestGetBlacklistStats:
    """Tests for get_blacklist_stats function."""

    def setup_method(self):
        """Clear blacklist before each test."""
        from app.core import token_blacklist
        token_blacklist._token_blacklist.clear()
        token_blacklist._redis_client = None

    @patch('app.core.token_blacklist._get_redis')
    def test_stats_with_redis(self, mock_get_redis):
        """Get stats with Redis available."""
        mock_redis = MagicMock()
        mock_redis.scan_iter.return_value = ["blacklist:1"]
        mock_get_redis.return_value = mock_redis
        
        from app.core.token_blacklist import get_blacklist_stats
        from app.core.token_blacklist import _token_blacklist
        _token_blacklist["test-jti"] = int(time.time()) + 3600
        
        stats = get_blacklist_stats()
        
        assert stats["redis_available"] is True
        assert stats["redis_count"] == 1
        assert stats["in_memory_count"] == 1

    def test_stats_without_redis(self):
        """Get stats without Redis."""
        from app.core.token_blacklist import get_blacklist_stats
        from app.core.token_blacklist import _token_blacklist
        _token_blacklist["test-jti"] = int(time.time()) + 3600
        
        with patch('app.core.token_blacklist._get_redis', return_value=None):
            stats = get_blacklist_stats()
        
        assert stats["redis_available"] is False
        assert stats["in_memory_count"] == 1
