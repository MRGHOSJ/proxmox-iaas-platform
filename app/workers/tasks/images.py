"""
Image template builder tasks.

Contains Celery tasks for ISO download tracking, VM creation, and template conversion.
"""
import logging
from datetime import datetime, timezone

from app.workers.celery_app import celery_app
from app.core.database import SessionLocal
from app.core.audit import log_audit_event, AUDIT_ACTIONS
from app.workers.tasks.helpers import sanitize_log

logger = logging.getLogger(__name__)


def _on_build_task_failure(task, exc, task_id, args, kwargs, einfo):
    """Callback when Celery task exhausts retries. Sets build status to error."""
    build_id = args[0] if args else None
    admin_user_id = args[1] if len(args) > 1 else None
    admin_username = args[2] if len(args) > 2 else None

    if not build_id:
        return

    db = SessionLocal()
    try:
        from app.models.image import ImageBuild

        build = db.query(ImageBuild).filter_by(id=build_id).first()
        if build:
            build.status = "error"
            db.commit()

            log_audit_event(
                db=db, action=AUDIT_ACTIONS["IMAGE_BUILD_ERROR"], target_type="image_template",
                actor_id=admin_user_id, actor_username=admin_username or "system",
                target_id=build.vmid, target_name=build.name,
                details=f"Build task failed permanently: {str(exc)[:500]}"
            )
            db.commit()
            logger.error(f"Build task failed permanently for build_id={build_id}: {exc}")
    except Exception:
        logger.exception(f"Failed to update build error status for build_id={build_id}")
    finally:
        db.close()


@celery_app.task(
    name="tasks.create_build_vm",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    on_failure=_on_build_task_failure,
)
def create_build_vm_task(self, build_id: int, admin_user_id: int, admin_username: str):
    """Create and start the build VM after ISO or image is ready."""
    db = SessionLocal()
    try:
        from app.models.image import ImageBuild
        from app.providers import get_hypervisor_provider

        build = db.query(ImageBuild).filter_by(id=build_id).first()
        if not build:
            logger.error(f"Build {build_id} not found for VM creation")
            return {"status": "error", "error": "Build not found"}

        provider = get_hypervisor_provider()
        build.status = "creating_vm"
        db.commit()

        iso_source = build.iso_url or build.iso_volid or ""
        is_img = iso_source.lower().endswith(('.img', '.qcow2', '.raw'))

        if is_img:
            logger.info(f"Creating build VM {build.vmid} ({build.name}) from disk image")
            provider.create_build_vm_from_image(
                node=build.node,
                vmid=build.vmid,
                name=build.name,
                image_volid=build.iso_volid,
                cpu=build.recommended_cpu or 2,
                ram_mb=build.recommended_ram_mb or 4096,
                disk_gb=build.recommended_disk_gb or 20,
                target_storage=build.storage,
            )
        else:
            logger.info(f"Creating build VM {build.vmid} ({build.name}) from ISO")
            provider.create_build_vm(
                node=build.node,
                vmid=build.vmid,
                name=build.name,
                iso_volid=build.iso_volid,
                cpu=build.recommended_cpu or 2,
                ram_mb=build.recommended_ram_mb or 4096,
                disk_gb=build.recommended_disk_gb or 20,
                storage=build.storage,
            )

        provider.start_vm(build.vmid)
        build.status = "running"
        db.commit()

        log_audit_event(
            db=db, action=AUDIT_ACTIONS["IMAGE_BUILD_START"], target_type="image_template",
            actor_id=admin_user_id, actor_username=admin_username,
            target_id=build.vmid, target_name=build.name,
            details=f"Build VM created for template: {build.name}, type={'img' if is_img else 'iso'}",
            tenant_id=None,
        )
        db.commit()

        logger.info(f"Build VM {build.vmid} created and started successfully")
        return {"status": "success", "vmid": build.vmid}

    except Exception as e:
        db.rollback()
        if build:
            build.status = "error"
            db.commit()
        logger.error(f"Failed to create build VM: {e}", exc_info=True)
        raise self.retry(exc=e, countdown=10)
    finally:
        db.close()


