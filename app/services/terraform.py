import os
import tempfile
import subprocess
import shutil
import logging
import re
import stat
from pathlib import Path
from typing import Dict, Tuple, Optional
from urllib.parse import urlparse, urlunparse, parse_qs
from app.core.websocket import publish_log_update
from jinja2 import Environment, FileSystemLoader
from app.core.config import settings


def _get_pg_env_from_url(database_url: str) -> Dict[str, str]:
    """Parse DATABASE_URL and return PostgreSQL environment variables for Terraform."""
    parsed = urlparse(database_url)
    pg_env = {
        'PGHOST': parsed.hostname or 'localhost',
        'PGPORT': str(parsed.port or 5432),
        'PGUSER': parsed.username or '',
        'PGPASSWORD': parsed.password or '',
        'PGDATABASE': parsed.path.lstrip('/') or 'postgres',
    }
    query_params = parse_qs(parsed.query)
    if 'sslmode' in query_params:
        pg_env['PGSSLMODE'] = query_params['sslmode'][0]
    else:
        pg_env['PGSSLMODE'] = 'disable'
    return pg_env
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

SENSITIVE_KEYS = [
    'backend_conn_str', 'password', 'secret', 'token', 'api_key', 'conn_str', 
    'database_url', 'db_password', 'db_user', 'auth', 'credential', 'private_key',
    'access_key', 'secret_key', 'session_token', 'refresh_token', 'api_secret',
    'encryption_key', 'jwt_secret', 'ssh_key', 'ssl_key', 'cert', 'vcenter_password',
    'vc_password', 'vsphere_password', 'redis_url', 'broker_url', 'result_backend'
]
TERRAFORM_TIMEOUT = 3600
TERRAFORM_INIT_TIMEOUT = 300
TERRAFORM_WORKSPACE_TIMEOUT = 30
TERRAFORM_OUTPUT_TIMEOUT = 30


def _mask_connection_string(conn_str: str) -> str:
    """
    Masks password in connection strings for safe logging.
    Input: postgresql://user:password@host:port/db
    Output: postgresql://user:***@host:port/db
    """
    if not conn_str:
        return ""
    
    try:
        parsed = urlparse(conn_str)
        if parsed.password:
            masked_netloc = f"{parsed.username}:***@{parsed.hostname}"
            if parsed.port:
                masked_netloc += f":{parsed.port}"
            masked = urlunparse((
                parsed.scheme,
                masked_netloc,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment
            ))
            return masked
    except Exception:
        pass
    
    return re.sub(r'://([^:]+):([^@]+)@', r'://\1:***@', conn_str)


def sanitize_for_logging(data: Dict) -> Dict:
    """
    Mask sensitive values in dictionaries before logging.
    """
    sanitized = {}
    for key, value in data.items():
        key_lower = key.lower()
        if any(sensitive in key_lower for sensitive in SENSITIVE_KEYS):
            sanitized[key] = '***REDACTED***'
        elif isinstance(value, str):
            if 'postgresql://' in value or 'mysql://' in value or 'redis://' in value:
                sanitized[key] = re.sub(r'://([^:]+):([^@]+)@', r'://\1:***@', value)
            else:
                sanitized[key] = value
        else:
            sanitized[key] = value
    return sanitized


def get_safe_variable_name(key: str) -> str:
    """
    Returns a display-safe version of variable name for logging.
    """
    key_lower = key.lower()
    if any(sensitive in key_lower for sensitive in SENSITIVE_KEYS):
        return f"{key} (redacted)"
    return key


def _create_secure_temp_dir(prefix: str) -> str:
    """
    Creates a secure temporary directory with restricted permissions.
    Uses the configured TERRAFORM_TEMP_DIR if set, otherwise system default.
    """
    base_dir = settings.TERRAFORM_TEMP_DIR if settings.TERRAFORM_TEMP_DIR else None
    
    if base_dir and not os.path.exists(base_dir):
        os.makedirs(base_dir, mode=0o700, exist_ok=True)
    
    job_dir = tempfile.mkdtemp(prefix=prefix, dir=base_dir)
    
    os.chmod(job_dir, stat.S_IRWXU)
    
    logger.debug(f"Created secure temp directory: {job_dir}")
    return job_dir


