import pytest
import time
from unittest.mock import patch, MagicMock
from app.core.rate_limit import (
    InMemoryRateLimiter,
    RedisRateLimiter,
    check_rate_limit,
    get_client_ip,
)


pytestmark = pytest.mark.unit


class TestInMemoryRateLimiter:
    """Tests for InMemoryRateLimiter class."""

    def test_is_allowed_first_request(self):
        """First request should be allowed."""
        limiter = InMemoryRateLimiter()
        
        allowed, remaining = limiter.is_allowed("test-key", max_requests=5, period_seconds=60)
        
        assert allowed is True
        assert remaining == 4

    def test_is_allowed_at_limit(self):
        """Request at limit should be rejected."""
        limiter = InMemoryRateLimiter()
        
        for i in range(5):
            limiter.is_allowed("test-key", max_requests=5, period_seconds=60)
        
        allowed, retry_after = limiter.is_allowed("test-key", max_requests=5, period_seconds=60)
        
        assert allowed is False
        assert retry_after >= 1

    def test_cleanup_removes_old_requests(self):
        """Old requests should be cleaned up."""
        limiter = InMemoryRateLimiter()
        
        old_time = time.time() - 120
        limiter._requests["test-key"] = [old_time, old_time - 10]
        
        allowed, remaining = limiter.is_allowed("test-key", max_requests=5, period_seconds=60)
        
        assert allowed is True
        assert len(limiter._requests["test-key"]) == 1

    def test_different_keys_independent(self):
        """Different keys should have independent limits."""
        limiter = InMemoryRateLimiter()
        
        for i in range(5):
            limiter.is_allowed("key-1", max_requests=5, period_seconds=60)
        
        allowed, _ = limiter.is_allowed("key-2", max_requests=5, period_seconds=60)
        
        assert allowed is True

    def test_get_remaining(self):
        """Get remaining returns correct count."""
        limiter = InMemoryRateLimiter()
        
        limiter.is_allowed("test-key", max_requests=5, period_seconds=60)
        limiter.is_allowed("test-key", max_requests=5, period_seconds=60)
        
        remaining = limiter.get_remaining("test-key", max_requests=5, period_seconds=60)
        
        assert remaining == 3

    def test_reset_clears_all(self):
        """Reset clears all keys."""
        limiter = InMemoryRateLimiter()
        
        limiter.is_allowed("key1", max_requests=1, period_seconds=60)
        limiter.is_allowed("key2", max_requests=1, period_seconds=60)
        
        limiter.reset()
        
        allowed, _ = limiter.is_allowed("key1", max_requests=5, period_seconds=60)
        assert allowed is True


class TestRedisRateLimiterClass:
    """Tests for RedisRateLimiter class."""

    def test_init_defaults(self):
        """Test default initialization."""
        limiter = RedisRateLimiter("redis://localhost:6379")
        
        assert limiter._redis_url == "redis://localhost:6379"
        assert limiter._fail_closed is True

    def test_init_custom_fail_closed(self):
        """Test custom fail_closed setting."""
        limiter = RedisRateLimiter("redis://localhost:6379", fail_closed=False)
        
        assert limiter._fail_closed is False

    @patch('app.core.rate_limit.TESTING', False)
    def test_is_allowed_redis_unavailable_fail_closed(self):
        """Test fail closed when Redis unavailable."""
        limiter = RedisRateLimiter("redis://localhost:6379", fail_closed=True)
        
        with patch.object(limiter, '_get_redis', return_value=None):
            with pytest.raises(Exception) as exc_info:
                limiter.is_allowed("test-key", max_requests=5, period_seconds=60)
            
            assert "503" in str(exc_info.value)

    @patch('app.core.rate_limit.TESTING', False)
    def test_is_allowed_redis_unavailable_fail_open(self):
        """Test fail open when Redis unavailable."""
        limiter = RedisRateLimiter("redis://localhost:6379", fail_closed=False)
        
        with patch.object(limiter, '_get_redis', return_value=None):
            allowed, remaining = limiter.is_allowed("test-key", max_requests=5, period_seconds=60)
            
            assert allowed is True

    @patch('app.core.rate_limit.TESTING', False)
    def test_is_allowed_redis_error_fail_closed(self):
        """Test fail closed on Redis error."""
        import redis
        limiter = RedisRateLimiter("redis://localhost:6379", fail_closed=True)
        
        mock_redis = MagicMock()
        mock_redis.pipeline.side_effect = redis.RedisError("Connection error")
        
        with patch.object(limiter, '_get_redis', return_value=mock_redis):
            with pytest.raises(Exception) as exc_info:
                limiter.is_allowed("test-key", max_requests=5, period_seconds=60)
            
            assert "503" in str(exc_info.value)

    def test_get_remaining_redis_available(self):
        """Test get_remaining with Redis."""
        limiter = RedisRateLimiter("redis://localhost:6379")
        
        mock_redis = MagicMock()
        mock_redis.zcard.return_value = 2
        
        with patch.object(limiter, '_get_redis', return_value=mock_redis):
            remaining = limiter.get_remaining("test-key", max_requests=5, period_seconds=60)
            
            assert remaining == 3

    def test_reset_clears_fallback(self):
        """Test reset clears fallback."""
        limiter = RedisRateLimiter("redis://localhost:6379")
        
        limiter._fallback.is_allowed("test-key", max_requests=5, period_seconds=60)
        
        limiter.reset()
        
        allowed, _ = limiter._fallback.is_allowed("test-key", max_requests=5, period_seconds=60)
        assert allowed is True


class TestGetClientIp:
    """Tests for get_client_ip function."""

    def test_extracts_ip_from_forwarded(self):
        """Should extract IP from X-Forwarded-For header."""
        mock_request = MagicMock()
        mock_request.headers.get.side_effect = lambda k: "203.0.113.1" if k == "X-Forwarded-For" else None
        mock_request.client.host = "127.0.0.1"
        
        result = get_client_ip(mock_request)
        
        assert result == "203.0.113.1"

    def test_fallback_to_client_host(self):
        """Should fall back to client host."""
        mock_request = MagicMock()
        mock_request.headers.get.return_value = None
        mock_request.client.host = "192.168.1.1"
        
        result = get_client_ip(mock_request)
        
        assert result == "192.168.1.1"

    def test_handles_none_client(self):
        """Should handle None client."""
        mock_request = MagicMock()
        mock_request.headers.get.return_value = None
        mock_request.client = None
        
        result = get_client_ip(mock_request)
        
        assert result == "unknown"


class TestCheckRateLimitFunction:
    """Tests for check_rate_limit function."""

    def test_allows_request_under_limit(self):
        """Should allow request under limit."""
        limiter = InMemoryRateLimiter()
        
        with patch('app.core.rate_limit.rate_limiter', limiter):
            mock_request = MagicMock()
            mock_request.headers.get.return_value = None
            mock_request.client.host = "10.0.0.1"
            
            check_rate_limit(mock_request, "login")


class TestInMemoryRateLimiterReset:
    """Tests for InMemoryRateLimiter reset."""

    def test_reset_clears_all_limits(self):
        """Reset should clear all rate limits."""
        limiter = InMemoryRateLimiter()
        
        limiter.is_allowed("key1", max_requests=1, period_seconds=60)
        limiter.is_allowed("key2", max_requests=1, period_seconds=60)
        
        limiter.reset()
        
        allowed, _ = limiter.is_allowed("key1", max_requests=5, period_seconds=60)
        assert allowed is True
