from fastapi import HTTPException, status
from typing import Optional


class TerraformExecutionError(Exception):
    """
    Custom exception for when Terraform (init/apply/destroy) fails.
    We want to show the logs to the user, not just a generic 500 error.
    """
    def __init__(self, message: str, logs: Optional[str] = None):
        self.message = message
        self.logs = logs
        super().__init__(self.message)


class ResourceConflictError(Exception):
    """Raised when a resource already exists (race condition detected)."""
    def __init__(self, resource_type: str, identifier: str):
        self.resource_type = resource_type
        self.identifier = identifier
        super().__init__(f"{resource_type} '{identifier}' already exists")


class ResourceNotFoundError(Exception):
    """Raised when a resource is not found."""
    def __init__(self, resource_type: str, identifier: str):
        self.resource_type = resource_type
        self.identifier = identifier
        super().__init__(f"{resource_type} '{identifier}' not found")


class InvalidStateTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""
    def __init__(self, current_state: str, target_state: str, allowed_transitions: list):
        self.current_state = current_state
        self.target_state = target_state
        self.allowed_transitions = allowed_transitions
        super().__init__(
            f"Cannot transition from '{current_state}' to '{target_state}'. "
            f"Allowed: {allowed_transitions}"
        )


class RateLimitExceededError(Exception):
    """Raised when rate limit is exceeded."""
    def __init__(self, retry_after: int):
        self.retry_after = retry_after
        super().__init__(f"Rate limit exceeded. Retry after {retry_after} seconds.")


class ConfigurationError(Exception):
    """Raised when there's a configuration issue."""
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class ProviderUnavailableError(Exception):
    """Raised when a provider (Celery, Redis, etc.) is unavailable."""
    def __init__(self, provider: str, detail: Optional[str] = None):
        self.provider = provider
        self.detail = detail
        super().__init__(f"{provider} is unavailable: {detail or 'unknown error'}")


class WireGuardConfigError(Exception):
    """Raised when WireGuard configuration fails (key generation, OPNsense API, etc.)."""
    def __init__(self, message: str, logs: Optional[str] = None):
        self.message = message
        self.logs = logs
        super().__init__(self.message)