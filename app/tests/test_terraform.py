import pytest
from unittest.mock import patch, MagicMock
from app.services import terraform


pytestmark = pytest.mark.unit


class TestMaskConnectionString:
    """Tests for _mask_connection_string function."""

    def test_masks_password_in_postgresql(self):
        """Masks password in postgresql connection string."""
        conn_str = "postgresql://user:secretpassword@localhost:5432/mydb"
        result = terraform._mask_connection_string(conn_str)
        assert "secretpassword" not in result
        assert "***" in result

    def test_masks_password_in_mysql(self):
        """Masks password in mysql connection string."""
        conn_str = "mysql://user:mypassword@localhost:3306/mydb"
        result = terraform._mask_connection_string(conn_str)
        assert "mypassword" not in result
        assert "***" in result

    def test_handles_no_password(self):
        """Handles connection string without password."""
        conn_str = "postgresql://user@localhost:5432/mydb"
        result = terraform._mask_connection_string(conn_str)
        assert "user" in result

    def test_handles_empty_string(self):
        """Handles empty string."""
        result = terraform._mask_connection_string("")
        assert result == ""

    def test_handles_none(self):
        """Handles None input."""
        result = terraform._mask_connection_string(None)
        assert result == ""


class TestSanitizeForLogging:
    """Tests for sanitize_for_logging function."""

    def test_masks_password_key(self):
        """Masks value for keys containing password."""
        data = {"password": "secret123"}
        result = terraform.sanitize_for_logging(data)
        assert result["password"] == "***REDACTED***"

    def test_masks_secret_key(self):
        """Masks value for keys containing secret."""
        data = {"api_secret": "mysecret"}
        result = terraform.sanitize_for_logging(data)
        assert result["api_secret"] == "***REDACTED***"

    def test_masks_token_key(self):
        """Masks value for keys containing token."""
        data = {"auth_token": "mytoken"}
        result = terraform.sanitize_for_logging(data)
        assert result["auth_token"] == "***REDACTED***"

    def test_masks_database_url(self):
        """Masks database URL password."""
        data = {"database_url": "postgresql://user:password@localhost/db"}
        result = terraform.sanitize_for_logging(data)
        assert "password" not in result["database_url"]

    def test_masks_redis_url(self):
        """Masks redis URL password."""
        data = {"redis_url": "redis://:password@localhost/0"}
        result = terraform.sanitize_for_logging(data)
        assert "password" not in result["redis_url"]

    def test_passes_through_non_sensitive(self):
        """Passes through non-sensitive values."""
        data = {"name": "test-vm", "cpu": 2}
        result = terraform.sanitize_for_logging(data)
        assert result["name"] == "test-vm"
        assert result["cpu"] == 2


class TestGetSafeVariableName:
    """Tests for get_safe_variable_name function."""

    def test_returns_key_for_safe_name(self):
        """Returns key unchanged for safe variable names."""
        result = terraform.get_safe_variable_name("vm_name")
        assert result == "vm_name"

    def test_marks_password_as_redacted(self):
        """Marks password key as redacted."""
        result = terraform.get_safe_variable_name("db_password")
        assert "(redacted)" in result

    def test_marks_secret_as_redacted(self):
        """Marks secret key as redacted."""
        result = terraform.get_safe_variable_name("api_secret")
        assert "(redacted)" in result


class TestGetPgEnvFromUrl:
    """Tests for _get_pg_env_from_url function."""

    def test_parses_basic_url(self):
        """Parses basic PostgreSQL URL."""
        url = "postgresql://user:password@localhost:5432/mydb"
        result = terraform._get_pg_env_from_url(url)
        
        assert result["PGHOST"] == "localhost"
        assert result["PGPORT"] == "5432"
        assert result["PGUSER"] == "user"
        assert result["PGPASSWORD"] == "password"
        assert result["PGDATABASE"] == "mydb"

    def test_uses_default_port(self):
        """Uses default port when not specified."""
        url = "postgresql://user:password@localhost/mydb"
        result = terraform._get_pg_env_from_url(url)
        
        assert result["PGPORT"] == "5432"

    def test_includes_sslmode(self):
        """Includes sslmode from query params."""
        url = "postgresql://user:password@localhost/mydb?sslmode=require"
        result = terraform._get_pg_env_from_url(url)
        
        assert result["PGSSLMODE"] == "require"

    def test_defaults_sslmode(self):
        """Defaults sslmode to disable when not specified."""
        url = "postgresql://user:password@localhost/mydb"
        result = terraform._get_pg_env_from_url(url)
        
        assert result["PGSSLMODE"] == "disable"


