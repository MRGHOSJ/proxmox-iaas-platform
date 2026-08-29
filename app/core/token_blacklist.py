import time
import os
import logging
from typing import Optional
from threading import Lock

_token_blacklist: dict = {}
_blacklist_lock = Lock()

BLACKLIST_CLEANUP_INTERVAL = 3600
_last_cleanup = time.time()

_redis_client = None
_redis_available = None
_production_warning_shown = False

logger = logging.getLogger(__name__)


def _get_redis():
    """Get or create Redis client for distributed token blacklist."""
    global _redis_client, _redis_available
    
    if _redis_client is not None:
        return _redis_client
    
    try:
        import redis
        from app.core.config import settings
        
        if settings.CELERY_BROKER_URL and settings.CELERY_BROKER_URL.startswith("redis://"):
            _redis_client = redis.from_url(settings.CELERY_BROKER_URL)
            _redis_client.ping()
            _redis_available = True
            logger.info("Redis token blacklist initialized successfully")
            return _redis_client
    except Exception as e:
        logger.warning(f"Redis not available for token blacklist: {e}")
        _redis_available = False
    
    return None


def _check_redis_required():
    """
    Check if Redis is required in production environment.
    
    CRITICAL: In production with multiple workers, in-memory blacklist
    will NOT be shared between workers. Tokens blacklisted on worker A
    will remain valid on worker B. This is a security vulnerability.
    
    Raises RuntimeError if Redis is required but unavailable.
    """
    global _production_warning_shown
    from app.core.config import settings
    
    if settings.ENVIRONMENT == "production":
        if not _production_warning_shown:
            _production_warning_shown = True
        
        if _redis_available is False or (_redis_available is None and _get_redis() is None):
            raise RuntimeError(
                "CRITICAL SECURITY ERROR: Redis is required for token blacklist in production environment. "
                "Without Redis, tokens blacklisted on one worker will remain valid on other workers. "
                "Please configure Redis at CELERY_BROKER_URL to enable distributed token blacklist."
            )


def add_token_to_blacklist(jti: str, expires_at: int) -> None:
    """
    Add a token to the blacklist.
    Uses Redis if available for distributed support, falls back to in-memory.
    
    In production with REQUIRE_REDIS_IN_PRODUCTION=true, raises RuntimeError if Redis unavailable.
    
    Args:
        jti: JWT ID (unique identifier for the token)
        expires_at: Token expiration timestamp
    """
    global _last_cleanup
    
    _check_redis_required()
    
    redis_client = _get_redis()
    
    if redis_client:
        try:
            ttl = max(1, int(expires_at - time.time()))
            redis_client.setex(f"blacklist:{jti}", ttl, "1")
            logger.debug(f"Token {jti[:8]}... added to Redis blacklist")
            return
        except Exception as e:
            logger.error(f"Failed to add token to Redis blacklist: {e}")
            _check_redis_required()
            if _redis_available is False:
                raise RuntimeError(
                    "Redis connection lost in production. Token blacklist operation failed. "
                    "This is a critical security error."
                )
    
    with _blacklist_lock:
        _token_blacklist[jti] = expires_at
        logger.debug(f"Token {jti[:8]}... added to in-memory blacklist")
        
        if time.time() - _last_cleanup > BLACKLIST_CLEANUP_INTERVAL:
            _cleanup_expired_tokens()
            _last_cleanup = time.time()


def is_token_blacklisted(jti: str) -> bool:
    """
    Check if a token is blacklisted.
    Checks Redis first if available, then falls back to in-memory.
    
    In production with REQUIRE_REDIS_IN_PRODUCTION=true, raises RuntimeError if Redis unavailable.
    
    Args:
        jti: JWT ID (unique identifier for the token)
        
    Returns:
        bool: True if token is blacklisted
    """
    _check_redis_required()
    
    redis_client = _get_redis()
    
    if redis_client:
        try:
            return bool(redis_client.exists(f"blacklist:{jti}"))
        except Exception as e:
            logger.error(f"Failed to check Redis blacklist: {e}")
            _check_redis_required()
            if _redis_available is False:
                raise RuntimeError(
                    "Redis connection lost in production. Token blacklist check failed. "
                    "This is a critical security error."
                )
    
    with _blacklist_lock:
        return jti in _token_blacklist


def _cleanup_expired_tokens() -> None:
    """Remove expired tokens from the in-memory blacklist."""
    current_time = time.time()
    expired_jtis = [
        jti for jti, expires_at in _token_blacklist.items()
        if expires_at < current_time
    ]
    for jti in expired_jtis:
        del _token_blacklist[jti]
    
    if expired_jtis:
        logger.debug(f"Cleaned up {len(expired_jtis)} expired tokens from in-memory blacklist")


def clear_blacklist() -> None:
    """Clear all tokens from blacklist. Used for testing."""
    global _token_blacklist
    
    redis_client = _get_redis()
    if redis_client:
        try:
            for key in redis_client.scan_iter("blacklist:*"):
                redis_client.delete(key)
            logger.debug("Cleared Redis token blacklist")
        except Exception as e:
            logger.warning(f"Failed to clear Redis blacklist: {e}")
    
    with _blacklist_lock:
        _token_blacklist.clear()
        logger.debug("Cleared in-memory token blacklist")


def get_blacklist_stats() -> dict:
    """Get statistics about the token blacklist."""
    redis_client = _get_redis()
    stats = {
        "redis_available": redis_client is not None,
        "in_memory_count": len(_token_blacklist)
    }
    
    if redis_client:
        try:
            keys = list(redis_client.scan_iter("blacklist:*"))
            stats["redis_count"] = len(keys)
        except Exception:
            stats["redis_count"] = "error"
    
    return stats
