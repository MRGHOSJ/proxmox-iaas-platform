"""
VM deployment tasks.

Contains tasks for deploying and provisioning VMs.
"""
import ipaddress
import logging

from app.core.database import SessionLocal
from app.core.websocket import publish_status_update
from app.core.crypto import encrypt
from app.core.ssh import generate_ssh_keypair
from app.models.vm import VM
from app.models.network import TenantNetwork
from app.workers.celery_app import celery_app
from app.workers.tasks.helpers import (
    MAX_RETRIES, RETRY_DELAY, get_db, 
    _cleanup_vm_on_failure, _validate_vm_data,
    log_to_vm
)
from app.services.terraform import (
    get_terraform_context,
    render_terraform_code,
    run_terraform_job,
    destroy_terraform_job
)

logger = logging.getLogger(__name__)


def _attempt_terraform_rollback(vm_id: int, vm_name: str, tf_code: str, variables: dict):
    """Attempt to destroy any partial Terraform infrastructure on failure."""
    try:
        logger.warning(f"Attempting Terraform rollback for VM {vm_id} ({vm_name})")
        destroy_terraform_job(vm_id, vm_name, tf_code, variables, workspace_prefix="vm")
        logger.info(f"Terraform rollback successful for VM {vm_id}")
    except Exception as rollback_error:
        logger.error(
            f"Terraform rollback failed for VM {vm_id}. "
            f"Manual cleanup may be required. Error: {rollback_error}"
        )


@celery_app.task(name="tasks.deploy_vm", bind=True, max_retries=MAX_RETRIES, default_retry_delay=RETRY_DELAY)
def deploy_vm_task(self, vm_id: int, vm_data_dict: dict, terraform_context_dict: dict):
    """
    Background task to provision a VM with retry logic for IP conflicts.
    Implements proper state machine transitions and cleanup on failure.
    
    On failure, attempts Terraform rollback to clean up partial infrastructure.
    """
    logger.info(f"Task started: Deploying VM {vm_id}")
    
    try:
        _validate_vm_data(vm_data_dict)
    except ValueError as e:
        logger.error(f"Invalid VM data received: {e}")
        return {"status": "error", "error": f"Invalid VM data: {str(e)}"}
    
    db = SessionLocal()
    
    vm = None
    last_tf_code = None
    last_variables = None
    
    try:
        vm = db.query(VM).filter(VM.id == vm_id).first()
        if not vm:
            logger.error(f"VM {vm_id} not found")
            return {"status": "error", "error": "VM not found"}

        if vm.status == "error":
            old_status = vm.status
            vm.status = "pending"
            db.commit()
            
            try:
                publish_status_update("vm", vm.id, old_status, "pending")
            except Exception as ws_err:
                logger.warning(f"Failed to broadcast status change: {ws_err}")

        old_status = vm.status
        vm.status = "provisioning"
        db.commit()
        
        try:
            publish_status_update("vm", vm.id, old_status, "provisioning")
        except Exception as ws_err:
            logger.warning(f"Failed to broadcast status change: {ws_err}")
        
        class MockVMRequest:
            def __init__(self, **kwargs):
                for key, value in kwargs.items():
                    setattr(self, key, value)
        
        vm_request = MockVMRequest(**vm_data_dict)
        
        # Note: Docker network provisioning removed - using TenantNetwork for Proxmox VMs only
        if vm.network_id and vm.provider == "docker":
            logger.warning(f"Docker network provisioning is deprecated for VM {vm_id}")
            vm.status = "error"
            vm.error = "Docker network provisioning is no longer supported"
            db.commit()
            return {"status": "error", "error": "Docker network provisioning deprecated"}
        
        # Proxmox VM provisioning uses TenantNetwork via provider
        old_status = vm.status
        template_name, variables = get_terraform_context(vm_request, vm_id, db)  
        last_tf_code = render_terraform_code(template_name, variables)
        last_variables = variables
        result = run_terraform_job(vm_id, vm.name, last_tf_code, last_variables, workspace_prefix="vm")
        
        vm.status = "running"
        
        if result and "outputs" in result:
            outputs = result["outputs"]
            if outputs.get("ip"):
                vm.ip_address = outputs.get("ip")
                logger.info(f"VM {vm_id} assigned internal IP: {vm.ip_address}")
            elif outputs.get("port"):
                vm.ip_address = f"127.0.0.1:{outputs.get('port')}"
                logger.info(f"VM {vm_id} assigned external port: {outputs.get('port')}")
        
        db.commit()
        
        try:
            publish_status_update("vm", vm.id, old_status, "running", {"ip_address": vm.ip_address})
        except Exception as ws_err:
            logger.warning(f"Failed to broadcast status change: {ws_err}")
        
        logger.info(f"Task completed: VM {vm_id} is {vm.status}")
        return {"status": "success", "vm_id": vm_id}

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Task failed for VM {vm_id}: {error_msg}")
        
        if vm and last_tf_code and last_variables:
            _attempt_terraform_rollback(vm_id, vm.name, last_tf_code, last_variables)
        
        if vm:
            _cleanup_vm_on_failure(db, vm, error_msg)
        
        if self.request.retries < self.max_retries:
            logger.info(f"Retrying task (attempt {self.request.retries + 1}/{self.max_retries})...")
            raise self.retry(exc=e)
        
        return {"status": "error", "error": error_msg, "vm_id": vm_id}
    
    finally:
        db.close()


