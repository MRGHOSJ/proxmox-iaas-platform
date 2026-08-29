import os
import time
import logging
from collections import defaultdict
from threading import Lock
from typing import Dict, Tuple, Optional
from fastapi import HTTPException, status, Request

from app.core.config import settings
from app.core.dependencies import get_client_ip


logger = logging.getLogger(__name__)

TESTING = os.getenv("TESTING", "false").lower() == "true"

# Configuration: Whether to fail closed (reject requests) when rate limiter is unavailable
# In production, this should be True for security
RATE_LIMIT_FAIL_CLOSED = os.getenv("RATE_LIMIT_FAIL_CLOSED", "true").lower() == "true"


class InMemoryRateLimiter:
    """
    Thread-safe in-memory rate limiter.
    For production, use Redis-based rate limiting.
    """
    
    def __init__(self):
        self._requests: Dict[str, list] = defaultdict(list)
        self._lock = Lock()
    
    def _cleanup_old_requests(self, key: str, period_seconds: int) -> None:
        current_time = time.time()
        cutoff_time = current_time - period_seconds
        self._requests[key] = [
            req_time for req_time in self._requests[key]
            if req_time > cutoff_time
        ]
    
    def is_allowed(self, key: str, max_requests: int, period_seconds: int) -> Tuple[bool, int]:
        with self._lock:
            self._cleanup_old_requests(key, period_seconds)
            
            current_count = len(self._requests[key])
            
            if current_count >= max_requests:
                retry_after = int(period_seconds - (time.time() - self._requests[key][0])) + 1
                return False, max(1, retry_after)
            
            self._requests[key].append(time.time())
            return True, max_requests - current_count - 1
    
    def get_remaining(self, key: str, max_requests: int, period_seconds: int) -> int:
        with self._lock:
            self._cleanup_old_requests(key, period_seconds)
            return max(0, max_requests - len(self._requests[key]))
    
    def reset(self) -> None:
        """Reset all rate limit counters. Useful for testing."""
        with self._lock:
            self._requests.clear()


class RedisRateLimiter:
    """
    Redis-based distributed rate limiter.
    Works across multiple workers/processes.
    
    If Redis is unavailable:
    - If RATE_LIMIT_FAIL_CLOSED=True: Reject requests (secure)
    - If RATE_LIMIT_FAIL_CLOSED=False: Allow requests (convenient but insecure)
    """
    
    def __init__(self, redis_url: str, fail_closed: bool = True):
        self._redis_url = redis_url
        self._redis = None
        self._fail_closed = fail_closed
        self._fallback = InMemoryRateLimiter()
        self._last_error_time = 0
        self._error_count = 0
    
    def _get_redis(self):
        if self._redis is None:
            try:
                import redis
                self._redis = redis.from_url(self._redis_url)
            except Exception:
                pass
        return self._redis
    
    def is_allowed(self, key: str, max_requests: int, period_seconds: int) -> Tuple[bool, int]:
        redis_client = self._get_redis()
        
        if redis_client is None:
            if self._fail_closed and not TESTING:
                # Fail closed: reject requests when Redis is unavailable
                logger.error("Redis unavailable and fail_closed is enabled - rejecting request")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Rate limiting service unavailable. Please try again later."
                )
            return self._fallback.is_allowed(key, max_requests, period_seconds)
        
        try:
            import redis
            current_time = time.time()
            window_key = f"ratelimit:{key}"
            
            pipe = redis_client.pipeline()
            pipe.zremrangebyscore(window_key, 0, current_time - period_seconds)
            pipe.zcard(window_key)
            pipe.zadd(window_key, {str(current_time): current_time})
            pipe.expire(window_key, period_seconds)
            
            results = pipe.execute()
            current_count = results[1]
            
            if current_count >= max_requests:
                oldest = redis_client.zrange(window_key, 0, 0, withscores=True)
                if oldest:
                    retry_after = int(period_seconds - (current_time - oldest[0][1])) + 1
                    return False, max(1, retry_after)
                return False, period_seconds
            
            return True, max_requests - current_count - 1
            
        except (redis.RedisError, redis.ConnectionError) as e:
            logger.error(f"Redis error during rate limiting: {e}")
            
            if self._fail_closed and not TESTING:
                # Fail closed: reject requests when Redis errors
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Rate limiting service error. Please try again later."
                )
            
            # Fall back to in-memory (not recommended for production)
            return self._fallback.is_allowed(key, max_requests, period_seconds)
    
    def get_remaining(self, key: str, max_requests: int, period_seconds: int) -> int:
        redis_client = self._get_redis()
        
        if redis_client is None:
            return self._fallback.get_remaining(key, max_requests, period_seconds)
        
        try:
            current_time = time.time()
            window_key = f"ratelimit:{key}"
            
            redis_client.zremrangebyscore(window_key, 0, current_time - period_seconds)
            current_count = redis_client.zcard(window_key)
            return max(0, max_requests - current_count)
            
        except Exception:
            return self._fallback.get_remaining(key, max_requests, period_seconds)
    
    def reset(self) -> None:
        self._fallback.reset()