class TestGetTerraformContext:
    """Tests for get_terraform_context function."""

    def test_docker_provider_returns_docker_template(self):
        """Docker provider returns docker.tf.j2 template."""
        mock_request = MagicMock()
        mock_request.provider = "docker"
        mock_request.name = "test-vm"
        mock_request.ram = 4096
        mock_request.cpu = 2
        mock_request.network_id = None
        
        mock_db = MagicMock()
        
        template_name, variables = terraform.get_terraform_context(mock_request, 1, mock_db)
        
        assert template_name == "docker.tf.j2"
        assert "vm_id" in variables
        assert "vm_name" in variables

    def test_unsupported_provider_raises(self):
        """Unsupported provider raises ValueError."""
        mock_request = MagicMock()
        mock_request.provider = "unsupported"
        mock_request.name = "test-vm"
        mock_request.ram = 4096
        mock_request.cpu = 2
        mock_request.network_id = None
        
        mock_db = MagicMock()
        
        with pytest.raises(ValueError) as exc_info:
            terraform.get_terraform_context(mock_request, 1, mock_db)
        
        assert "unsupported" in str(exc_info.value).lower()

    def test_network_context_included(self):
        """Network context included when network_id is set."""
        mock_request = MagicMock()
        mock_request.provider = "docker"
        mock_request.name = "test-vm"
        mock_request.ram = 4096
        mock_request.cpu = 2
        mock_request.network_id = 1
        
        mock_network = MagicMock()
        mock_network.name = "test-network"
        
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_network
        
        template_name, variables = terraform.get_terraform_context(mock_request, 1, mock_db)
        
        assert variables["enable_networking"] is True
        assert variables["network_name"] == "test-network"

    def test_no_network_disables_networking(self):
        """Networking disabled when no network_id."""
        mock_request = MagicMock()
        mock_request.provider = "docker"
        mock_request.name = "test-vm"
        mock_request.ram = 4096
        mock_request.cpu = 2
        mock_request.network_id = None
        
        mock_db = MagicMock()
        
        template_name, variables = terraform.get_terraform_context(mock_request, 1, mock_db)
        
        assert variables["enable_networking"] is False


class TestRenderTerraformCode:
    """Tests for render_terraform_code function."""

    def test_render_returns_string(self):
        """Render returns a string."""
        with patch('app.services.terraform.tf_env') as mock_env:
            mock_template = MagicMock()
            mock_template.render.return_value = "rendered terraform code"
            mock_env.get_template.return_value = mock_template
            
            result = terraform.render_terraform_code("docker.tf.j2", {"vm_name": "test"})
            
            assert isinstance(result, str)

    def test_render_calls_template_with_variables(self):
        """Render calls template with provided variables."""
        with patch('app.services.terraform.tf_env') as mock_env:
            mock_template = MagicMock()
            mock_template.render.return_value = "rendered"
            mock_env.get_template.return_value = mock_template
            
            variables = {"vm_name": "test", "cpu": 2}
            terraform.render_terraform_code("docker.tf.j2", variables)
            
            mock_template.render.assert_called_once_with(**variables)


class TestRenderInitScript:
    """Tests for render_init_script function."""

    def test_render_init_script_returns_string(self):
        """Returns rendered script string."""
        with patch('app.services.terraform.init_env') as mock_env:
            mock_template = MagicMock()
            mock_template.render.return_value = "#!/bin/bash\necho hello"
            mock_env.get_template.return_value = mock_template
            
            result = terraform.render_init_script(package="nginx")
            
            assert isinstance(result, str)

    def test_render_init_script_error_returns_empty(self):
        """Returns empty string on error."""
        with patch('app.services.terraform.init_env') as mock_env:
            mock_env.get_template.side_effect = Exception("Template not found")
            
            result = terraform.render_init_script()
            
            assert result == ""


class TestFormatTfValue:
    """Tests for value formatting in Terraform."""

    def test_string_format(self):
        """String values are quoted."""
        assert terraform._format_tf_value("test") == '"test"'

    def test_integer_format(self):
        """Integer values are returned as strings."""
        assert terraform._format_tf_value(123) == "123"

    def test_float_format(self):
        """Float values are returned as strings."""
        assert terraform._format_tf_value(1.5) == "1.5"

    def test_true_bool_format(self):
        """True is formatted as 'true'."""
        assert terraform._format_tf_value(True) == "true"

    def test_false_bool_format(self):
        """False is formatted as 'false'."""
        assert terraform._format_tf_value(False) == "false"

    def test_list_format(self):
        """Lists are formatted as quoted strings."""
        result = terraform._format_tf_value([1, 2, 3])
        assert '"' in result


