import pytest
from app.core.exceptions import (
    TerraformExecutionError,
    ResourceConflictError,
    ResourceNotFoundError,
    InvalidStateTransitionError,
    RateLimitExceededError,
    ConfigurationError,
    ProviderUnavailableError,
)


pytestmark = pytest.mark.unit


class TestTerraformExecutionError:
    """Tests for TerraformExecutionError custom exception."""

    def test_exception_with_message_only(self):
        """Exception can be created with message only."""
        exc = TerraformExecutionError("Terraform failed")
        
        assert exc.message == "Terraform failed"
        assert exc.logs is None

    def test_exception_with_message_and_logs(self):
        """Exception can be created with message and logs."""
        logs = "Error: something went wrong\nLine 2 of error"
        exc = TerraformExecutionError("Terraform failed", logs=logs)
        
        assert exc.message == "Terraform failed"
        assert exc.logs == logs

    def test_exception_is_exception_instance(self):
        """Exception is instance of Exception."""
        exc = TerraformExecutionError("Error")
        assert isinstance(exc, Exception)

    def test_exception_str_representation(self):
        """Exception string representation contains message."""
        exc = TerraformExecutionError("Terraform apply failed")
        
        assert str(exc) == "Terraform apply failed"

    def test_exception_can_be_raised_and_caught(self):
        """Exception can be raised and caught."""
        with pytest.raises(TerraformExecutionError) as exc_info:
            raise TerraformExecutionError("Test error")
        
        assert exc_info.value.message == "Test error"

    def test_exception_can_be_caught_as_exception(self):
        """Exception can be caught as generic Exception."""
        with pytest.raises(Exception) as exc_info:
            raise TerraformExecutionError("Test error")
        
        assert isinstance(exc_info.value, TerraformExecutionError)

    def test_exception_with_empty_logs(self):
        """Exception can have empty logs string."""
        exc = TerraformExecutionError("Error", logs="")
        
        assert exc.logs == ""

    def test_exception_with_multiline_logs(self):
        """Exception handles multiline logs correctly."""
        logs = """
        Error: Configuration error
        
          on main.tf line 5:
           5: resource "docker_container" "test"
        
        Error: Another error
        """
        exc = TerraformExecutionError("Multiple errors", logs=logs)
        
        assert "Error:" in exc.logs
        assert "main.tf" in exc.logs

    def test_exception_attributes_are_accessible(self):
        """Exception attributes are directly accessible."""
        exc = TerraformExecutionError("Test", logs="Log output")
        
        assert hasattr(exc, 'message')
        assert hasattr(exc, 'logs')
        assert exc.message == "Test"
        assert exc.logs == "Log output"


class TestResourceConflictError:
    """Tests for ResourceConflictError exception."""

    def test_exception_attributes(self):
        """Exception stores resource type and identifier."""
        exc = ResourceConflictError("VM", "test-vm")
        
        assert exc.resource_type == "VM"
        assert exc.identifier == "test-vm"

    def test_exception_message(self):
        """Exception message contains resource info."""
        exc = ResourceConflictError("Network", "net-1")
        
        assert "Network" in str(exc)
        assert "net-1" in str(exc)
        assert "already exists" in str(exc)


class TestResourceNotFoundError:
    """Tests for ResourceNotFoundError exception."""

    def test_exception_attributes(self):
        """Exception stores resource type and identifier."""
        exc = ResourceNotFoundError("VM", "missing-vm")
        
        assert exc.resource_type == "VM"
        assert exc.identifier == "missing-vm"

    def test_exception_message(self):
        """Exception message contains resource info."""
        exc = ResourceNotFoundError("Network", "net-1")
        
        assert "Network" in str(exc)
        assert "net-1" in str(exc)
        assert "not found" in str(exc)


class TestInvalidStateTransitionError:
    """Tests for InvalidStateTransitionError exception."""

    def test_exception_attributes(self):
        """Exception stores state transition info."""
        exc = InvalidStateTransitionError("running", "pending", ["stopped"])
        
        assert exc.current_state == "running"
        assert exc.target_state == "pending"
        assert exc.allowed_transitions == ["stopped"]

    def test_exception_message(self):
        """Exception message contains transition info."""
        exc = InvalidStateTransitionError("error", "running", ["pending"])
        
        assert "running" in str(exc)
        assert "error" in str(exc)
        assert "pending" in str(exc)


class TestRateLimitExceededError:
    """Tests for RateLimitExceededError exception."""

    def test_exception_attributes(self):
        """Exception stores retry_after value."""
        exc = RateLimitExceededError(60)
        
        assert exc.retry_after == 60

    def test_exception_message(self):
        """Exception message contains retry info."""
        exc = RateLimitExceededError(30)
        
        assert "30" in str(exc)
        assert "Rate limit" in str(exc)


class TestConfigurationError:
    """Tests for ConfigurationError exception."""

    def test_exception_message(self):
        """Exception stores and displays message."""
        exc = ConfigurationError("Missing required setting")
        
        assert exc.message == "Missing required setting"
        assert "Missing required setting" in str(exc)


class TestProviderUnavailableError:
    """Tests for ProviderUnavailableError exception."""

    def test_exception_attributes(self):
        """Exception stores provider and detail."""
        exc = ProviderUnavailableError("Redis", "Connection refused")
        
        assert exc.provider == "Redis"
        assert exc.detail == "Connection refused"

    def test_exception_message(self):
        """Exception message contains provider info."""
        exc = ProviderUnavailableError("Celery", "Broker unavailable")
        
        assert "Celery" in str(exc)
        assert "unavailable" in str(exc)

    def test_exception_with_no_detail(self):
        """Exception handles missing detail."""
        exc = ProviderUnavailableError("Docker")
        
        assert exc.provider == "Docker"
        assert exc.detail is None
