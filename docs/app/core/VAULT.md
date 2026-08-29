# Vault Integration Documentation

This document describes the HashiCorp Vault integration for secure secret management.

---

## Table of Contents
- [Overview](#overview)
- [Configuration](#configuration)
- [Usage](#usage)
- [Fallback Behavior](#fallback-behavior)
- [Secret Loading](#secret-loading)

---

## Overview

**File:** `app/core/vault.py`

The Vault integration provides secure secret storage using HashiCorp Vault. It supports:
- AppRole authentication
- KV v2 secret engine
- Graceful fallback to environment variables

---

## Configuration

### Environment Variables

| Variable | Type | Required | Description |
|----------|------|----------|-------------|
| `VAULT_ENABLED` | boolean | No | Enable/disable Vault (default: `true`) |
| `VAULT_ADDR` | string | Yes* | Vault server address |
| `VAULT_ROLE_ID` | string | Yes* | AppRole role ID |
| `VAULT_SECRET_ID` | string | Yes* | AppRole secret ID |

*Required only if `VAULT_ENABLED=true`

### Configuration Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `VAULT_MOUNT_POINT` | `secret` | KV v2 mount point |
| `VAULT_PATH` | `cloud` | Secret path |

---

## Usage

### Getting Secrets

```python
from app.core.vault import get_secret

# Get a secret
api_key = get_secret("PROXMOX_PASSWORD")

# With default fallback
token = get_secret("JWT_SECRET_KEY", "default-token")
```

### Check Vault Availability

```python
from app.core.vault import get_vault_client

vault = get_vault_client()
if vault.is_vault_available():
    # Use Vault secrets
    pass
```

---

## Fallback Behavior

If Vault is unavailable or disabled, the system falls back to environment variables:

```
┌─────────────────────────────────┐
│     get_secret(key)              │
└───────────────┬─────────────────┘
                │
       ┌────────┴────────┐
       │ VAULT_ENABLED?    │
       └────────┬─────────┘
          │     │
         No    Yes
         │     │
         ▼     ▼
    os.getenv  is_vault_available?
       │           │
       │          No
       │          │
       │          ▼
       │    os.getenv(key)
       │          │
       └────┬───┘
            ▼
        Return secret
```

### Fallback Conditions

| Condition | Behavior |
|-----------|----------|
| `VAULT_ENABLED=false` | Uses environment variables directly |
| `hvac` not installed | Uses environment variables |
| Vault credentials not configured | Uses environment variables |
| Vault unreachable | Uses environment variables |
| Secret not in Vault | Uses environment variable |

---

## Secret Loading

At startup, the VaultClient loads all secrets from `secret/cloud`:

```python
def _load_secrets(self):
    response = self._client.secrets.kv.v2.read_secret_version(
        path="cloud",
        mount_point="secret"
    )
    self._secrets = response["data"]["data"]
```

### Secrets Structure

Expected Vault structure:

```
secret/cloud/
├── PROXMOX_PASSWORD=xxx
├── PROXMOX_USER=xxx
├── VCENTER_PASSWORD=xxx
├── AWS_SECRET_KEY=xxx
└── ... (other secrets)
```

---

## Error Handling

| Error | Handling |
|-------|----------|
| Vault not configured | Log warning, use env vars |
| Authentication failed | Log error, use env vars |
| Secret not found | Log warning, return default |
| Connection error | Log error, use env vars |

### Logging

```
WARNING: Vault credentials not fully configured. Falling back to environment variables.
INFO: Successfully connected to Vault at https://vault.example.com
WARNING: No secrets found at Vault path: secret/cloud
ERROR: Failed to authenticate with Vault: xxx - Falling back to environment variables.
```

---

## Best Practices

1. **Enable Vault in production** - Use for production deployments
2. **Use AppRole** - Not root tokens
3. **Rotate secrets regularly** - Automated rotation recommended
4. **Audit access** - Enable Vault audit logging
5. **Network isolation** - Use TLS for Vault communication