class TestGetNetworkTerraformContext:
    """Tests for get_network_terraform_context function."""

    @patch('app.services.terraform.render_terraform_code')
    def test_get_network_terraform_context_docker(self, mock_render):
        """Returns docker network template for docker provider."""
        mock_render.return_value = "terraform code"
        
        template_name, variables = terraform.get_network_terraform_context(
            network_name="test-net",
            cidr="172.20.0.0/16",
            network_id=1,
            provider="docker"
        )
        
        assert "docker" in template_name
        assert variables["network_name"] == "test-net"
        assert variables["subnet_cidr"] == "172.20.0.0/16"

    def test_get_network_terraform_context_invalid_provider(self):
        """Raises error for invalid provider."""
        with pytest.raises(ValueError) as exc_info:
            terraform.get_network_terraform_context(
                network_name="test-net",
                cidr="172.20.0.0/16",
                network_id=1,
                provider="invalid"
            )
        
        assert "unsupported" in str(exc_info.value).lower()


class TestSecureCleanup:
    """Tests for secure cleanup function."""

    def test_secure_cleanup_handles_nonexistent(self):
        """Handles nonexistent directory."""
        with patch('app.services.terraform.os.path.exists', return_value=False):
            result = terraform._secure_cleanup("/nonexistent")
        
        assert result is None


class TestRenderTerraformCodeWithMocks:
    """Tests for render_terraform_code function with mocks."""

    @patch('app.services.terraform.tf_env')
    def test_render_terraform_code(self, mock_env):
        """Renders terraform code with variables."""
        mock_template = MagicMock()
        mock_template.render.return_value = "rendered code"
        mock_env.get_template.return_value = mock_template
        
        result = terraform.render_terraform_code("test.tf.j2", {"var": "val"})
        
        assert result == "rendered code"
        mock_template.render.assert_called_once_with(var="val")

    @patch('app.services.terraform.tf_env')
    def test_render_terraform_code_with_backend(self, mock_env):
        """Passes backend_conn_str to template."""
        mock_template = MagicMock()
        mock_template.render.return_value = "rendered"
        mock_env.get_template.return_value = mock_template
        
        terraform.render_terraform_code("test.tf.j2", {"var": "val", "backend_conn_str": "conn"})
        
        call_kwargs = mock_template.render.call_args[1]
        assert "backend_conn_str" in call_kwargs


class TestTerraformIntegration:
    """Integration tests for terraform service."""

    def test_sanitize_for_logging_ignores_nested(self):
        """Handles nested dictionaries (top-level only)."""
        data = {"config": {"password": "secret"}}
        result = terraform.sanitize_for_logging(data)
        assert "config" in result

    def test_sanitize_for_logging_empty_dict(self):
        """Handles empty dictionary."""
        result = terraform.sanitize_for_logging({})
        assert result == {}

    def test_mask_connection_string_mongodb(self):
        """Masks MongoDB connection string."""
        conn = "mongodb://user:pass@localhost:27017/db"
        result = terraform._mask_connection_string(conn)
        assert "pass" not in result

    def test_get_safe_variable_name_redis_key(self):
        """Redacts redis_url key."""
        result = terraform.get_safe_variable_name("redis_url")
        assert "(redacted)" in result

    def test_get_safe_variable_name_jwt_key(self):
        """Redacts jwt_secret key."""
        result = terraform.get_safe_variable_name("jwt_secret")
        assert "(redacted)" in result

    def test_get_safe_variable_name_ssl_key(self):
        """Redacts ssl_key key."""
        result = terraform.get_safe_variable_name("ssl_key")
        assert "(redacted)" in result

    def test_get_safe_variable_name_vcenter_password(self):
        """Redacts vcenter_password key."""
        result = terraform.get_safe_variable_name("vcenter_password")
        assert "(redacted)" in result

    def test_get_safe_variable_name_conn_str(self):
        """Redacts conn_str key."""
        result = terraform.get_safe_variable_name("backend_conn_str")
        assert "(redacted)" in result

    def test_mask_connection_string_with_special_chars(self):
        """Masks connection string with special chars in password."""
        conn = "postgresql://user:p@ss:w0rd@localhost/db"
        result = terraform._mask_connection_string(conn)
        assert "p@ss:w0rd" not in result
