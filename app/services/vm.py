import logging
import json
from sqlalchemy.orm import Session
from app.models.vm import VM, VMSnapshot, VALID_STATUS_TRANSITIONS
from app.schemas.vm import VMCreate, VMUpdate, VMSnapshotCreate
from app.services.terraform import (
    get_terraform_context,
    render_terraform_code,
    run_terraform_job
)
from app.providers import get_container_provider, get_hypervisor_provider, ProviderException

logger = logging.getLogger(__name__)


def validate_status_transition(current_status: str, target_status: str) -> bool:
    """
    Validate if a status transition is allowed.
    Uses centralized status transition definitions from VM model.
    """
    allowed_transitions = VALID_STATUS_TRANSITIONS.get(current_status, [])
    return target_status in allowed_transitions


def validate_vm_action(vm: VM, action: str) -> None:
    """
    Validate if an action can be performed on a VM based on its current status.
    Raises ValueError if the action is not allowed.
    """
    if action == "start":
        if vm.status not in ["stopped", "error"]:
            raise ValueError(f"Cannot start VM with status '{vm.status}'. VM must be stopped or in error state.")
    elif action == "stop":
        if vm.status not in ["running"]:
            raise ValueError(f"Cannot stop VM with status '{vm.status}'. VM must be running.")
    elif action == "restart":
        if vm.status not in ["running"]:
            raise ValueError(f"Cannot restart VM with status '{vm.status}'. VM must be running.")
    elif action == "delete":
        if vm.status == "running":
            raise ValueError("Cannot delete running VM. Stop it first or use force=true")


def create_vm_logic(db: Session, vm_data: VMCreate, owner_id: int):
    """
    Core logic to provision a VM.
    """
    existing_vm = db.query(VM).filter(VM.name == vm_data.name).first()
    if existing_vm:
        raise ValueError(f"VM name '{vm_data.name}' already exists")

    vm_data_dict = vm_data.model_dump()

    new_vm = VM(
        **vm_data_dict,
        owner_id=owner_id,
        status="pending"
    )

    try:
        db.add(new_vm)
        db.flush()

        template_name, variables = get_terraform_context(vm_data, new_vm.id, db)
        tf_code = render_terraform_code(template_name, variables)

        result = run_terraform_job(new_vm.id, new_vm.name, tf_code, variables, workspace_prefix="vm")

        new_vm.status = "running"

        if new_vm.provider == "docker":
            port = result["outputs"].get("port")
            new_vm.ip_address = f"127.0.0.1:{port}"
        elif new_vm.provider == "vsphere":
            new_vm.ip_address = result["outputs"].get("ip", "0.0.0.0")

        new_vm.terraform_job_id = f"terraform_{new_vm.name}_{new_vm.id}"

        db.commit()
        db.refresh(new_vm)

        logger.info(f"VM '{new_vm.name}' (ID: {new_vm.id}) provisioned successfully.")
        return new_vm

    except Exception as e:
        logger.error(f"Error creating VM '{vm_data.name}': {e}")
        new_vm.status = "error"
        db.commit()
        raise


def delete_vm_logic(db: Session, vm_id: int, force: bool = False):
    """
    Core logic to destroy a VM using container or hypervisor provider.
    """
    vm = db.query(VM).filter(VM.id == vm_id).first()

    if not vm:
        raise ValueError(f"VM with ID {vm_id} not found")

    if not force:
        validate_vm_action(vm, "delete")

    try:
        if vm.provider == "proxmox":
            hypervisor_provider = get_hypervisor_provider()
            proxmox_vm_id = vm.proxmox_vm_id
            if proxmox_vm_id:
                hypervisor_provider.delete_vm(proxmox_vm_id)
                logger.info(f"Deleted Proxmox VM {proxmox_vm_id} (ID: {vm_id})")
            db.delete(vm)
            db.commit()
            logger.info(f"VM '{vm.name}' (ID: {vm_id}) destroyed successfully.")
        else:
            container_provider = get_container_provider(vm.provider)

            if vm.status == "running":
                container_provider.stop(vm.name)

            container_provider.remove(vm.name, force=force)

            db.delete(vm)
            db.commit()

            logger.info(f"VM '{vm.name}' (ID: {vm_id}) destroyed successfully.")

    except Exception as e:
        logger.error(f"Error deleting VM ID {vm_id}: {e}")
        raise


