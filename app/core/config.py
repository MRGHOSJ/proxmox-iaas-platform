import logging
import os
import sys
import json
from typing import List

from dotenv import load_dotenv

load_dotenv(override=False)

from app.core.vault import get_secret


class Settings:
    DATABASE_URL = get_secret("DATABASE_URL", os.getenv("DATABASE_URL"))
    JWT_SECRET_KEY = get_secret("JWT_SECRET_KEY", os.getenv("JWT_SECRET_KEY"))
    ALGORITHM = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
    DEBUG = os.getenv("DEBUG", "False").lower() == "true"
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
    CELERY_BROKER_URL = get_secret("CELERY_BROKER_URL", os.getenv("CELERY_BROKER_URL"))
    CELERY_RESULT_BACKEND = get_secret("CELERY_RESULT_BACKEND", os.getenv("CELERY_RESULT_BACKEND"))
    
    ALLOW_REGISTRATION = os.getenv("ALLOW_REGISTRATION", "false").lower() == "true"
    
    ALLOWED_EMAIL_DOMAINS = os.getenv("ALLOWED_EMAIL_DOMAINS", "")
    
    RATE_LIMIT_ENABLED = os.getenv("RATE_LIMIT_ENABLED", "true").lower() == "true"
    RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "5"))
    RATE_LIMIT_PERIOD_SECONDS = int(os.getenv("RATE_LIMIT_PERIOD_SECONDS", "60"))
    RATE_LIMIT_ADMIN_REQUESTS = int(os.getenv("RATE_LIMIT_ADMIN_REQUESTS", "10"))
    RATE_LIMIT_ADMIN_PERIOD_SECONDS = int(os.getenv("RATE_LIMIT_ADMIN_PERIOD_SECONDS", "60"))
    
    DEFAULT_ADMIN_USERNAME = get_secret("DEFAULT_ADMIN_USERNAME", os.getenv("DEFAULT_ADMIN_USERNAME"))
    DEFAULT_ADMIN_PASSWORD = get_secret("DEFAULT_ADMIN_PASSWORD", os.getenv("DEFAULT_ADMIN_PASSWORD"))
    DEFAULT_ADMIN_EMAIL = get_secret("DEFAULT_ADMIN_EMAIL", os.getenv("DEFAULT_ADMIN_EMAIL"))
    
    ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
    
    DATABASE_POOL_SIZE = int(os.getenv("DATABASE_POOL_SIZE", "10"))
    DATABASE_MAX_OVERFLOW = int(os.getenv("DATABASE_MAX_OVERFLOW", "20"))
    DATABASE_POOL_RECYCLE = int(os.getenv("DATABASE_POOL_RECYCLE", "3600"))
    DATABASE_ECHO_SQL = os.getenv("DATABASE_ECHO_SQL", "false").lower() == "true"
    
    REQUEST_MAX_BODY_SIZE = int(os.getenv("REQUEST_MAX_BODY_SIZE", "10485760"))
    
    VCENTER_SERVER = get_secret("VCENTER_SERVER", os.getenv("VCENTER_SERVER", ""))
    VCENTER_USER = get_secret("VCENTER_USER", os.getenv("VCENTER_USER", ""))
    VCENTER_PASSWORD = get_secret("VCENTER_PASSWORD", os.getenv("VCENTER_PASSWORD", ""))
    VCENTER_DATACENTER = get_secret("VCENTER_DATACENTER", os.getenv("VCENTER_DATACENTER", ""))
    VCENTER_CLUSTER = get_secret("VCENTER_CLUSTER", os.getenv("VCENTER_CLUSTER", ""))
    VCENTER_DATASTORE = get_secret("VCENTER_DATASTORE", os.getenv("VCENTER_DATASTORE", ""))
    VCENTER_NETWORK = get_secret("VCENTER_NETWORK", os.getenv("VCENTER_NETWORK", ""))
    
    CORS_ORIGINS = os.getenv("CORS_ORIGINS", '["http://localhost:3000", "http://localhost:8000"]')
    
    PROXMOX_URL = get_secret("PROXMOX_URL", os.getenv("PROXMOX_URL", "https://192.168.100.100:8006"))
    PROXMOX_USERNAME = get_secret("PROXMOX_USERNAME", os.getenv("PROXMOX_USERNAME", "root@pam"))
    PROXMOX_TOKEN = get_secret("PROXMOX_TOKEN", os.getenv("PROXMOX_TOKEN", ""))
    PROXMOX_NODE = os.getenv("PROXMOX_NODE", "pve")
    PROXMOX_STORAGE = os.getenv("PROXMOX_STORAGE", "local-lvm")
    
    HYPERVISOR_TYPE = os.getenv("HYPERVISOR_TYPE", "proxmox")
    OPNSENSE_TEMPLATE_ID = int(os.getenv("OPNSENSE_TEMPLATE_ID", "9000"))
    OPNSENSE_MIN_MEMORY_MB = int(os.getenv("OPNSENSE_MIN_MEMORY_MB", "800"))
    
    OPNSENSE_BOOTSTRAP_KEY = get_secret("OPNSENSE_BOOTSTRAP_KEY", os.getenv("OPNSENSE_BOOTSTRAP_KEY", ""))
    OPNSENSE_BOOTSTRAP_SECRET = get_secret("OPNSENSE_BOOTSTRAP_SECRET", os.getenv("OPNSENSE_BOOTSTRAP_SECRET", ""))

    DEFAULT_OPNSENSE_VM_ID = int(os.getenv("OPNSENSE_VM_ID", "10000"))
    DEFAULT_OPNSENSE_NODE = os.getenv("OPNSENSE_NODE", "pve")
    DEFAULT_OPNSENSE_PARENT_IF = os.getenv("OPNSENSE_PARENT_IF", "vtnet1")
    OPNSENSE_CONFIG_PATH = os.getenv("OPNSENSE_CONFIG_PATH", "/conf/config.xml")

    GUEST_AGENT_SETTLE_DELAY = int(os.getenv("GUEST_AGENT_SETTLE_DELAY", "1"))

    WIREGUARD_DEFAULT_LISTEN_PORT = int(os.getenv("WIREGUARD_DEFAULT_LISTEN_PORT", "51820"))
    WIREGUARD_DEFAULT_MTU = int(os.getenv("WIREGUARD_DEFAULT_MTU", "1420"))
    WIREGUARD_DEFAULT_DNS = os.getenv("WIREGUARD_DEFAULT_DNS", "1.1.1.1, 1.0.0.1")
    WIREGUARD_GLOBAL_POOL_CIDR = os.getenv("WIREGUARD_GLOBAL_POOL_CIDR", "10.200.0.0/14")
    WIREGUARD_PEER_KEEPALIVE = int(os.getenv("WIREGUARD_PEER_KEEPALIVE", "25"))
    WIREGUARD_FIELD_ENCRYPTION_KEY = get_secret(
        "WIREGUARD_FIELD_ENCRYPTION_KEY", os.getenv("WIREGUARD_FIELD_ENCRYPTION_KEY", "")
    )
    
    REQUIRE_REDIS_IN_PRODUCTION = True
    
    CREATE_DEFAULT_ADMIN = os.getenv("CREATE_DEFAULT_ADMIN", "false").lower() == "true"
    
    TERRAFORM_TEMP_DIR = os.getenv("TERRAFORM_TEMP_DIR", "")
    
    DEFAULT_VM_IMAGE = os.getenv("DEFAULT_VM_IMAGE", "nginx:latest")
    
    @property
    def allowed_email_domains_list(self) -> List[str]:
        if not self.ALLOWED_EMAIL_DOMAINS:
            return []
        return [d.strip().lower() for d in self.ALLOWED_EMAIL_DOMAINS.split(",") if d.strip()]
    
    @property
    def cors_origins_list(self) -> List[str]:
        try:
            origins = json.loads(self.CORS_ORIGINS) if isinstance(self.CORS_ORIGINS, str) else self.CORS_ORIGINS
            return origins if isinstance(origins, list) else []
        except (json.JSONDecodeError, TypeError):
            return ["http://localhost:3000", "http://localhost:8000"]
    
    def validate_production_settings(self):
        """Validate critical settings for production environment."""
        if self.ENVIRONMENT == "production":
            errors = []
            
            if not self.JWT_SECRET_KEY or self.JWT_SECRET_KEY == "jwt-secret-key-change-in-production-1234567890":
                errors.append("JWT_SECRET_KEY must be set to a secure value in production")
            
            if not self.DEFAULT_ADMIN_PASSWORD:
                errors.append("DEFAULT_ADMIN_PASSWORD must be set in production")
            
            if not self.DEFAULT_ADMIN_EMAIL or self.DEFAULT_ADMIN_EMAIL == "admin@cloud.com":
                errors.append("DEFAULT_ADMIN_EMAIL must be explicitly set in production (not using default 'admin@cloud.com')")
            
            if not self.DATABASE_URL:
                errors.append("DATABASE_URL must be set in production")
            
            if "sqlite" in (self.DATABASE_URL or "").lower():
                errors.append("SQLite is not recommended for production. Use PostgreSQL.")
            
            if self.REQUIRE_REDIS_IN_PRODUCTION:
                if not self.CELERY_BROKER_URL or not self.CELERY_BROKER_URL.startswith("redis://"):
                    errors.append("CELERY_BROKER_URL must be a Redis URL in production (redis://...)")
            
            cors_origins = self.cors_origins_list
            if "*" in cors_origins:
                errors.append("CORS_ORIGINS cannot contain '*' in production")
            
            if errors:
                for error in errors:
                    logging.error(f"Configuration Error: {error}")
                sys.exit(1)


settings = Settings()
settings.validate_production_settings()

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    force=True
)