def create_rate_limiter():
    """Factory function to create appropriate rate limiter."""
    if TESTING or not settings.CELERY_BROKER_URL:
        logger.info("Using in-memory rate limiter (testing mode or no Redis configured)")
        return InMemoryRateLimiter()
    
    redis_url = settings.CELERY_BROKER_URL
    if redis_url.startswith("redis://"):
        fail_closed = RATE_LIMIT_FAIL_CLOSED
        logger.info(f"Using Redis rate limiter (fail_closed={fail_closed})")
        return RedisRateLimiter(redis_url, fail_closed=fail_closed)
    
    logger.info("Using in-memory rate limiter (no Redis URL detected)")
    return InMemoryRateLimiter()


rate_limiter = create_rate_limiter()


def check_rate_limit(request: Request, endpoint: str = "default") -> None:
    """
    Check rate limit for the current request.
    
    Endpoint-specific rate limits:
    - admin_*: Stricter limits for admin operations
    - vm_create: Standard limit for VM creation
    - network_create/delete: Standard limit for network operations
    - register/login: Standard limit for auth operations
    - default: Fallback limit
    """
    if not settings.RATE_LIMIT_ENABLED or TESTING:
        return
    
    client_ip = get_client_ip(request)
    key = f"{endpoint}:{client_ip}"
    
    # Admin endpoints get stricter limits to prevent abuse
    # Resource-intensive endpoints also get stricter limits
    admin_endpoints = ["admin_audit", "admin_fix", "admin_reconcile", 
                       "admin_status_override", "admin_users"]
    
    # Resource-intensive endpoints that need stricter rate limiting
    intensive_endpoints = ["vm_logs", "vm_create", "network_create"]
    
    if endpoint in admin_endpoints:
        max_requests = settings.RATE_LIMIT_ADMIN_REQUESTS
        period_seconds = settings.RATE_LIMIT_ADMIN_PERIOD_SECONDS
    elif endpoint in intensive_endpoints:
        max_requests = max(1, settings.RATE_LIMIT_REQUESTS // 2)
        period_seconds = settings.RATE_LIMIT_PERIOD_SECONDS
    else:
        max_requests = settings.RATE_LIMIT_REQUESTS
        period_seconds = settings.RATE_LIMIT_PERIOD_SECONDS
    
    allowed, retry_after = rate_limiter.is_allowed(
        key,
        max_requests,
        period_seconds
    )
    
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Try again in {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)}
        )


def check_admin_rate_limit(request: Request, endpoint: str = "admin") -> None:
    """
    Convenience function for admin endpoints with automatic stricter limits.
    Admin operations have separate rate limiting to prevent:
    - DoS via expensive reconciliation operations
    - Brute force status manipulation
    - Audit log flooding
    """
    check_rate_limit(request, endpoint=f"admin_{endpoint}")
