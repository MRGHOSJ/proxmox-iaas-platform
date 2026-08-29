"""
Symmetric field-level encryption helpers.

Used to encrypt sensitive WireGuard values (peer private keys, pre-shared keys)
at rest in the database. The encryption key is loaded from
settings.WIREGUARD_FIELD_ENCRYPTION_KEY (Fernet, 32-byte url-safe base64).

If no key is configured a development-only key is generated and emitted to logs
on startup so the application still boots. Production deployments MUST set
WIREGUARD_FIELD_ENCRYPTION_KEY explicitly — rotating it requires re-encrypting
all stored values, which is out of scope for v1.
"""
import logging
import os
import threading

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_fernet: Fernet | None = None
_key_warned: bool = False


def _resolve_key() -> bytes:
    """
    Resolve the Fernet key.

    Priority:
      1. WIREGUARD_FIELD_ENCRYPTION_KEY env / vault value (base64-encoded 32 bytes)
      2. Stable dev key derived from a file under /tmp (NOT for production)
      3. Random ephemeral key — values become unreadable across restarts.
    """
    global _key_warned

    raw = settings.WIREGUARD_FIELD_ENCRYPTION_KEY
    if raw:
        try:
            return raw.encode("ascii") if isinstance(raw, str) else raw
        except Exception:
            logger.exception(
                "WIREGUARD_FIELD_ENCRYPTION_KEY is set but invalid — generating a development key instead."
            )

    dev_path = "/tmp/.cloud_wg_fernet_key"
    if os.path.exists(dev_path):
        try:
            with open(dev_path, "rb") as fh:
                return fh.read()
        except Exception:
            logger.warning("Could not read dev Fernet key at %s; generating a new one.", dev_path)

    key = Fernet.generate_key()
    try:
        with open(dev_path, "wb") as fh:
            fh.write(key)
        os.chmod(dev_path, 0o600)
    except Exception:
        pass

    if not _key_warned:
        _key_warned = True
        logger.warning(
            "WIREGUARD_FIELD_ENCRYPTION_KEY is not set. Using a development key persisted at %s. "
            "Set WIREGUARD_FIELD_ENCRYPTION_KEY in production so that encrypted values survive "
            "restarts and replicas.",
            dev_path,
        )
    return key


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        with _lock:
            if _fernet is None:
                _fernet = Fernet(_resolve_key())
    return _fernet


def encrypt(plaintext: str) -> str:
    """Encrypt a string and return a url-safe base64 token."""
    if plaintext is None:
        return None
    return _get_fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    """Decrypt a previously encrypted token. Raises on invalid token."""
    if token is None:
        return None
    try:
        return _get_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise ValueError("Invalid encrypted value (key may have changed)") from e
