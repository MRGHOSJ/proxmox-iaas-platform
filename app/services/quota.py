from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
import logging

from app.models.vm import VM
from app.models.network import TenantNetwork
from app.models.tenant import Tenant
from app.schemas.tenant import QuotaSettings

logger = logging.getLogger(__name__)


class QuotaExceededError(Exception):
    def __init__(self, resource: str, limit: int, current: int, requested: int):
        self.resource = resource
        self.limit = limit
        self.current = current
        self.requested = requested
        super().__init__(
            f"Quota exceeded for {resource}: limit={limit}, current={current}, requested={requested}"
        )


def get_quota_settings(tenant_id: int, db: Session) -> QuotaSettings:
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if not tenant:
        return QuotaSettings()
    return QuotaSettings.from_settings_json(tenant.settings)


def get_current_usage(tenant_id: int, db: Session) -> dict:
    vm_count = db.query(func.count(VM.id)).filter(VM.tenant_id == tenant_id).scalar() or 0
    cpu_cores = db.query(func.coalesce(func.sum(VM.cpu), 0)).filter(VM.tenant_id == tenant_id).scalar() or 0
    ram_mb = db.query(func.coalesce(func.sum(VM.ram), 0)).filter(VM.tenant_id == tenant_id).scalar() or 0
    disk_mb = db.query(func.coalesce(func.sum(VM.disk_size_mb), 0)).filter(VM.tenant_id == tenant_id).scalar() or 0
    disk_gb = round(disk_mb / 1024, 1)
    network_count = db.query(func.count(TenantNetwork.id)).filter(
        TenantNetwork.tenant_id == tenant_id,
        TenantNetwork.status == "active"
    ).scalar() or 0
    
    return {
        "vm_count": vm_count,
        "cpu_cores": cpu_cores,
        "ram_mb": ram_mb,
        "disk_mb": disk_mb,
        "disk_gb": disk_gb,
        "network_count": network_count
    }


def check_vm_quota(
    tenant_id: int,
    db: Session,
    cpu: int = 0,
    ram: int = 0,
    disk_size: int = 0
) -> None:
    quota = get_quota_settings(tenant_id, db)
    usage = get_current_usage(tenant_id, db)
    
    if quota.max_vms is not None:
        if usage["vm_count"] + 1 > quota.max_vms:
            raise QuotaExceededError("max_vms", quota.max_vms, usage["vm_count"], 1)
    
    if quota.max_cpu_cores is not None:
        if usage["cpu_cores"] + cpu > quota.max_cpu_cores:
            raise QuotaExceededError("max_cpu_cores", quota.max_cpu_cores, usage["cpu_cores"], cpu)
    
    if quota.max_ram_mb is not None:
        if usage["ram_mb"] + ram > quota.max_ram_mb:
            raise QuotaExceededError("max_ram_mb", quota.max_ram_mb, usage["ram_mb"], ram)
    
    if quota.max_disk_gb is not None:
        if usage["disk_gb"] + disk_size > quota.max_disk_gb:
            raise QuotaExceededError("max_disk_gb", quota.max_disk_gb, usage["disk_gb"], disk_size)


def check_network_quota(tenant_id: int, db: Session) -> None:
    quota = get_quota_settings(tenant_id, db)
    usage = get_current_usage(tenant_id, db)
    
    if quota.max_networks is not None:
        if usage["network_count"] + 1 > quota.max_networks:
            raise QuotaExceededError("max_networks", quota.max_networks, usage["network_count"], 1)


def check_disk_resize_quota(tenant_id: int, additional_mib: int, db: Session) -> None:
    """
    Check if tenant can add more disk space via resize.
    
    Args:
        tenant_id: The tenant ID
        additional_mib: Additional disk space in MiB being added
        db: Database session
    
    Raises:
        QuotaExceededError: If quota would be exceeded
    """
    quota = get_quota_settings(tenant_id, db)
    usage = get_current_usage(tenant_id, db)
    
    if quota.max_disk_gb is not None:
        current_disk_mib = usage["disk_mb"]
        new_total_mib = current_disk_mib + additional_mib
        new_total_gb = round(new_total_mib / 1024, 1)
        
        if new_total_gb > quota.max_disk_gb:
            raise QuotaExceededError(
                "max_disk_gb",
                quota.max_disk_gb,
                usage["disk_gb"],
                round(additional_mib / 1024, 1)
            )


def get_quota_status(tenant_id: int, db: Session) -> dict:
    quota = get_quota_settings(tenant_id, db)
    usage = get_current_usage(tenant_id, db)
    
    def calc_remaining(limit: Optional[int], current: int) -> Optional[int]:
        if limit is None:
            return None
        return max(0, limit - current)
    
    def calc_percentage(limit: Optional[int], current: int) -> Optional[float]:
        if limit is None or limit == 0:
            return None
        return round((current / limit) * 100, 1)
    
    return {
        "quota": quota,
        "usage": usage,
        "remaining": {
            "vm_count": calc_remaining(quota.max_vms, usage["vm_count"]),
            "cpu_cores": calc_remaining(quota.max_cpu_cores, usage["cpu_cores"]),
            "ram_mb": calc_remaining(quota.max_ram_mb, usage["ram_mb"]),
            "disk_gb": calc_remaining(quota.max_disk_gb, usage["disk_gb"]),
            "network_count": calc_remaining(quota.max_networks, usage["network_count"])
        },
        "percentage": {
            "vm_count": calc_percentage(quota.max_vms, usage["vm_count"]),
            "cpu_cores": calc_percentage(quota.max_cpu_cores, usage["cpu_cores"]),
            "ram_mb": calc_percentage(quota.max_ram_mb, usage["ram_mb"]),
            "disk_gb": calc_percentage(quota.max_disk_gb, usage["disk_gb"]),
            "network_count": calc_percentage(quota.max_networks, usage["network_count"])
        }
    }
