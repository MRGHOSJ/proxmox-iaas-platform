import os
import logging
from typing import Optional, Dict, Any
from functools import lru_cache

logger = logging.getLogger(__name__)

try:
    import hvac
except ImportError:
    hvac = None


def _get_vault_config():
    from dotenv import load_dotenv
    load_dotenv(override=False)
    
    return {
        "addr": os.getenv("VAULT_ADDR"),
        "role_id": os.getenv("VAULT_ROLE_ID"),
        "secret_id": os.getenv("VAULT_SECRET_ID"),
    }


VAULT_MOUNT_POINT = "secret"
VAULT_PATH = "cloud"


class VaultClient:
    _instance: Optional["VaultClient"] = None
    _client: Optional[Any] = None
    _secrets: Dict[str, Any] = {}
    _initialized: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        
        if not hvac:
            logger.warning("hvac library not installed. Vault integration disabled.")
            self._initialized = True
            return

        config = _get_vault_config()
        addr = config["addr"]
        role_id = config["role_id"]
        secret_id = config["secret_id"]

        if not all([addr, role_id, secret_id]):
            logger.warning(
                "Vault credentials not fully configured. "
                "VAULT_ADDR, VAULT_ROLE_ID, and VAULT_SECRET_ID must be set. "
                "Falling back to environment variables."
            )
            self._initialized = True
            return

        try:
            self._client = hvac.Client(url=addr)
            
            self._authenticate(role_id, secret_id)
            
            self._load_secrets()
            
            logger.info(f"Successfully connected to Vault at {addr}")
            self._initialized = True
            
        except Exception as e:
            logger.error(f"Vault error: {e}. Falling back to environment variables.")
            self._initialized = True

    def _authenticate(self, role_id: str, secret_id: str):
        if not self._client:
            return

        try:
            self._client.auth.approle.login(
                role_id=role_id,
                secret_id=secret_id
            )
            logger.debug("Successfully authenticated with Vault via AppRole")
        except Exception as e:
            logger.error(f"Failed to authenticate with Vault: {e}")
            raise

    def _load_secrets(self):
        if not self._client:
            return

        try:
            response = self._client.secrets.kv.v2.read_secret_version(
                path=VAULT_PATH,
                mount_point=VAULT_MOUNT_POINT
            )
            self._secrets = response["data"]["data"]
            logger.debug(f"Loaded secrets from Vault path: {VAULT_MOUNT_POINT}/{VAULT_PATH}")
        except Exception as e:
            logger.warning(f"No secrets found at Vault path: {VAULT_MOUNT_POINT}/{VAULT_PATH} - {e}")
            self._secrets = {}

    def get_secret(self, key: str, default: Optional[str] = None) -> Optional[str]:
        if not self._secrets:
            return default
        return self._secrets.get(key, default)

    def is_vault_available(self) -> bool:
        return bool(self._client and self._secrets)


@lru_cache(maxsize=1)
def get_vault_client() -> VaultClient:
    return VaultClient()


def get_secret(key: str, default: Optional[str] = None) -> Optional[str]:
    """
    Get secret from Vault or environment.
    Falls back to environment variables if Vault is not available or disabled.
    """
    # Check if Vault is explicitly disabled
    vault_enabled = os.getenv("VAULT_ENABLED", "true").lower() == "true"
    
    if not vault_enabled:
        logger.debug("Vault is disabled, using environment variables")
        return os.getenv(key, default)
    
    vault = get_vault_client()
    try:
        if vault.is_vault_available():
            secret = vault.get_secret(key)
            if secret:
                return secret
    except Exception as e:
        logger.debug(f"Vault not available, using env var: {e}")
    
    # Fallback to environment variable
    return os.getenv(key, default)