def start_vm_logic(db: Session, vm_id: int):
    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        raise ValueError(f"VM with ID {vm_id} not found")

    validate_vm_action(vm, "start")

    try:
        if vm.provider == "proxmox":
            hypervisor_provider = get_hypervisor_provider()
            proxmox_vm_id = vm.proxmox_vm_id
            if proxmox_vm_id:
                hypervisor_provider.start_vm(proxmox_vm_id)
            vm.status = "running"

            import time
            for attempt in range(6):
                time.sleep(5)
                try:
                    interfaces = hypervisor_provider.get_vm_interfaces(proxmox_vm_id)
                    for iface in interfaces:
                        if iface.ip and not iface.ip.startswith("127.") and ":" not in iface.ip:
                            vm.ip_address = iface.ip
                            break
                    if vm.ip_address:
                        break
                except Exception:
                    logger.debug(f"Guest agent not ready (attempt {attempt + 1}/6)")

            db.commit()
            db.refresh(vm)
            logger.info(f"Started Proxmox VM {proxmox_vm_id} (ID: {vm_id})")
        else:
            container_provider = get_container_provider(vm.provider)
            container_provider.start(vm.name)

            vm.status = "running"
            db.commit()
            db.refresh(vm)
            logger.info(f"Started VM {vm.name} (ID: {vm_id}) via provider abstraction")
    except ProviderException as e:
        logger.error(f"Provider error starting VM {vm_id}: {e}")
        raise ValueError(f"Failed to start VM: {e.message}")
    except Exception as e:
        logger.error(f"Unexpected error starting VM {vm_id}: {e}")
        raise ValueError(f"Failed to start VM: {str(e)}")


def stop_vm_logic(db: Session, vm_id: int):
    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        raise ValueError(f"VM with ID {vm_id} not found")

    validate_vm_action(vm, "stop")

    try:
        if vm.provider == "proxmox":
            hypervisor_provider = get_hypervisor_provider()
            proxmox_vm_id = vm.proxmox_vm_id
            if proxmox_vm_id:
                hypervisor_provider.stop_vm(proxmox_vm_id)
            vm.status = "stopped"
            db.commit()
            db.refresh(vm)
            logger.info(f"Stopped Proxmox VM {proxmox_vm_id} (ID: {vm_id})")
        else:
            container_provider = get_container_provider(vm.provider)
            container_provider.stop(vm.name)

            vm.status = "stopped"
            db.commit()
            db.refresh(vm)
            logger.info(f"Stopped VM {vm.name} (ID: {vm_id}) via provider abstraction")
    except ProviderException as e:
        logger.error(f"Provider error stopping VM {vm_id}: {e}")
        raise ValueError(f"Failed to stop VM: {e.message}")
    except Exception as e:
        logger.error(f"Unexpected error stopping VM {vm_id}: {e}")
        raise ValueError(f"Failed to stop VM: {str(e)}")


def restart_vm_logic(db: Session, vm_id: int):
    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        raise ValueError(f"VM with ID {vm_id} not found")

    validate_vm_action(vm, "restart")

    try:
        if vm.provider == "proxmox":
            hypervisor_provider = get_hypervisor_provider()
            proxmox_vm_id = vm.proxmox_vm_id
            if proxmox_vm_id:
                hypervisor_provider.stop_vm(proxmox_vm_id)
                hypervisor_provider.start_vm(proxmox_vm_id)
            vm.status = "running"
            db.commit()
            db.refresh(vm)
            logger.info(f"Restarted Proxmox VM {proxmox_vm_id} (ID: {vm_id})")
        else:
            container_provider = get_container_provider(vm.provider)
            container_provider.restart(vm.name)

            vm.status = "running"
            db.commit()
            db.refresh(vm)
            logger.info(f"Restarted VM {vm.name} (ID: {vm_id}) via provider abstraction")
    except ProviderException as e:
        logger.error(f"Provider error restarting VM {vm_id}: {e}")
        raise ValueError(f"Failed to restart VM: {e.message}")
    except Exception as e:
        logger.error(f"Unexpected error restarting VM {vm_id}: {e}")
        raise ValueError(f"Failed to restart VM: {str(e)}")


def get_vm_logs_logic(db: Session, vm_id: int, tail: int):
    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        raise ValueError(f"VM with ID {vm_id} not found")

    try:
        if vm.provider == "proxmox":
            return {
                "vm_id": vm_id,
                "vm_name": vm.name,
                "logs": "",
                "lines": 0
            }
        else:
            container_provider = get_container_provider(vm.provider)
            logs_result = container_provider.get_logs(vm.name, tail=tail)

            return {
                "vm_id": vm_id,
                "vm_name": vm.name,
                "logs": logs_result.logs,
                "lines": logs_result.line_count
            }
    except ProviderException as e:
        logger.error(f"Provider error getting logs for VM {vm_id}: {e}")
        raise ValueError(f"Failed to retrieve logs: {e.message}")
    except Exception as e:
        logger.error(f"Unexpected error getting logs for VM {vm_id}: {e}")
        raise ValueError(f"Failed to retrieve logs: {str(e)}")