@celery_app.task(
    name="tasks.convert_build_to_template",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    on_failure=_on_build_task_failure,
)
def convert_build_to_template_task(self, build_id: int, admin_user_id: int, admin_username: str,
                                    name: str, category: str = "client_vm",
                                    description: str = None, os_type: str = "linux",
                                    os_version: str = None,
                                    recommended_cpu: int = 2, recommended_ram_mb: int = 4096,
                                    recommended_disk_gb: int = 20,
                                    tags: list = None):
    """Stop build VM, convert to template, register in DB with provided metadata."""
    db = SessionLocal()
    try:
        from app.models.image import ImageBuild, ImageTemplate
        from app.providers import get_hypervisor_provider

        build = db.query(ImageBuild).filter_by(id=build_id).first()
        if not build:
            logger.error(f"Build {build_id} not found for template conversion")
            return {"status": "error", "error": "Build not found"}

        provider = get_hypervisor_provider()
        build.status = "converting"
        db.commit()

        logger.info(f"Converting build VM {build.vmid} to template")

        try:
            provider.stop_vm(build.vmid)
        except Exception:
            pass

        provider.convert_to_template(build.node, build.vmid)

        existing = db.query(ImageTemplate).filter_by(
            provider="proxmox", template_id=str(build.vmid)
        ).first()

        if existing:
            existing.name = name
            existing.category = category
            existing.description = description
            existing.os_type = os_type
            existing.version = os_version
            existing.recommended_cpu = recommended_cpu
            existing.recommended_ram_mb = recommended_ram_mb
            existing.recommended_disk_gb = recommended_disk_gb
            existing.last_synced_at = datetime.now(timezone.utc)
            existing.is_active = True
            image = existing
            action = AUDIT_ACTIONS["IMAGE_UPDATE"]
        else:
            image = ImageTemplate(
                name=name,
                provider="proxmox",
                template_id=str(build.vmid),
                category=category,
                description=description,
                os_type=os_type,
                version=os_version,
                tags=tags,
                recommended_cpu=recommended_cpu,
                recommended_ram_mb=recommended_ram_mb,
                recommended_disk_gb=recommended_disk_gb,
                last_synced_at=datetime.now(timezone.utc),
            )
            db.add(image)
            db.flush()
            action = AUDIT_ACTIONS["IMAGE_REGISTER"]

        db.commit()

        log_audit_event(
            db=db, action=action, target_type="image_template",
            actor_id=admin_user_id, actor_username=admin_username,
            target_id=image.id, target_name=name,
            details=f"Build VM {build.vmid} converted to template: {name}",
            tenant_id=None,
        )
        db.commit()

        db.delete(build)
        db.commit()

        logger.info(f"Build VM {build.vmid} converted to template (id={image.id})")
        return {"status": "success", "image_id": image.id}

    except Exception as e:
        db.rollback()
        logger.error(f"Failed to convert build to template: {e}", exc_info=True)
        raise self.retry(exc=e, countdown=10)
    finally:
        db.close()


def _detect_os_type(name: str) -> str:
    """Detect OS type from template name."""
    name_lower = name.lower()
    if "ubuntu" in name_lower:
        return "Ubuntu"
    elif "debian" in name_lower:
        return "Debian"
    elif "centos" in name_lower:
        return "CentOS"
    elif "rocky" in name_lower:
        return "Rocky Linux"
    elif "alma" in name_lower:
        return "AlmaLinux"
    elif "windows" in name_lower:
        return "Windows"
    elif "opnsense" in name_lower:
        return "OPNsense"
    elif "pfsense" in name_lower:
        return "pfSense"
    elif "fedora" in name_lower:
        return "Fedora"
    elif "arch" in name_lower:
        return "Arch Linux"
    return "Linux"