@celery_app.task(name="tasks.provision_vm", bind=True, max_retries=0, time_limit=600, soft_time_limit=540)
def provision_vm_task(self, vm_id: int, provision_data: dict):
    """
    Background task to provision a VM with cloud-init.
    Runs asynchronously via Celery with sanitized logging for tenant UI.
    """
    from app.providers import get_hypervisor_provider
    
    logger.info(f"Starting VM provisioning for VM {vm_id}")

    db = SessionLocal()
    
    try:
        vm = db.query(VM).filter(VM.id == vm_id).first()
        if not vm:
            logger.error(f"VM {vm_id} not found")
            return {"status": "error", "error": "VM not found"}
        
        log_to_vm(vm_id, "Starting VM provisioning...")
        
        log_to_vm(vm_id, "Allocating resources...")
        
        provider = get_hypervisor_provider()
        
        for attempt in range(3):
            try:
                next_id = provider._api("GET", "cluster/nextid")
                if isinstance(next_id, dict):
                    proxmox_vm_id = int(next_id.get("data", 0))
                else:
                    proxmox_vm_id = int(str(next_id))
                break
            except Exception as e:
                if attempt < 2:
                    log_to_vm(vm_id, f"Retrying resource allocation...")
                    continue
                raise Exception(f"Failed to allocate VM ID: {e}")
        
        # Save proxmox_vm_id immediately so it's persisted even if clone/config fails later
        vm.proxmox_vm_id = proxmox_vm_id
        db.commit()
        log_to_vm(vm_id, f"Proxmox VM ID {proxmox_vm_id} allocated and saved")

        ssh_public_key = provision_data.get("ssh_public_key")
        ssh_user = provision_data.get("username", "ubuntu")
        if not ssh_public_key:
            log_to_vm(vm_id, "Generating SSH key pair...")
            ssh_public_key, private_pem = generate_ssh_keypair(comment=f"{vm.name}@proxmox-iaas-platform")
            vm.ssh_private_key_enc = encrypt(private_pem)
            log_to_vm(vm_id, "SSH key pair generated")
        vm.ssh_public_key = ssh_public_key
        vm.ssh_user = ssh_user
        db.commit()

        skip_cloudinit = provision_data.get("skip_cloudinit", False)
        log_to_vm(vm_id, f"Cloning VM from template (skip_cloudinit={skip_cloudinit})...")

        provider.clone_vm_with_cloudinit(
            template_id=provision_data["template_id"],
            new_vm_id=proxmox_vm_id,
            name=provision_data["name"],
            vm_config=provision_data.get("vm_config"),
            username=provision_data.get("username", "ubuntu"),
            password=provision_data.get("password"),
            ssh_public_key=ssh_public_key,
            ip_mode=provision_data.get("ip_mode", "dhcp"),
            ip_address=provision_data.get("ip_address"),
            gateway=provision_data.get("gateway", "10.0.0.1"),
            dns_nameservers=provision_data.get("dns_nameservers"),
            dns_search=provision_data.get("dns_search"),
            cpu=provision_data.get("cpu", 1),
            ram=provision_data.get("ram", 1024),
            auto_start=provision_data.get("auto_start", True),
            skip_cloudinit=skip_cloudinit,
        )
        
        disk_size_gb = provision_data.get("disk_size_gb")
        template_disk_size = provision_data.get("template_disk_size", 20)
        if disk_size_gb and disk_size_gb > template_disk_size:
            log_to_vm(vm_id, f"Resizing disk to {disk_size_gb} GB...")
            additional_gb = disk_size_gb - template_disk_size
            provider.resize_disk(proxmox_vm_id, "scsi0", f"+{additional_gb}G")
            log_to_vm(vm_id, f"Disk resized by {additional_gb} GB")

        log_to_vm(vm_id, "Syncing disk size from Proxmox...")
        try:
            disk_info = provider.get_vm_disk_info(proxmox_vm_id)
            if disk_info:
                primary_disk = next((d for d in disk_info if d['id'] in ('scsi0', 'virtio0', 'sata0', 'ide0')), disk_info[0])
                vm.disk_size_mb = primary_disk['size_mib']
                log_to_vm(vm_id, f"Disk size synced: {primary_disk['size_mib']} MiB ({round(primary_disk['size_mib']/1024, 1)} GiB)")
        except Exception as disk_err:
            logger.warning(f"Could not sync disk size from Proxmox: {disk_err}")

        assigned_ip = provision_data.get("ip_address")
        
        if provision_data.get("auto_start", True):
            vm.status = "running"
            
            if not assigned_ip:
                log_to_vm(vm_id, "Waiting for network to initialize...")
                import time
                for attempt in range(6):
                    time.sleep(5)
                    try:
                        interfaces = provider.get_vm_interfaces(proxmox_vm_id)
                        for iface in interfaces:
                            if iface.ip and not iface.ip.startswith("127.") and ":" not in iface.ip:
                                assigned_ip = iface.ip
                                break
                        if assigned_ip:
                            log_to_vm(vm_id, f"Assigned IP: {assigned_ip}")
                            break
                    except Exception as ip_err:
                        logger.debug(f"Guest agent not ready yet (attempt {attempt + 1}/6): {ip_err}")
                else:
                    log_to_vm(vm_id, "Could not fetch IP via guest agent")
            
            log_to_vm(vm_id, "VM provisioned and started successfully")
        else:
            vm.status = "stopped"
            log_to_vm(vm_id, "VM provisioned successfully")
        
        if assigned_ip:
            vm.ip_address = assigned_ip
            
            if not vm.network_id:
                tenant_networks = db.query(TenantNetwork).filter(
                    TenantNetwork.tenant_id == vm.tenant_id
                ).all()
                
                for network in tenant_networks:
                    if network.cidr:
                        try:
                            if ipaddress.ip_address(assigned_ip) in ipaddress.ip_network(network.cidr, strict=False):
                                vm.network_id = network.id
                                logger.info(f"Auto-assigned VM {vm.id} to network {network.name} (ID: {network.id}) based on IP {assigned_ip}")
                                break
                        except ValueError:
                            continue
        
        db.commit()
        
        try:
            publish_status_update("vm", vm.id, "provisioning", vm.status)
        except Exception as ws_err:
            logger.warning(f"Failed to broadcast status: {ws_err}")
        
        return {"status": "success", "vm_id": vm_id, "proxmox_vm_id": proxmox_vm_id}
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"VM provisioning failed for VM {vm_id}: {error_msg}")
        
        log_to_vm(vm_id, f"Provisioning failed: {error_msg}")
        
        try:
            vm = db.query(VM).filter(VM.id == vm_id).first()
            if vm:
                vm.status = "error"
                vm.error = error_msg
                db.commit()
                
                try:
                    publish_status_update("vm", vm.id, "provisioning", "error")
                except Exception as ws_err:
                    logger.warning(f"Failed to broadcast error status: {ws_err}")
        except Exception as db_err:
            logger.error(f"Failed to update VM status: {db_err}")
        
        return {"status": "error", "error": error_msg, "vm_id": vm_id}
    
    finally:
        db.close()