def _secure_cleanup(job_dir: str, preserve_logs: bool = False, error_msg: Optional[str] = None) -> None:
    """
    Securely cleans up a job directory.
    If preserve_logs is True and error_msg is provided, preserves logs temporarily.
    """
    if not job_dir or not os.path.exists(job_dir):
        return
    
    if preserve_logs and error_msg:
        try:
            log_file = os.path.join(job_dir, "terraform_error.log")
            with open(log_file, "w") as f:
                f.write(f"Terraform job error:\n")
                f.write(f"Error: {error_msg}\n")
            logger.info(f"Error logs preserved at: {job_dir}")
            return
        except Exception as e:
            logger.warning(f"Could not preserve error logs: {e}")
    
    try:
        for root, dirs, files in os.walk(job_dir):
            for d in dirs:
                dir_path = os.path.join(root, d)
                os.chmod(dir_path, stat.S_IRWXU)
            for f in files:
                file_path = os.path.join(root, f)
                os.chmod(file_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR if os.access(file_path, os.X_OK) else stat.S_IRUSR | stat.S_IWUSR)
        
        shutil.rmtree(job_dir, ignore_errors=True)
        logger.debug(f"Securely cleaned up temp directory: {job_dir}")
    except Exception as e:
        logger.warning(f"Failed to securely cleanup {job_dir}: {e}")
        shutil.rmtree(job_dir, ignore_errors=True)


TEMPLATES_DIR = Path(__file__).parent.parent / "terraform" / "templates"
INIT_DIR = Path(__file__).parent.parent / "terraform" / "init"

tf_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
init_env = Environment(loader=FileSystemLoader(str(INIT_DIR)))


def get_terraform_context(vm_request, vm_id: int, db: Optional[Session] = None) -> Tuple[str, Dict]:
    """
    Determines template and variables based on provider.
    Returns: (template_filename, variables_dict)
    Note: backend_conn_str is NOT included - passed via TF_BACKEND_CONN_STR env var
    """
    provider = vm_request.provider
    logger.info(f"Preparing context for provider: {provider}")

    variables = {}
    variables["vm_id"] = vm_id
    variables["vm_name"] = vm_request.name

    if provider == "docker":
        variables["memory_mb"] = vm_request.ram 
        variables["image_name"] = getattr(vm_request, 'image', None) or settings.DEFAULT_VM_IMAGE
        variables["cpu_shares"] = vm_request.cpu * 1024
        variables["external_port"] = 8000 + vm_id
        
        # Docker network support removed - using TenantNetwork for Proxmox
        # network_id on VMs is deprecated
        variables["enable_networking"] = False

        firewall_rules = []
        if hasattr(vm_request, 'firewall_rules') and vm_request.firewall_rules:
            firewall_rules = list(vm_request.firewall_rules)
            if firewall_rules and isinstance(firewall_rules[0], dict):
                firewall_rules = sorted(firewall_rules, key=lambda x: x.get('priority', 100))
        
        outer_rules = []
        inner_rules = []
        for r in firewall_rules:
            layer = r.get('layer', 'both')
            if layer in ('outer', 'both'):
                outer_rules.append(r)
            if layer in ('inner', 'both'):
                inner_rules.append(r)
        
        variables["firewall_outer_rules"] = outer_rules
        variables["firewall_inner_rules"] = inner_rules
        
        return "docker.tf.j2", variables

    elif provider == "proxmox":
        variables["template_id"] = getattr(vm_request, 'template_id', None) or 9000
        variables["memory_mb"] = vm_request.ram 
        variables["cpu_cores"] = vm_request.cpu
        variables["vlan_id"] = getattr(vm_request, 'vlan_id', None) or 100
        variables["proxmox_url"] = settings.PROXMOX_URL
        variables["proxmox_username"] = settings.PROXMOX_USERNAME
        variables["proxmox_password"] = settings.PROXMOX_PASSWORD
        variables["proxmox_node"] = settings.PROXMOX_NODE
        variables["root_password"] = getattr(vm_request, 'root_password', None) or "change-me"
        
        return "proxmox_clone.tf.j2", variables

    else:
        raise ValueError(f"Unsupported provider: {provider}")


def render_init_script(**kwargs) -> str:
    """Renders the Cloud-Init script."""
    try:
        template = init_env.get_template("user-data.sh.j2")
        return template.render(**kwargs)
    except Exception as e:
        logger.warning(f"Could not render init script: {e}")
        return ""


def render_terraform_code(template_name: str, variables: Dict) -> str:
    """Renders the Terraform HCL file."""
    logger.debug(f"Rendering template: {template_name}")
    template = tf_env.get_template(template_name)
    return template.render(**variables)