def create_snapshot_logic(db: Session, vm_id: int, snapshot_data: VMSnapshotCreate, user_id: int):
    """
    Create a snapshot of a VM.
    """
    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        raise ValueError(f"VM with ID {vm_id} not found")

    if vm.status != "running":
        raise ValueError(f"Cannot snapshot VM with status '{vm.status}'. VM must be running.")

    existing_snapshot = db.query(VMSnapshot).filter(
        VMSnapshot.vm_id == vm_id,
        VMSnapshot.name == snapshot_data.name
    ).first()
    if existing_snapshot:
        raise ValueError(f"Snapshot with name '{snapshot_data.name}' already exists for this VM")

    try:
        container_provider = get_container_provider(vm.provider)
        snapshot_info = container_provider.create_snapshot(vm.name, snapshot_data.name)

        config_json = json.dumps({
            "image": vm.image,
            "cpu": vm.cpu,
            "ram": vm.ram,
            "disk_size": vm.disk_size,
            "network_id": vm.network_id
        })

        db_snapshot = VMSnapshot(
            vm_id=vm_id,
            name=snapshot_data.name,
            description=snapshot_data.description,
            image_tag=snapshot_info.image_tag,
            container_config=config_json,
            created_by=user_id
        )
        db.add(db_snapshot)
        db.commit()
        db.refresh(db_snapshot)

        logger.info(f"Created snapshot '{snapshot_data.name}' for VM {vm_id}")
        return db_snapshot

    except ProviderException as e:
        logger.error(f"Provider error creating snapshot for VM {vm_id}: {e}")
        raise ValueError(f"Failed to create snapshot: {e.message}")
    except Exception as e:
        db.rollback()
        logger.error(f"Unexpected error creating snapshot for VM {vm_id}: {e}")
        raise ValueError(f"Failed to create snapshot: {str(e)}")


def list_snapshots_logic(db: Session, vm_id: int):
    """
    List all snapshots for a VM.
    """
    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        raise ValueError(f"VM with ID {vm_id} not found")

    snapshots = db.query(VMSnapshot).filter(VMSnapshot.vm_id == vm_id).order_by(VMSnapshot.created_at.desc()).all()
    return snapshots


def restore_snapshot_logic(db: Session, vm_id: int, snapshot_id: int):
    """
    Restore a VM from a snapshot.
    """
    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        raise ValueError(f"VM with ID {vm_id} not found")

    snapshot = db.query(VMSnapshot).filter(
        VMSnapshot.id == snapshot_id,
        VMSnapshot.vm_id == vm_id
    ).first()
    if not snapshot:
        raise ValueError(f"Snapshot with ID {snapshot_id} not found for VM {vm_id}")

    try:
        container_provider = get_container_provider(vm.provider)

        if vm.status == "running":
            container_provider.stop(vm.name)

        container_provider.remove(vm.name, force=True)

        container_provider.restore_snapshot(snapshot.image_tag, vm.name)

        logger.info(f"Restored snapshot '{snapshot.name}' for VM {vm_id}")
        return True

    except ProviderException as e:
        logger.error(f"Provider error restoring snapshot for VM {vm_id}: {e}")
        raise ValueError(f"Failed to restore snapshot: {e.message}")
    except Exception as e:
        logger.error(f"Unexpected error restoring snapshot for VM {vm_id}: {e}")
        raise ValueError(f"Failed to restore snapshot: {str(e)}")


def delete_snapshot_logic(db: Session, vm_id: int, snapshot_id: int):
    """
    Delete a snapshot.
    """
    vm = db.query(VM).filter(VM.id == vm_id).first()
    if not vm:
        raise ValueError(f"VM with ID {vm_id} not found")

    snapshot = db.query(VMSnapshot).filter(
        VMSnapshot.id == snapshot_id,
        VMSnapshot.vm_id == vm_id
    ).first()
    if not snapshot:
        raise ValueError(f"Snapshot with ID {snapshot_id} not found for VM {vm_id}")

    try:
        container_provider = get_container_provider(vm.provider)
        container_provider.delete_snapshot(snapshot.image_tag)

        db.delete(snapshot)
        db.commit()

        logger.info(f"Deleted snapshot '{snapshot.name}' for VM {vm_id}")
        return True

    except ProviderException as e:
        logger.error(f"Provider error deleting snapshot for VM {vm_id}: {e}")
        raise ValueError(f"Failed to delete snapshot: {e.message}")
    except Exception as e:
        db.rollback()
        logger.error(f"Unexpected error deleting snapshot for VM {vm_id}: {e}")
        raise ValueError(f"Failed to delete snapshot: {str(e)}")