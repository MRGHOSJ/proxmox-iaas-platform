import json
import logging
import re
import uuid
import secrets
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dependencies import get_current_user as get_user, get_current_tenant
from app.core.iam import is_super_admin, has_permission
from app.core.audit import log_audit_event, AUDIT_ACTIONS
from app.models.user import User
from app.models.tenant import Tenant
from app.models.image import ImageTemplate, TenantImage, ImageBuild
from app.providers import get_hypervisor_provider
from app.schemas.vm import (
    CPUResizeRequest,
    RAMResizeRequest,
    DiskResizeRequest,
    DiskResizeResponse,
    ResourceResizeResponse,
    VMResourcesResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/images", tags=["Images"])

TEMPLATE_SYNC_CACHE_KEY = "proxmox:templates:last_sync"
TEMPLATE_SYNC_TTL = 300


def _get_redis():
    try:
        from app.core.websocket import get_sync_redis
        return get_sync_redis()
    except Exception:
        return None


def sync_proxmox_templates(db: Session) -> int:
    """Sync Proxmox templates into DB. Returns count of newly registered."""
    redis_client = _get_redis()
    if redis_client:
        cached = redis_client.get(TEMPLATE_SYNC_CACHE_KEY)
        if cached:
            return 0

    provider = get_hypervisor_provider()
    try:
        proxmox_templates = provider.list_templates()
    except Exception as e:
        logger.error(f"Failed to list Proxmox templates for sync: {e}")
        return 0

    existing_rows = (
        db.query(ImageTemplate)
        .filter(ImageTemplate.provider == "proxmox")
        .all()
    )
    existing_by_vmid = {t.template_id: t for t in existing_rows}

    new_count = 0
    now = datetime.now(timezone.utc)
    for pt in proxmox_templates:
        vmid = str(pt["vmid"])
        row = existing_by_vmid.get(vmid)
        if row:
            row.last_synced_at = now
            if not row.is_active:
                row.is_active = True
                row.name = pt["name"]
                row.os_type = pt.get("os", row.os_type or "Linux")
                row.recommended_cpu = pt.get("cores", row.recommended_cpu)
                row.recommended_ram_mb = pt.get("memory", row.recommended_ram_mb)
                row.recommended_disk_gb = pt.get("disk", row.recommended_disk_gb)
            continue

        db.add(ImageTemplate(
            name=pt["name"],
            provider="proxmox",
            template_id=vmid,
            category="client_vm",
            os_type=pt.get("os", "Linux"),
            recommended_cpu=pt.get("cores", 1),
            recommended_ram_mb=pt.get("memory", 1024),
            recommended_disk_gb=pt.get("disk", 10),
            is_active=True,
            is_public=False,
        ))
        new_count += 1

    if proxmox_templates:
        proxmox_vmids = {str(pt["vmid"]) for pt in proxmox_templates}
        for vmid, row in existing_by_vmid.items():
            if row.is_active and vmid not in proxmox_vmids:
                logger.info(
                    f"Proxmox template {vmid} ({row.name}) no longer exists on Proxmox; "
                    f"soft-deleting DB row id={row.id}"
                )
                row.is_active = False
                row.last_synced_at = now
    else:
        logger.warning("Proxmox returned 0 templates; skipping orphan reconciliation")

    db.commit()
    if new_count:
        logger.info(f"Auto-registered {new_count} new Proxmox templates into DB")

    if redis_client:
        redis_client.setex(TEMPLATE_SYNC_CACHE_KEY, TEMPLATE_SYNC_TTL, "1")

    return new_count


# ─── Pydantic Schemas ───

class ImageResponse(BaseModel):
    id: int
    name: str
    provider: str
    template_id: str
    category: str
    description: Optional[str] = None
    version: Optional[str] = None
    os_type: Optional[str] = None
    tags: Optional[dict] = None
    recommended_cpu: int = 2
    recommended_ram_mb: int = 4096
    recommended_disk_gb: int = 20
    provisioning_notes: Optional[str] = None
    is_active: bool = True
    is_public: bool = False
    api_enabled: bool = False
    last_synced_at: Optional[str] = None
    created_at: Optional[str] = None
    tenant_count: int = 0

    class Config:
        from_attributes = True


class ImageCreateRequest(BaseModel):
    name: str
    category: str = "client_vm"
    provider: str = "proxmox"
    template_id: str
    description: Optional[str] = None
    version: Optional[str] = None
    os_type: Optional[str] = None
    tags: Optional[dict] = None
    recommended_cpu: int = 2
    recommended_ram_mb: int = 4096
    recommended_disk_gb: int = 20
    provisioning_notes: Optional[str] = None
    is_public: bool = False
    api_enabled: bool = False


class ImageUpdateRequest(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    version: Optional[str] = None
    os_type: Optional[str] = None
    tags: Optional[dict] = None
    recommended_cpu: Optional[int] = None
    recommended_ram_mb: Optional[int] = None
    recommended_disk_gb: Optional[int] = None
    provisioning_notes: Optional[str] = None
    is_public: Optional[bool] = None
    api_enabled: Optional[bool] = None


class BuildStartRequest(BaseModel):
    name: str
    category: str = "client_vm"
    node: str
    storage: str
    volid: Optional[str] = None
    iso_url: Optional[str] = None
    image_url: Optional[str] = None
    description: Optional[str] = None
    recommended_cpu: int = 2
    recommended_ram_mb: int = 4096
    recommended_disk_gb: int = 20
    # When True, the build is download-only: ISO is fetched into storage but no
    # VM is created and no template build is started. The build row still
    # exists for tray tracking and audit, but is hidden from "Builds in Progress".
    download_only: bool = False


class ConvertRequest(BaseModel):
    name: str
    category: str = "client_vm"
    description: Optional[str] = None
    os_type: str = "linux"
    os_version: Optional[str] = None
    recommended_cpu: int = 2
    recommended_ram_mb: int = 4096
    recommended_disk_gb: int = 20
    tags: Optional[dict] = None


class AssignTenantsRequest(BaseModel):
    tenant_ids: List[int]


class IsoFileResponse(BaseModel):
    filename: str
    size: int
    format: str
    volid: str


class BuildProgressResponse(BaseModel):
    status: str
    percent: Optional[int] = None
    message: str
    downloaded_mb: Optional[int] = None
    total_mb: Optional[int] = None
    speed_mbps: Optional[str] = None
    eta: Optional[str] = None
    download_only: bool = False


class BuildLogResponse(BaseModel):
    vmid: int
    status: str
    lines: List[str]


class CategoryCountResponse(BaseModel):
    category: str
    count: int


# ─── Helper: Allocate unique build VMID ───

def _allocate_build_vmid(db: Session) -> int:
    """Find next available VMID for build VMs (starting from 9000)."""
    db_vmids = {row[0] for row in db.query(ImageBuild.vmid).all()}
    try:
        provider = get_hypervisor_provider()
        proxmox_vms = provider.list_all_vms()
        proxmox_vmids = {vm["vmid"] for vm in proxmox_vms if vm.get("vmid")}
    except Exception:
        proxmox_vmids = set()

    used = db_vmids | proxmox_vmids
    vmid = 9000
    while vmid in used:
        vmid += 1
    return vmid


def _detect_os_type(name: str) -> str:
    name_lower = name.lower()
    os_map = [
        ("ubuntu", "Ubuntu"), ("debian", "Debian"), ("centos", "CentOS"),
        ("rocky", "Rocky Linux"), ("alma", "AlmaLinux"), ("windows", "Windows"),
        ("opnsense", "OPNsense"), ("pfsense", "pfSense"), ("fedora", "Fedora"),
        ("arch", "Arch Linux"),
    ]
    for keyword, os_name in os_map:
        if keyword in name_lower:
            return os_name
    return "Linux"


# ═══════════════════════════════════════════════════════════
# NON-PARAM ROUTES — MUST BE BEFORE /{image_id}
# ═══════════════════════════════════════════════════════════

@router.get("/categories")
async def list_categories(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """List categories with image counts (any authenticated user)."""
    categories = [
        "client_vm", "firewall", "vpn", "load_balancer",
        "monitoring", "database", "web_server", "custom",
    ]
    result = []
    for cat in categories:
        count = db.query(ImageTemplate).filter_by(category=cat, is_active=True).count()
        result.append({"category": cat, "count": count})
    return result


@router.get("/query-url-metadata")
async def query_image_metadata(
    url: str = Query(..., description="URL to query for filename, size, and mimetype"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """Query Proxmox for URL metadata (filename, size, mimetype)."""
    import requests
    provider = get_hypervisor_provider()
    try:
        metadata = provider.query_url_metadata(provider._node, url)
        return metadata
    except requests.exceptions.HTTPError as e:
        resp_text = str(e)
        if "name resolution" in resp_text.lower() or "dns" in resp_text.lower():
            raise HTTPException(502, f"Proxmox node cannot resolve URL domain. DNS resolution failed: {url}")
        if "ssl" in resp_text.lower() or "certificate" in resp_text.lower():
            raise HTTPException(502, f"SSL/TLS error connecting to URL: {url}")
        raise HTTPException(502, f"Proxmox failed to query URL metadata: {str(e)[:300]}")
    except requests.exceptions.ConnectionError as e:
        raise HTTPException(502, f"Proxmox node cannot reach the URL: {str(e)[:300]}")
    except requests.exceptions.Timeout:
        raise HTTPException(504, "Proxmox timed out while querying URL metadata")
    except Exception as e:
        raise HTTPException(500, f"Unexpected error querying URL metadata: {str(e)[:300]}")


@router.get("/downloaded")
async def list_downloaded_images(
    node: str = Query("pve", description="Proxmox node name"),
    storage: str = Query("local", description="Storage name"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """List ISO and image files already downloaded to Proxmox storage."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")

    provider = get_hypervisor_provider()
    contents = provider.list_storage_content(node, storage, content_type="iso")
    result = []
    for item in contents:
        volid = item.get("volid", "")
        filename = volid.split("/")[-1] if "/" in volid else volid
        result.append({
            "filename": filename,
            "size": item.get("size", 0),
            "format": item.get("format", "iso"),
            "volid": volid,
            "is_image": filename.lower().endswith(('.img', '.qcow2', '.raw')),
        })
    return result


@router.delete("/downloaded")
async def delete_downloaded_image(
    node: str = Query(...),
    storage: str = Query(...),
    volid: str = Query(...),
    filename: str = Query(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """Delete a file (ISO/IMG) from Proxmox storage. Also removes any
    download-only ImageBuild row that tracked this file. Real (non-download)
    builds are left alone — their VMs may still reference the ISO."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")

    provider = get_hypervisor_provider()
    try:
        provider.delete_storage_content(node, storage, volid, delay=5)
    except Exception as e:
        msg = str(e)[:300]
        if "404" in msg or "Not Found" in msg:
            raise HTTPException(404, f"File not found on {storage}: {volid}")
        raise HTTPException(502, f"Proxmox failed to delete file: {msg}")

    builds = (
        db.query(ImageBuild)
        .filter(ImageBuild.iso_volid == volid, ImageBuild.download_only.is_(True))
        .all()
    )
    for build in builds:
        db.delete(build)
    db.commit()

    log_audit_event(
        db=db, action=AUDIT_ACTIONS["IMAGE_DELETE"], target_type="image_template",
        actor_id=current_user.id, actor_username=current_user.username,
        target_name=filename or volid,
        details=f"Deleted from {node}/{storage}: {volid}, builds_removed={len(builds)}",
        ip_address=None, tenant_id=None,
    )
    db.commit()

    return {
        "status": "deleted",
        "volid": volid,
        "builds_removed": len(builds),
    }


@router.get("/templates")
async def list_templates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """Super admin: list DB-registered templates with auto-sync from Proxmox."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")

    sync_proxmox_templates(db)
    images = db.query(ImageTemplate).filter_by(is_active=True).all()
    result = []
    for img in images:
        tenant_count = db.query(TenantImage).filter_by(image_id=img.id, is_active=True).count()
        result.append({
            "id": img.id,
            "name": img.name,
            "provider": img.provider,
            "template_id": img.template_id,
            "category": img.category,
            "description": img.description,
            "version": img.version,
            "os_type": img.os_type,
            "tags": img.tags,
            "recommended_cpu": img.recommended_cpu,
            "recommended_ram_mb": img.recommended_ram_mb,
            "recommended_disk_gb": img.recommended_disk_gb,
            "provisioning_notes": img.provisioning_notes,
            "is_active": img.is_active,
            "is_public": img.is_public,
            "api_enabled": img.api_enabled,
            "last_synced_at": img.last_synced_at.isoformat() if img.last_synced_at else None,
            "created_at": img.created_at.isoformat() if img.created_at else None,
            "tenant_count": tenant_count,
        })
    return result


@router.get("/builds")
async def list_builds(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """Super admin: list all builds."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")

    builds = db.query(ImageBuild).all()
    return [
        {
            "id": b.id,
            "vmid": b.vmid,
            "name": b.name,
            "category": b.category,
            "node": b.node,
            "storage": b.storage,
            "iso_volid": b.iso_volid,
            "iso_url": b.iso_url,
            "status": b.status,
            "celery_task_id": b.celery_task_id,
            "download_upid": b.download_upid,
            "download_only": b.download_only,
            "created_at": b.created_at.isoformat() if b.created_at else None,
            "recommended_cpu": b.recommended_cpu,
            "recommended_ram_mb": b.recommended_ram_mb,
            "recommended_disk_gb": b.recommended_disk_gb,
        }
        for b in builds
    ]


# ═══════════════════════════════════════════════════════════
# BUILD ROUTES — MUST BE BEFORE /{image_id}
# ═══════════════════════════════════════════════════════════

@router.post("/build")
async def start_build(
    data: BuildStartRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """Start template build from existing volid or download URL, then create VM."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")

    if not data.volid and not data.iso_url and not data.image_url:
        raise HTTPException(400, "Provide volid, iso_url, or image_url")

    vmid = _allocate_build_vmid(db)
    provider = get_hypervisor_provider()
    download_upid = None
    final_volid = data.volid

    if data.volid:
        filename = data.volid.split("/")[-1] if "/" in data.volid else data.volid
        image_type = "img" if filename.lower().endswith(('.img', '.qcow2', '.raw')) else "iso"
        build = ImageBuild(
            vmid=vmid,
            name=data.name,
            category=data.category,
            node=data.node,
            storage=data.storage,
            iso_volid=data.volid,
            description=data.description,
            recommended_cpu=data.recommended_cpu,
            recommended_ram_mb=data.recommended_ram_mb,
            recommended_disk_gb=data.recommended_disk_gb,
            status="ready",
        )
        db.add(build)
        db.commit()

        from app.workers.tasks.images import create_build_vm_task
        task = create_build_vm_task.delay(build.id, current_user.id, current_user.username)
        build.celery_task_id = task.id
        build.status = "creating_vm"
        db.commit()

        return {
            "id": build.id,
            "vmid": vmid,
            "status": build.status,
            "image_type": image_type,
        }

    url = data.image_url or data.iso_url
    filename = url.split("/")[-1]
    image_type = "img" if filename.lower().endswith(('.img', '.qcow2', '.raw')) else "iso"

    existing = provider.list_storage_content(data.node, data.storage, content_type="iso")
    already_exists = any(filename in item.get("volid", "") for item in existing)

    if already_exists:
        final_volid = f"{data.storage}:iso/{filename}"
    else:
        download_result = provider.download_iso_url(data.node, data.storage, url)
        download_upid = download_result["upid"]
        final_volid = download_result.get("volid", f"{data.storage}:iso/{filename}")

    initial_status = "downloading_iso" if (url and not already_exists) else "ready"

    build = ImageBuild(
        vmid=vmid,
        name=data.name,
        category=data.category,
        node=data.node,
        storage=data.storage,
        iso_volid=final_volid,
        iso_url=url,
        download_upid=download_upid,
        description=data.description,
        recommended_cpu=data.recommended_cpu,
        recommended_ram_mb=data.recommended_ram_mb,
        recommended_disk_gb=data.recommended_disk_gb,
        status=initial_status,
        download_only=data.download_only,
    )
    db.add(build)
    db.commit()

    log_audit_event(
        db=db, action=AUDIT_ACTIONS["IMAGE_BUILD_START"], target_type="image_template",
        actor_id=current_user.id, actor_username=current_user.username,
        target_id=vmid, target_name=data.name,
        details=f"name={data.name},category={data.category},volid={final_volid},type={image_type},download_only={data.download_only}",
        ip_address=None, tenant_id=None,
    )
    db.commit()

    if build.status == "ready" and not build.download_only:
        from app.workers.tasks.images import create_build_vm_task
        task = create_build_vm_task.delay(build.id, current_user.id, current_user.username)
        build.celery_task_id = task.id
        build.status = "creating_vm"
        db.commit()

    return {
        "id": build.id,
        "vmid": vmid,
        "status": build.status,
        "download_upid": build.download_upid,
        "image_type": image_type,
        "download_only": build.download_only,
    }


@router.get("/build/{vmid}/download-progress", response_model=BuildProgressResponse)
async def get_download_progress(
    vmid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """Poll ISO download progress. Auto-creates VM when complete."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")

    build = db.query(ImageBuild).filter_by(vmid=vmid).first()
    if not build:
        raise HTTPException(404, "Build not found")

    if not build.download_upid:
        return BuildProgressResponse(status="complete", percent=100, message="Using existing ISO", download_only=build.download_only)

    provider = get_hypervisor_provider()
    task = provider.get_task_status(build.node, build.download_upid)

    if task.get("status") == "stopped":
        exit_status = task.get("exitstatus", "unknown")
        if exit_status == "OK":
            if build.status == "downloading_iso":
                build.status = "ready"
                db.commit()
                if not build.download_only:
                    from app.workers.tasks.images import create_build_vm_task
                    task_result = create_build_vm_task.delay(build.id, current_user.id, current_user.username)
                    build.celery_task_id = task_result.id
                    build.status = "creating_vm"
                    db.commit()
            return BuildProgressResponse(
                status="complete",
                percent=100,
                message="Download complete, creating VM..." if not build.download_only else "Download complete",
                download_only=build.download_only,
            )
        else:
            build.status = "error"
            db.commit()
            log_audit_event(
                db=db, action=AUDIT_ACTIONS["IMAGE_BUILD_ERROR"], target_type="image_template",
                actor_id=current_user.id, actor_username=current_user.username,
                target_id=build.vmid, target_name=build.name,
                details=f"ISO download failed: {exit_status}",
                ip_address=None, tenant_id=None,
            )
            db.commit()
            return BuildProgressResponse(status="error", percent=0, message=f"Download failed: {exit_status}", download_only=build.download_only)

    log = provider.get_task_log_tail(build.node, build.download_upid, tail=200, header=20)
    percent = None
    message = "Downloading ISO to storage..."
    downloaded_mb = None
    total_mb = None
    total_bytes = 0
    speed_mbps = None
    eta = None
    saw_saving_line = False
    saw_length_line = False

    lines = log if isinstance(log, list) else []
    if lines:
        # Pass 1: scan the whole log for the `Length: <bytes> (<size>)` header
        # so `total_bytes` / `total_mb` are populated regardless of whether
        # the latest NN% tick happens to come before or after the header in
        # the line ordering.
        for entry in lines:
            line_text = entry.get("t", "")
            if not line_text:
                continue
            if "Saving to:" in line_text and ".tmp_dwnl." in line_text:
                saw_saving_line = True
            if not saw_length_line:
                length_match = re.search(r'Length:\s+(\d+)\s+\((\d+[MG])\)', line_text)
                if length_match:
                    total_bytes = int(length_match.group(1))
                    total_mb = total_bytes // (1024 * 1024)
                    saw_length_line = True

        # Pass 2: walk the log in reverse to pick up the most recent progress
        # tick (the latest line containing `NN%`).
        for entry in reversed(lines):
            line_text = entry.get("t", "")
            if not line_text:
                continue

            percent_match = re.search(r'(\d+)%', line_text)
            if percent_match:
                percent = int(percent_match.group(1))

                size_match = re.match(r'\s*(\d+)K\s', line_text)
                parsed_kb = int(size_match.group(1)) if size_match else 0
                # wget's first progress tick reports `0K ... 20%` (delta bytes,
                # not cumulative). When the K counter is 0 but we know the
                # total, derive the real byte count from the percent.
                if parsed_kb > 0:
                    downloaded_mb = parsed_kb // 1024
                elif total_bytes > 0 and percent < 100:
                    downloaded_mb = (total_bytes * percent // 100) // (1024 * 1024)

                speed_match = re.search(r'(\d+\.?\d*[MG])\s', line_text)
                if speed_match:
                    speed_mbps = speed_match.group(1)

                eta_match = re.search(r'(\d+[hms](?:\d+[ms])?(?:\d+s)?)$', line_text)
                if eta_match:
                    eta = eta_match.group(1)

                message = f"Downloading ({percent}%)"
                break

        if percent is None:
            # The download is still warming up (Connecting / TLS handshake /
            # first byte) and no NN% tick has been flushed yet. Pick a useful
            # message from what the header DID tell us.
            if saw_length_line and saw_saving_line:
                message = f"Connecting… ({total_mb} MB)"
            elif saw_length_line:
                message = f"Preparing download… ({total_mb} MB)"
            elif saw_saving_line:
                message = "Connecting…"
            elif lines:
                first_line = lines[0].get("t", "")
                if first_line:
                    message = first_line

    # Fallback: read the live size of the `.tmp_dwnl.<pid>` file directly
    # from Proxmox's storage content API. Two passes — first without any
    # `content_type` filter (Proxmox may tag the temp file with
    # `content=iso` OR leave it un-tagged, depending on the storage plugin),
    # then the filtered call as a backup. This is the only signal that's
    # truly "live" without depending on log-flush timing.
    if build.iso_volid:
        filename = build.iso_volid.rsplit("/", 1)[-1]
        if filename and (percent is None or downloaded_mb is None or downloaded_mb == 0):
            temp_prefix = f"{filename}.tmp_dwnl."
            downloaded_bytes = 0
            try:
                content_all = provider.list_storage_content(
                    build.node, build.storage
                ) or []
                for item in content_all:
                    tail = (item.get("volid", "") or "").rsplit("/", 1)[-1]
                    if tail.startswith(temp_prefix):
                        downloaded_bytes = max(
                            downloaded_bytes, int(item.get("size", 0) or 0)
                        )
                if downloaded_bytes <= 0:
                    content_iso = provider.list_storage_content(
                        build.node, build.storage, content_type="iso"
                    ) or []
                    for item in content_iso:
                        tail = (item.get("volid", "") or "").rsplit("/", 1)[-1]
                        if tail.startswith(temp_prefix):
                            downloaded_bytes = max(
                                downloaded_bytes, int(item.get("size", 0) or 0)
                            )
            except Exception as e:
                logger.debug(
                    f"Could not read temp file size for {filename}: {e}"
                )

            if downloaded_bytes > 0:
                downloaded_mb = max(
                    downloaded_mb or 0,
                    downloaded_bytes // (1024 * 1024),
                )
                if total_bytes > 0:
                    computed = int(downloaded_bytes * 100 / total_bytes)
                    # Clamp to 99 so UI doesn't briefly flash 100% while
                    # the temp file is still being finalised.
                    percent = min(99, max(percent or 0, computed))
                message = (
                    f"Downloading ({downloaded_mb} MB / {total_mb} MB)"
                    if total_mb
                    else f"Downloading ({downloaded_mb} MB)"
                )

    return BuildProgressResponse(
        status="downloading",
        percent=percent,
        message=message,
        downloaded_mb=downloaded_mb,
        total_mb=total_mb,
        speed_mbps=speed_mbps,
        eta=eta,
        download_only=build.download_only,
    )


@router.get("/build/{vmid}/download-logs")
async def get_download_logs(
    vmid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """Get full download task logs for a build."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")

    build = db.query(ImageBuild).filter_by(vmid=vmid).first()
    if not build:
        raise HTTPException(404, "Build not found")

    if not build.download_upid:
        return {"vmid": vmid, "status": "complete", "lines": ["Download already complete, no logs available."]}

    provider = get_hypervisor_provider()
    task = provider.get_task_status(build.node, build.download_upid)
    task_status = task.get("status", "unknown")

    log = provider.get_task_log(build.node, build.download_upid, start=0, limit=500)
    lines = []
    if isinstance(log, list):
        for entry in log:
            line_text = entry.get("t", "")
            if line_text:
                lines.append(line_text)

    return {"vmid": vmid, "status": task_status, "lines": lines}


@router.get("/build/{vmid}/status")
async def build_status(
    vmid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """Check build VM status."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")

    build = db.query(ImageBuild).filter_by(vmid=vmid).first()
    if not build:
        raise HTTPException(404, "Build not found")

    provider = get_hypervisor_provider()
    try:
        vm_status = provider.get_vm_status(vmid)
        proxmox_status = vm_status.get("data", {}).get("status", "unknown")
    except Exception:
        proxmox_status = "unknown"

    return {
        "vmid": vmid,
        "name": build.name,
        "status": build.status,
        "proxmox_status": proxmox_status,
        "node": build.node,
        "celery_task_id": build.celery_task_id,
        "download_only": build.download_only,
        "recommended_cpu": build.recommended_cpu,
        "recommended_ram_mb": build.recommended_ram_mb,
        "recommended_disk_gb": build.recommended_disk_gb,
    }


@router.post("/build/{vmid}/console")
async def build_console(
    vmid: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """Get VNC console for build VM. Reuses existing WebSocket token system."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")

    build = db.query(ImageBuild).filter_by(vmid=vmid).first()
    if not build:
        raise HTTPException(404, "Build not found")

    if build.status not in ("running",):
        raise HTTPException(400, f"Build VM is not running (status: {build.status})")

    provider = get_hypervisor_provider()
    try:
        vnc_info = provider.get_vnc_proxy(vmid)
    except Exception as e:
        logger.error(f"Failed to get VNC for build VM {vmid}: {e}")
        raise HTTPException(500, f"Failed to get console: {str(e)}")

    ws_token = secrets.token_urlsafe(32)

    from app.core.cache import set_console_token, set_active_console_session
    set_console_token(ws_token, {
        "vm_id": vmid,
        "proxmox_vm_id": vmid,
        "user_id": current_user.id,
        "vnc_info": vnc_info,
        "console_type": "vnc",
    })

    set_active_console_session(vmid, {
        "upid": vnc_info.get("upid", ""),
        "node": vnc_info.get("node", ""),
        "proxmox_vm_id": vmid,
        "user_id": current_user.id,
        "console_type": "vnc",
    })

    return {
        "websocket_url": f"/v1/vm/ws/console/{ws_token}",
        "vm_id": vmid,
        "vnc_password": vnc_info.get("ticket", ""),
        "console_type": "vnc",
        "desktop_name": f"Build: {build.name}",
    }


@router.post("/build/{vmid}/convert")
async def convert_build(
    vmid: int,
    data: ConvertRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """Convert build VM to template (triggers Celery task)."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")

    build = db.query(ImageBuild).filter_by(vmid=vmid).first()
    if not build:
        raise HTTPException(404, "Build not found")

    if build.status != "running":
        raise HTTPException(400, f"Build VM must be running (status: {build.status})")

    build.status = "converting"
    db.commit()

    from app.workers.tasks.images import convert_build_to_template_task
    task = convert_build_to_template_task.delay(
        build.id, current_user.id, current_user.username,
        name=data.name,
        category=data.category,
        description=data.description,
        os_type=data.os_type,
        os_version=data.os_version,
        recommended_cpu=data.recommended_cpu,
        recommended_ram_mb=data.recommended_ram_mb,
        recommended_disk_gb=data.recommended_disk_gb,
        tags=data.tags,
    )
    build.celery_task_id = task.id
    db.commit()

    return {"status": "converting", "task_id": task.id}


@router.delete("/build/{vmid}")
async def cancel_build(
    vmid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """Cancel build. Destroy VM, keep ISO."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")

    build = db.query(ImageBuild).filter_by(vmid=vmid).first()
    if not build:
        raise HTTPException(404, "Build not found")

    provider = get_hypervisor_provider()
    try:
        provider.stop_vm(vmid)
    except Exception:
        pass

    try:
        provider.delete_vm(vmid)
    except Exception:
        pass

    iso_path = build.iso_volid

    log_audit_event(
        db=db, action=AUDIT_ACTIONS["IMAGE_BUILD_CANCEL"], target_type="image_template",
        actor_id=current_user.id, actor_username=current_user.username,
        target_id=build.vmid, target_name=build.name,
        details=f"Build cancelled, ISO retained: {iso_path}",
        ip_address=None, tenant_id=None,
    )

    db.delete(build)
    db.commit()

    return {"status": "cancelled", "vm_destroyed": True, "iso_retained": True, "iso_path": iso_path}


# ─── Build VM hardware (CPU/RAM/Disk) ──────────────────────────────
# These mirror the /vm/{vm_id}/resize-* endpoints in app/api/vm.py but
# operate on the build's Proxmox vmid directly. Build VMs are not
# registered in the `vm` DB table, so they have no proxmox_vm_id to
# resolve through. The build's `recommended_cpu/ram_mb/disk_gb` fields
# are kept in sync so the eventual "Convert to Template" inherits the
# new sizing.


def _require_running_build(db: Session, vmid: int) -> ImageBuild:
    """Fetch a build by vmid and ensure it is in the `running` state."""
    build = db.query(ImageBuild).filter_by(vmid=vmid).first()
    if not build:
        raise HTTPException(404, "Build not found")
    if build.status != "running":
        raise HTTPException(
            400,
            f"Build VM must be running to change hardware (status: {build.status})",
        )
    return build


@router.get("/build/{vmid}/resources", response_model=VMResourcesResponse)
def get_build_resources(
    vmid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """Super admin: get live CPU/RAM/disk of a build VM."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")
    build = _require_running_build(db, vmid)
    provider = get_hypervisor_provider()
    try:
        resources = provider.get_vm_resources(build.vmid)
    except Exception as e:
        raise HTTPException(502, f"Proxmox failed to read resources: {e}")
    return VMResourcesResponse(
        cpu_cores=int(resources.get("cpu_cores", 1)),
        memory_mb=int(resources.get("memory_mb", 1024)),
        memory_gb=round(int(resources.get("memory_mb", 1024)) / 1024, 1),
        disks=resources.get("disks", {}),
        digest=resources.get("digest"),
        name=resources.get("name"),
        status=resources.get("status", "running"),
    )


@router.get("/build/{vmid}/config")
def get_build_config(
    vmid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """Super admin: get the raw Proxmox VM config for display in the Options tab."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")
    build = db.query(ImageBuild).filter_by(vmid=vmid).first()
    if not build:
        raise HTTPException(404, "Build not found")
    provider = get_hypervisor_provider()
    try:
        target_node = build.node or provider._get_node_for_vm(build.vmid)
        config = provider._api(
            "GET", f"nodes/{target_node}/qemu/{build.vmid}/config"
        )
    except Exception as e:
        raise HTTPException(502, f"Proxmox failed to read config: {e}")
    return {"vmid": build.vmid, "config": config}


@router.post("/build/{vmid}/resize-cpu", response_model=ResourceResizeResponse)
def resize_build_cpu(
    vmid: int,
    request_body: CPUResizeRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """Super admin: resize CPU cores of a build VM."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")
    build = _require_running_build(db, vmid)
    provider = get_hypervisor_provider()
    try:
        current = provider.get_vm_resources(build.vmid)
        previous_cores = int(current.get("cpu_cores", 1))
        provider.update_vm_resources(build.vmid, cpu_cores=request_body.cores)
    except Exception as e:
        raise HTTPException(502, f"Proxmox failed to update CPU: {e}")

    build.recommended_cpu = request_body.cores
    db.commit()

    log_audit_event(
        db=db, action=AUDIT_ACTIONS["VM_UPDATE"], target_type="image_template",
        actor_id=current_user.id, actor_username=current_user.username,
        target_id=build.vmid, target_name=build.name,
        new_value=f"cpu_cores={request_body.cores}",
        ip_address=request.headers.get("X-Forwarded-For"),
        tenant_id=None,
    )
    db.commit()

    restarted = False
    if request_body.restart_after_resize:
        try:
            provider.stop_vm(build.vmid)
        except Exception:
            pass
        try:
            provider.start_vm(build.vmid)
            restarted = True
        except Exception as e:
            logger.warning(f"Failed to restart build VM {vmid} after CPU resize: {e}")

    return ResourceResizeResponse(
        resource_type="cpu",
        previous_value=previous_cores,
        new_value=request_body.cores,
        status="resized",
        restarted=restarted,
    )


@router.post("/build/{vmid}/resize-ram", response_model=ResourceResizeResponse)
def resize_build_ram(
    vmid: int,
    request_body: RAMResizeRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """Super admin: resize RAM of a build VM."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")
    build = _require_running_build(db, vmid)
    provider = get_hypervisor_provider()
    try:
        current = provider.get_vm_resources(build.vmid)
        previous_memory = int(current.get("memory_mb", 1024))
        provider.update_vm_resources(build.vmid, memory_mb=request_body.memory_mb)
    except Exception as e:
        raise HTTPException(502, f"Proxmox failed to update RAM: {e}")

    build.recommended_ram_mb = request_body.memory_mb
    db.commit()

    log_audit_event(
        db=db, action=AUDIT_ACTIONS["VM_UPDATE"], target_type="image_template",
        actor_id=current_user.id, actor_username=current_user.username,
        target_id=build.vmid, target_name=build.name,
        new_value=f"memory_mb={request_body.memory_mb}",
        ip_address=request.headers.get("X-Forwarded-For"),
        tenant_id=None,
    )
    db.commit()

    restarted = False
    if request_body.restart_after_resize:
        try:
            provider.stop_vm(build.vmid)
        except Exception:
            pass
        try:
            provider.start_vm(build.vmid)
            restarted = True
        except Exception as e:
            logger.warning(f"Failed to restart build VM {vmid} after RAM resize: {e}")

    return ResourceResizeResponse(
        resource_type="memory",
        previous_value=previous_memory,
        new_value=request_body.memory_mb,
        status="resized",
        restarted=restarted,
    )


@router.post("/build/{vmid}/resize-disk", response_model=DiskResizeResponse)
def resize_build_disk(
    vmid: int,
    request_body: DiskResizeRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """Super admin: resize a disk on a build VM (relative size, e.g. +10G)."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")
    build = _require_running_build(db, vmid)
    provider = get_hypervisor_provider()

    try:
        resources = provider.get_vm_resources(build.vmid)
        disks = resources.get("disks", {}) or {}
        disk_cfg = disks.get(request_body.disk)
        if not disk_cfg:
            raise HTTPException(400, f"Disk {request_body.disk} not found on build VM")
        previous_size_mib = provider._parse_config_disk_size(disk_cfg["config"])

        requested_mib = provider._parse_size_to_mib(request_body.size)
        if requested_mib <= 0:
            raise HTTPException(400, "Size must be greater than 0")

        provider.resize_disk(build.vmid, request_body.disk, request_body.size, node=build.node)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Proxmox failed to resize disk: {e}")

    new_size_mib = previous_size_mib + requested_mib
    try:
        post = provider.get_vm_resources(build.vmid)
        post_disk_cfg = (post.get("disks", {}) or {}).get(request_body.disk)
        if post_disk_cfg:
            parsed = provider._parse_config_disk_size(post_disk_cfg["config"])
            if parsed > 0:
                new_size_mib = parsed
    except Exception:
        pass

    new_size_gb = round(new_size_mib / 1024, 1)
    build.recommended_disk_gb = new_size_gb
    db.commit()

    log_audit_event(
        db=db, action=AUDIT_ACTIONS["VM_UPDATE"], target_type="image_template",
        actor_id=current_user.id, actor_username=current_user.username,
        target_id=build.vmid, target_name=build.name,
        old_value=f"disk={request_body.disk},size={previous_size_mib}M",
        new_value=f"disk={request_body.disk},size={new_size_mib}M",
        details=f"Resized build disk {request_body.disk} by {request_body.size}",
        ip_address=request.headers.get("X-Forwarded-For"),
        tenant_id=None,
    )
    db.commit()

    restarted = False
    if request_body.restart_after_resize:
        try:
            provider.stop_vm(build.vmid)
        except Exception:
            pass
        try:
            provider.start_vm(build.vmid)
            restarted = True
        except Exception as e:
            logger.warning(f"Failed to restart build VM {vmid} after disk resize: {e}")

    return DiskResizeResponse(
        disk_id=request_body.disk,
        previous_size_mib=previous_size_mib,
        new_size_mib=new_size_mib,
        previous_size_gb=round(previous_size_mib / 1024, 1),
        new_size_gb=new_size_gb,
        status="resized",
        restarted=restarted,
    )


@router.get("/iso/list")
async def list_downloaded_isos(
    node: str = Query(...),
    storage: str = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """List ISO files already present in Proxmox storage for reuse."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")

    provider = get_hypervisor_provider()
    contents = provider.list_storage_content(node, storage, content_type="iso")
    return [
        {
            "filename": item.get("volid", "").split("/")[-1],
            "size": item.get("size", 0),
            "format": item.get("format", "iso"),
            "volid": item.get("volid", ""),
        }
        for item in contents if item.get("content") == "iso"
    ]


# ═══════════════════════════════════════════════════════════
# CRUD ROUTES — After all non-param routes
# ═══════════════════════════════════════════════════════════

@router.get("")
async def list_images(
    category: Optional[str] = Query(None),
    provider: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, description="registered|unregistered|all"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """Super admin: list all images, auto-syncing from Proxmox."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")

    sync_proxmox_templates(db)

    query = db.query(ImageTemplate).filter_by(is_active=True)
    if category:
        query = query.filter(ImageTemplate.category == category)
    if provider:
        query = query.filter(ImageTemplate.provider == provider)

    images = query.all()
    result = []
    for img in images:
        tenant_count = db.query(TenantImage).filter_by(image_id=img.id, is_active=True).count()
        result.append({
            "id": img.id,
            "name": img.name,
            "provider": img.provider,
            "template_id": img.template_id,
            "category": img.category,
            "description": img.description,
            "version": img.version,
            "os_type": img.os_type,
            "tags": img.tags,
            "recommended_cpu": img.recommended_cpu,
            "recommended_ram_mb": img.recommended_ram_mb,
            "recommended_disk_gb": img.recommended_disk_gb,
            "provisioning_notes": img.provisioning_notes,
            "is_active": img.is_active,
            "is_public": img.is_public,
            "api_enabled": img.api_enabled,
            "last_synced_at": img.last_synced_at.isoformat() if img.last_synced_at else None,
            "created_at": img.created_at.isoformat() if img.created_at else None,
            "tenant_count": tenant_count,
        })

    return result


@router.get("/{image_id}")
async def get_image(
    image_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """Super admin: full image metadata + tenant assignments."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")

    image = db.query(ImageTemplate).filter_by(id=image_id).first()
    if not image:
        raise HTTPException(404, "Image not found")

    assignments = db.query(TenantImage).filter_by(image_id=image_id, is_active=True).all()
    tenant_ids = [a.tenant_id for a in assignments]

    return {
        "id": image.id,
        "name": image.name,
        "provider": image.provider,
        "template_id": image.template_id,
        "category": image.category,
        "description": image.description,
        "version": image.version,
        "os_type": image.os_type,
        "tags": image.tags,
        "recommended_cpu": image.recommended_cpu,
        "recommended_ram_mb": image.recommended_ram_mb,
        "recommended_disk_gb": image.recommended_disk_gb,
        "provisioning_notes": image.provisioning_notes,
        "is_active": image.is_active,
        "is_public": image.is_public,
        "api_enabled": image.api_enabled,
        "last_synced_at": image.last_synced_at.isoformat() if image.last_synced_at else None,
        "created_at": image.created_at.isoformat() if image.created_at else None,
        "assigned_tenant_ids": tenant_ids,
    }


@router.post("")
async def register_image(
    data: ImageCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """Super admin: register existing Proxmox template."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")

    provider = get_hypervisor_provider()
    live_templates = provider.list_templates()
    found = any(str(t["vmid"]) == data.template_id for t in live_templates)
    if not found:
        raise HTTPException(400, f"Template ID {data.template_id} not found in Proxmox or not marked as template")

    existing = db.query(ImageTemplate).filter_by(
        provider=data.provider, template_id=data.template_id
    ).first()
    if existing:
        if existing.is_active:
            raise HTTPException(409, "Template already registered")
        existing.is_active = True
        existing.name = data.name
        existing.category = data.category
        existing.description = data.description
        existing.version = data.version
        existing.os_type = data.os_type
        existing.tags = data.tags
        existing.recommended_cpu = data.recommended_cpu
        existing.recommended_ram_mb = data.recommended_ram_mb
        existing.recommended_disk_gb = data.recommended_disk_gb
        existing.provisioning_notes = data.provisioning_notes
        existing.is_public = data.is_public
        existing.api_enabled = data.api_enabled
        existing.last_synced_at = datetime.now(timezone.utc)
        image = existing
    else:
        image = ImageTemplate(
            name=data.name,
            provider=data.provider,
            template_id=data.template_id,
            category=data.category,
            description=data.description,
            version=data.version,
            os_type=data.os_type,
            tags=data.tags,
            recommended_cpu=data.recommended_cpu,
            recommended_ram_mb=data.recommended_ram_mb,
            recommended_disk_gb=data.recommended_disk_gb,
            provisioning_notes=data.provisioning_notes,
            is_public=data.is_public,
            api_enabled=data.api_enabled,
            last_synced_at=datetime.now(timezone.utc),
        )
        db.add(image)

    db.commit()
    db.refresh(image)

    log_audit_event(
        db=db, action=AUDIT_ACTIONS["IMAGE_REGISTER"], target_type="image_template",
        actor_id=current_user.id, actor_username=current_user.username,
        target_id=image.id, target_name=image.name,
        details=f"provider={data.provider},template_id={data.template_id},category={data.category}",
        ip_address=None, tenant_id=None,
    )
    db.commit()

    return {"id": image.id, "name": image.name, "message": "Image registered successfully"}


@router.patch("/{image_id}")
async def update_image(
    image_id: int,
    data: ImageUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """Super admin: update image metadata."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")

    image = db.query(ImageTemplate).filter_by(id=image_id).first()
    if not image:
        raise HTTPException(404, "Image not found")

    old_values = {
        "name": image.name, "category": image.category, "is_public": image.is_public,
        "description": image.description,
    }

    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(image, field, value)

    image.last_synced_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(image)

    new_values = {
        "name": image.name, "category": image.category, "is_public": image.is_public,
        "description": image.description,
    }

    log_audit_event(
        db=db, action=AUDIT_ACTIONS["IMAGE_UPDATE"], target_type="image_template",
        actor_id=current_user.id, actor_username=current_user.username,
        target_id=image.id, target_name=image.name,
        old_value=str(old_values), new_value=str(new_values),
        ip_address=None, tenant_id=None,
    )
    db.commit()

    return {"id": image.id, "name": image.name, "message": "Image updated successfully"}


@router.delete("/{image_id}")
async def delete_image(
    image_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """Super admin: delete image (destroys the underlying Proxmox template and soft-deletes the DB row)."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")

    image = db.query(ImageTemplate).filter_by(id=image_id).first()
    if not image:
        raise HTTPException(404, "Image not found")

    proxmox_destroyed = False
    proxmox_error = None
    try:
        vmid_int = int(image.template_id)
        provider = get_hypervisor_provider()
        provider.delete_vm(vmid_int)
        proxmox_destroyed = True
    except (ValueError, TypeError):
        proxmox_error = f"Invalid template_id on row: {image.template_id!r}"
        logger.error(proxmox_error)
    except Exception as e:
        proxmox_error = str(e)
        logger.warning(f"Proxmox destroy failed for template {image.template_id} ({image.name}): {e}")

    image.is_active = False
    db.commit()

    details = "Deleted"
    if proxmox_destroyed:
        details += " (Proxmox template destroyed)"
    elif proxmox_error:
        details += f" (Proxmox destroy skipped: {proxmox_error})"

    log_audit_event(
        db=db, action=AUDIT_ACTIONS["IMAGE_DELETE"], target_type="image_template",
        actor_id=current_user.id, actor_username=current_user.username,
        target_id=image.id, target_name=image.name,
        details=details,
        ip_address=None, tenant_id=None,
    )
    db.commit()

    return {
        "message": "Image deleted successfully",
        "proxmox_destroyed": proxmox_destroyed,
        "proxmox_error": proxmox_error,
    }


@router.post("/{image_id}/tenants")
async def assign_tenants(
    image_id: int,
    data: AssignTenantsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """Super admin: assign image to tenants."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")

    image = db.query(ImageTemplate).filter_by(id=image_id).first()
    if not image:
        raise HTTPException(404, "Image not found")

    from app.models.tenant import Tenant
    for tid in data.tenant_ids:
        tenant = db.query(Tenant).filter_by(id=tid).first()
        if not tenant:
            raise HTTPException(404, f"Tenant {tid} not found")

        existing = db.query(TenantImage).filter_by(image_id=image_id, tenant_id=tid).first()
        if existing:
            existing.is_active = True
        else:
            db.add(TenantImage(image_id=image_id, tenant_id=tid, is_active=True))

    db.commit()

    log_audit_event(
        db=db, action=AUDIT_ACTIONS["IMAGE_ASSIGN"], target_type="image_template",
        actor_id=current_user.id, actor_username=current_user.username,
        target_id=image.id, target_name=image.name,
        details=f"tenant_ids={data.tenant_ids}",
        ip_address=None, tenant_id=None,
    )
    db.commit()

    return {"message": f"Image assigned to {len(data.tenant_ids)} tenants"}


@router.delete("/{image_id}/tenants/{tenant_id}")
async def unassign_tenant(
    image_id: int,
    tenant_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
):
    """Super admin: remove tenant assignment."""
    if not is_super_admin(current_user, db):
        raise HTTPException(403, "Super admin only")

    assignment = db.query(TenantImage).filter_by(image_id=image_id, tenant_id=tenant_id).first()
    if not assignment:
        raise HTTPException(404, "Assignment not found")

    image = db.query(ImageTemplate).filter_by(id=image_id).first()
    db.delete(assignment)
    db.commit()

    log_audit_event(
        db=db, action=AUDIT_ACTIONS["IMAGE_UNASSIGN"], target_type="image_template",
        actor_id=current_user.id, actor_username=current_user.username,
        target_id=image_id, target_name=image.name if image else str(image_id),
        details=f"tenant_id={tenant_id}",
        ip_address=None, tenant_id=None,
    )
    db.commit()

    return {"message": "Tenant unassigned successfully"}


# ═══════════════════════════════════════════════════════════
# TENANT-SCOPED ROUTES
# ═══════════════════════════════════════════════════════════

@router.get("/tenants/{tenant_id}/images")
async def tenant_images(
    tenant_id: int,
    category: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user),
    current_tenant: Tenant = Depends(get_current_tenant),
):
    """Tenant member: images visible to this tenant (public + assigned)."""
    if tenant_id != current_tenant.id and not is_super_admin(current_user, db):
        raise HTTPException(403, "Access denied to this tenant's images")

    assigned_ids = db.query(TenantImage.image_id).filter(
        TenantImage.tenant_id == tenant_id,
        TenantImage.is_active == True,
    ).scalar_subquery()

    images = db.query(ImageTemplate).filter(
        ImageTemplate.is_active == True,
        or_(
            ImageTemplate.is_public == True,
            ImageTemplate.id.in_(assigned_ids),
        ),
    )
    if category:
        images = images.filter(ImageTemplate.category == category)

    images = images.all()

    return [
        {
            "id": img.id,
            "name": img.name,
            "category": img.category,
            "os_type": img.os_type,
            "description": img.description,
            "recommended_cpu": img.recommended_cpu,
            "recommended_ram_mb": img.recommended_ram_mb,
            "recommended_disk_gb": img.recommended_disk_gb,
            "provisioning_notes": img.provisioning_notes,
            "tags": img.tags,
            "template_id": img.template_id,
        }
        for img in images
    ]