def _format_tf_value(val):
    """Format a value for Terraform tfvars file."""
    import json
    
    if isinstance(val, str):
        return f'"{val}"'
    elif isinstance(val, bool):
        return "true" if val else "false"
    elif isinstance(val, (int, float)):
        return str(val)
    elif isinstance(val, (list, dict)):
        return json.dumps(val)
    else:
        return f'"{val}"'


def run_terraform_job(identifier: int, name: str, tf_code: str, variables: Dict, workspace_prefix: str = "vm") -> Dict:
    """
    Executes Terraform with Real-Time Logging.
    Credentials are passed via environment variables, never in templates.
    Uses secure temp directories with restricted permissions.
    """
    logger.info(f"Starting Terraform job for {name}")
    job_dir = _create_secure_temp_dir(f"terraform_{name}_")
    
    publish_log_update(identifier, f"Initializing Terraform environment for {name}...")
    
    try:
        tf_file_path = os.path.join(job_dir, "main.tf")
        with open(tf_file_path, "w") as f:
            f.write(tf_code)
        os.chmod(tf_file_path, stat.S_IRUSR | stat.S_IWUSR)
        
        tfvars_lines = [f'{k} = {_format_tf_value(v)}' for k, v in variables.items() 
                       if k != 'backend_conn_str']
        tfvars_content = "\n".join(tfvars_lines)
        tfvars_path = os.path.join(job_dir, "terraform.tfvars")
        with open(tfvars_path, "w") as f:
            f.write(tfvars_content)
        os.chmod(tfvars_path, stat.S_IRUSR | stat.S_IWUSR)
            
        logger.info(f"--- Terraform Variables for {name} ---")
        safe_vars = {k: v for k, v in variables.items() if k != 'backend_conn_str'}
        logger.info(f"Variables: {sanitize_for_logging(safe_vars)}")
        logger.info("------------------------------------------")

        safe_env = {
            'PATH': os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin'),
            'HOME': os.environ.get('HOME', '/root'),
            'USER': os.environ.get('USER', 'root'),
            'LANG': os.environ.get('LANG', 'en_US.UTF-8'),
            'LC_ALL': os.environ.get('LC_ALL', 'en_US.UTF-8'),
            'TF_IN_AUTOMATION': 'true',
            'TF_CLI_ARGS': '-no-color',
        }
        
        # Terraform PostgreSQL backend uses TF_BACKEND_CONN_STR for connection
        if settings.DATABASE_URL:
            safe_env['TF_BACKEND_CONN_STR'] = settings.DATABASE_URL
        
        pg_env = _get_pg_env_from_url(settings.DATABASE_URL)
        safe_env.update(pg_env)

        if settings.VCENTER_SERVER:
            safe_env['VSPHERE_SERVER'] = settings.VCENTER_SERVER
            safe_env['VSPHERE_USER'] = settings.VCENTER_USER
            safe_env['VSPHERE_PASSWORD'] = settings.VCENTER_PASSWORD
        
        env = safe_env

        logger.info(f"Running: terraform init in {job_dir}")
        publish_log_update(identifier, "Running terraform init...")

        process = subprocess.Popen(
            ["terraform", "init"], 
            cwd=job_dir, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT,
            text=True,
            env=env
        )
        
        output_lines = []
        try:
            for line in process.stdout:
                clean_line = line.strip()
                output_lines.append(clean_line)
                logger.info(f"[TF INIT] {clean_line}")
                publish_log_update(identifier, f"[INIT] {clean_line}")

            
            process.wait(timeout=TERRAFORM_INIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise Exception(f"Terraform init timed out after {TERRAFORM_INIT_TIMEOUT} seconds")
        
        if process.returncode != 0:
            error_msg = f"Terraform init failed with code {process.returncode}. Output:\n" + "\n".join(output_lines)
            logger.error(error_msg)
            publish_log_update(identifier, f"[ERROR] {error_msg}")
            raise Exception(error_msg)

        workspace_name = f"{workspace_prefix}_{identifier}"
        publish_log_update(identifier, f"Preparing workspace: {workspace_name}")
        
        try:
            subprocess.run(
                ["terraform", "workspace", "new", workspace_name],
                cwd=job_dir,
                check=True,
                capture_output=True,
                env=env,
                timeout=TERRAFORM_WORKSPACE_TIMEOUT
            )
            logger.info(f"Created new workspace: {workspace_name}")
        except subprocess.CalledProcessError:
            logger.info(f"Workspace {workspace_name} already exists. Selecting it.")

        subprocess.run(
            ["terraform", "workspace", "select", workspace_name],
            cwd=job_dir,
            check=True,
            capture_output=True,
            env=env,
            timeout=TERRAFORM_WORKSPACE_TIMEOUT
        )
        logger.info(f"Selected workspace: {workspace_name}")

        logger.info(f"Running: terraform apply -auto-approve -var-file=terraform.tfvars")
        publish_log_update(identifier, "Running terraform apply...")
        
        process_apply = subprocess.Popen(
            ["terraform", "apply", "-auto-approve", f"-var-file={tfvars_path}"], 
            cwd=job_dir, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT,
            text=True,
            env=env
        )

        while True:
            output = process_apply.stdout.readline()
            if output == '' and process_apply.poll() is not None:
                break
            if output:
                clean_line = output.strip()
                logger.info(f"[TF APPLY] {clean_line}")
                # SEND TO WEBSOCKET
                try:
                    publish_log_update(identifier, clean_line)
                except Exception:
                    pass # Avoid crashing task if Redis fails

        return_code = process_apply.poll()

        if return_code != 0:
            logger.error(f"Terraform apply failed with exit code {return_code}")
            publish_log_update(identifier, f"Error: Terraform apply failed with code {return_code}")
            raise Exception("Terraform apply failed. Check logs for details.")
            
        logger.info(f"Terraform apply successful for {name}. Fetching outputs...")
        publish_log_update(identifier, f"Terraform apply successful for {name}. Fetching outputs...")

        outputs = {}
        
        def get_output(output_name):
            try:
                res = subprocess.run(
                    ["terraform", "output", "-raw", output_name],
                    cwd=job_dir,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=30
                )
                return res.stdout.strip()
            except Exception:
                logger.warning(f"Could not fetch output {output_name}")
                return None

        outputs["ip"] = get_output("vm_internal_ip")
        outputs["port"] = get_output("vm_external_port")

        logger.info(f"Job finished. IP: {outputs['ip']}, Port: {outputs['port']}")
        publish_log_update(identifier, f"Job finished. IP: {outputs['ip']}, Port: {outputs['port']}")
        
        _secure_cleanup(job_dir)
        
        return {"status": "success", "outputs": outputs}
        
    except Exception as e:
        logger.error(f"Job failed: {e}")
        _secure_cleanup(job_dir, preserve_logs=True, error_msg=str(e))
        raise


def destroy_terraform_job(identifier: int, name: str, tf_code: str, variables: Dict, workspace_prefix: str = "vm") -> Dict:
    """
    Executes Terraform Destroy to remove infrastructure.
    Credentials are passed via environment variables, never in templates.
    Uses secure temp directories with restricted permissions.
    """
    logger.info(f"Starting Terraform DESTROY for {name}")
    publish_log_update(identifier, f"Starting destruction of network {name}...")
    job_dir = _create_secure_temp_dir(f"terraform_destroy_{name}_")
    
    try:
        tf_file_path = os.path.join(job_dir, "main.tf")
        with open(tf_file_path, "w") as f:
            f.write(tf_code)
        os.chmod(tf_file_path, stat.S_IRUSR | stat.S_IWUSR)
        
        tfvars_lines = [f'{k} = {_format_tf_value(v)}' for k, v in variables.items()
                       if k != 'backend_conn_str']
        tfvars_content = "\n".join(tfvars_lines)
        tfvars_path = os.path.join(job_dir, "terraform.tfvars")
        with open(tfvars_path, "w") as f:
            f.write(tfvars_content)
        os.chmod(tfvars_path, stat.S_IRUSR | stat.S_IWUSR)

        # Start with a copy of the current environment to preserve system variables
        # needed for DNS resolution on Windows
        safe_env = {
            'PATH': os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin'),
            'HOME': os.environ.get('HOME', '/root'),
            'USER': os.environ.get('USER', 'root'),
            'LANG': os.environ.get('LANG', 'en_US.UTF-8'),
            'LC_ALL': os.environ.get('LC_ALL', 'en_US.UTF-8'),
            'TF_IN_AUTOMATION': 'true',
            'TF_CLI_ARGS': '-no-color',
        }
        
        # Terraform PostgreSQL backend uses TF_BACKEND_CONN_STR for connection
        if settings.DATABASE_URL:
            safe_env['TF_BACKEND_CONN_STR'] = settings.DATABASE_URL
        
        pg_env = _get_pg_env_from_url(settings.DATABASE_URL)
        safe_env.update(pg_env)

        if settings.VCENTER_SERVER:
            safe_env['VSPHERE_SERVER'] = settings.VCENTER_SERVER
            safe_env['VSPHERE_USER'] = settings.VCENTER_USER
            safe_env['VSPHERE_PASSWORD'] = settings.VCENTER_PASSWORD
        
        env = safe_env

        logger.info(f"Running: terraform init in {job_dir}")
        process = subprocess.Popen(
            ["terraform", "init"], 
            cwd=job_dir, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT,
            text=True,
            env=env
        )
        
        output_lines = []
        try:
            for line in process.stdout:
                output_lines.append(line.strip())
                logger.info(f"[TF INIT] {line.strip()}")
            
            process.wait(timeout=TERRAFORM_INIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise Exception(f"Terraform init timed out after {TERRAFORM_INIT_TIMEOUT} seconds")
        
        if process.returncode != 0:
            error_msg = f"Terraform init failed with code {process.returncode}. Output:\n" + "\n".join(output_lines)
            logger.error(error_msg)
            raise Exception(error_msg)

        workspace_name = f"{workspace_prefix}_{identifier}"
        publish_log_update(identifier, f"Preparing workspace: {workspace_name}")
        
        # Check if workspace exists, if not skip destroy (nothing to destroy)
        ws_check = subprocess.run(
            ["terraform", "workspace", "list"],
            cwd=job_dir,
            capture_output=True,
            text=True,
            env=env,
            timeout=TERRAFORM_WORKSPACE_TIMEOUT
        )
        
        if workspace_name not in ws_check.stdout:
            logger.warning(f"Workspace {workspace_name} does not exist. Nothing to destroy.")
            return {"status": "destroyed", "message": "Workspace does not exist, nothing to destroy"}
        
        subprocess.run(
            ["terraform", "workspace", "select", workspace_name],
            cwd=job_dir,
            check=True,
            capture_output=True,
            env=env,
            timeout=TERRAFORM_WORKSPACE_TIMEOUT
        )
        logger.info(f"Selected workspace for destruction: {workspace_name}")

        publish_log_update(identifier, "Running terraform destroy...")
        logger.warning(f"Running: terraform destroy -auto-approve for {name}")
        process_destroy = subprocess.Popen(
            ["terraform", "destroy", "-auto-approve", f"-var-file={tfvars_path}"], 
            cwd=job_dir, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT,
            text=True,
            env=env
        )

        while True:
            output = process_destroy.stdout.readline()
            if output == '' and process_destroy.poll() is not None:
                break
            if output:
                clean_line = output.strip()
                if clean_line:
                    publish_log_update(identifier, clean_line)
                logger.info(f"[TF DESTROY] {output.strip()}")

        return_code = process_destroy.poll()

        if return_code != 0:
            logger.error(f"Terraform destroy failed with exit code {return_code}")
            raise Exception("Terraform destroy failed. Check logs for details.")
            
        logger.info(f"Terraform destroy successful for {name}")
        publish_log_update(identifier, "Network destruction successful.")
        
        _secure_cleanup(job_dir)
        
        return {"status": "destroyed"}
        
    except Exception as e:
        logger.error(f"Destroy job failed: {e}")
        _secure_cleanup(job_dir, preserve_logs=True, error_msg=str(e))
        raise


def get_network_terraform_context(
    network_name: str, 
    cidr: str, 
    network_id: int, 
    provider: str = "docker"
) -> Tuple[str, Dict]:
    """
    Generates Terraform context for network infrastructure.
    Used by deploy_network_task and destroy_network_task.
    
    Returns: (template_filename, variables_dict)
    Note: backend_conn_str is NOT included - passed via TF_BACKEND_CONN_STR env var
    """
    import ipaddress
    
    try:
        net_obj = ipaddress.ip_network(cidr, strict=False)
        normalized_cidr = str(net_obj)
    except ValueError:
        normalized_cidr = cidr
    
    variables = {
        "network_name": network_name,
        "subnet_cidr": normalized_cidr,
    }
    
    if provider == "docker":
        return "docker_network.tf.j2", variables
    
    raise ValueError(f"Unsupported provider for networks: {provider}")


def get_terraform_output(job_dir: str, output_name: str) -> Optional[str]:
    """
    Retrieves a specific output value from Terraform state.
    Useful for getting the actual IP address after provisioning.
    """
    try:
        result = subprocess.run(
            ["terraform", "output", "-raw", output_name],
            cwd=job_dir,
            capture_output=True,
            text=True,
            check=True,
            timeout=30
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        logger.warning(f"Could not retrieve output '{output_name}': {e}")
        return None
