"""
Simple in-memory cache for temporary data.
For production, consider using Redis for distributed caching.
"""

import time
import threading

console_tokens = {}
console_tokens_lock = threading.Lock()


class ActiveConsoleSessions:
    """Thread-safe store for active console sessions per VM."""
    
    def __init__(self):
        self._sessions = {}
        self._lock = threading.Lock()
    
    def set(self, vm_id: int, session_data: dict) -> None:
        """Store an active console session for a VM."""
        with self._lock:
            self._sessions[vm_id] = {
                "data": session_data,
                "started_at": time.time()
            }
            logger.info(f"Console session tracked for VM {vm_id}")
    
    def get(self, vm_id: int) -> dict | None:
        """Get the active console session for a VM."""
        with self._lock:
            entry = self._sessions.get(vm_id)
            if not entry:
                return None
            return entry["data"]
    
    def remove(self, vm_id: int) -> dict | None:
        """Remove and return the console session for a VM."""
        with self._lock:
            entry = self._sessions.pop(vm_id, None)
            if not entry:
                return None
            return entry["data"]
    
    def get_all(self) -> dict:
        """Get all active console sessions (returns a copy)."""
        with self._lock:
            return {k: v["data"] for k, v in self._sessions.items()}
    
    def clear(self) -> None:
        """Clear all active console sessions."""
        with self._lock:
            self._sessions.clear()


import logging
logger = logging.getLogger(__name__)

active_console_sessions = ActiveConsoleSessions()

class ConsoleTokenStore:
    """Thread-safe store for VNC console tokens with expiration."""
    
    def __init__(self, ttl_seconds: int = 30):
        self._tokens = {}
        self._lock = threading.Lock()
        self._ttl = ttl_seconds
    
    def set(self, token: str, data: dict) -> None:
        with self._lock:
            self._tokens[token] = {
                "data": data,
                "expires_at": time.time() + self._ttl
            }
    
    def get(self, token: str) -> dict:
        with self._lock:
            entry = self._tokens.get(token)
            if not entry:
                return None
            if time.time() > entry["expires_at"]:
                del self._tokens[token]
                return None
            return entry["data"]
    
    def pop(self, token: str) -> dict:
        with self._lock:
            entry = self._tokens.pop(token, None)
            if not entry:
                return None
            if time.time() > entry["expires_at"]:
                return None
            return entry["data"]

console_token_store = ConsoleTokenStore(ttl_seconds=30)

def get_console_token(token: str) -> dict:
    """Get and validate a console token."""
    return console_token_store.get(token)

def set_console_token(token: str, data: dict) -> None:
    """Store a console token with TTL."""
    console_token_store.set(token, data)

def pop_console_token(token: str) -> dict:
    """Retrieve and remove a console token."""
    return console_token_store.pop(token)


def set_active_console_session(vm_id: int, session_data: dict) -> None:
    """Store an active console session for a VM."""
    active_console_sessions.set(vm_id, session_data)


def get_active_console_session(vm_id: int) -> dict | None:
    """Get the active console session for a VM."""
    return active_console_sessions.get(vm_id)


def remove_active_console_session(vm_id: int) -> dict | None:
    """Remove and return the console session for a VM."""
    return active_console_sessions.remove(vm_id)