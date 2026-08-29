from pydantic import BaseModel, ConfigDict, Field, field_validator, field_serializer, model_validator
from typing import Literal, Optional
from datetime import datetime
import re
import shlex


SHELL_SAFE_PATTERN = re.compile(r'^[a-z][a-z0-9_-]{2,49}$')

VALID_PROVIDERS = ["docker", "vsphere", "proxmox"]


class VMBase(BaseModel):
    """Base schema with common VM fields"""
    name: str = Field(..., min_length=3, max_length=50, description="VM name (unique)")
    description: Optional[str] = Field(None, max_length=500, description="VM description")
    cpu: int = Field(2, ge=1, le=32, description="Number of CPU cores")
    ram: int = Field(4096, ge=512, le=65536, description="RAM in MB")
    provider: Literal["docker", "vsphere", "proxmox"] = Field("docker", description="Infrastructure provider")
    image: Optional[str] = Field(None, max_length=255, description="Container/VM image (e.g., nginx:latest)")
    
    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        """Ensure VM name is alphanumeric with hyphens/underscores only, safe for shell"""
        if not v:
            raise ValueError('VM name cannot be empty')
        
        normalized = v.lower()
        
        if not SHELL_SAFE_PATTERN.match(normalized):
            raise ValueError(
                'VM name must start with lowercase letter, contain only lowercase letters, '
                'numbers, hyphens, and underscores (3-50 characters)'
            )
        if normalized.startswith('-') or normalized.endswith('-') or normalized.startswith('_') or normalized.endswith('_'):
            raise ValueError('VM name cannot start or end with hyphens or underscores')
        if '--' in normalized or '__' in normalized:
            raise ValueError('VM name cannot contain consecutive hyphens or underscores')
        
        if shlex.quote(normalized) != normalized:
            raise ValueError('VM name contains unsafe characters for shell execution')
        
        return normalized
    
    @field_validator('provider')
    @classmethod
    def validate_provider(cls, v):
        """Ensure provider is a valid provider type."""
        if v not in VALID_PROVIDERS:
            raise ValueError(f"Provider must be one of: {', '.join(VALID_PROVIDERS)}")
        return v


class VMCreate(VMBase):
    """Schema for creating a new VM"""
    network_id: Optional[int] = Field(None, description="ID of the Network to attach this VM to")


class VMUpdate(BaseModel):
    """Schema for updating VM metadata (partial updates allowed)"""
    model_config = ConfigDict(extra="forbid")
    
    description: Optional[str] = Field(None, max_length=500)


class VMStatusUpdate(BaseModel):
    """Schema for admin-only status override"""
    status: Literal["pending", "creating", "provisioning", "running", "stopped", "error"]
    reason: str = Field(..., min_length=10, max_length=500, description="Required reason for status override (audit trail)")
    force: bool = Field(False, description="Force status change even if transition is normally invalid")


class VMResponse(VMBase):
    """Schema for VM responses (includes DB-generated fields)"""
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    ip_address: Optional[str] = None
    status: str
    network_id: Optional[int] = None
    terraform_job_id: Optional[str] = None
    celery_task_id: Optional[str] = None
    proxmox_vm_id: Optional[int] = None
    error: Optional[str] = None
    owner_id: int
    created_at: datetime
    
    disk_size_gb: Optional[float] = None
    disk_size_mb: Optional[int] = None
    template_id: Optional[int] = None
    ssh_user: Optional[str] = None
    ssh_public_key: Optional[str] = None
    
    @model_validator(mode='after')
    def compute_disk_size_gb(self):
        if self.disk_size_gb is None and self.disk_size_mb:
            self.disk_size_gb = round(self.disk_size_mb / 1024, 1)
        return self

class VMListResponse(BaseModel):
    """Schema for paginated VM list responses"""
    total: int
    vms: list[VMResponse]
    offset: int
    limit: int


class VMStatsResponse(BaseModel):
    """Schema for VM statistics dashboard"""
    total_vms: int
    status_breakdown: dict[str, int]
    provider_breakdown: dict[str, int]
    cpu_total: int
    ram_total_mb: int
    disk_total_gb: float


class VMLogsResponse(BaseModel):
    """Schema for VM logs"""
    vm_id: int
    vm_name: str
    logs: str
    lines: int


class VMSnapshotBase(BaseModel):
    """Base schema for VM snapshots"""
    name: str = Field(..., min_length=3, max_length=100, description="Snapshot name")
    description: Optional[str] = Field(None, max_length=500, description="Snapshot description")
    
    @field_validator('name')
    @classmethod
    def validate_name(cls, v):
        if not v:
            raise ValueError('Snapshot name cannot be empty')
        normalized = v.lower()
        if not SHELL_SAFE_PATTERN.match(normalized):
            raise ValueError(
                'Snapshot name must start with lowercase letter, contain only lowercase letters, '
                'numbers, hyphens, and underscores (3-100 characters)'
            )
        return normalized


class VMSnapshotCreate(VMSnapshotBase):
    """Schema for creating a snapshot"""
    pass


class VMSnapshotResponse(VMSnapshotBase):
    """Schema for snapshot responses"""
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    vm_id: int
    image_tag: str
    container_config: Optional[str] = None
    created_at: datetime
    created_by: Optional[int] = None


class VMSnapshotRestore(BaseModel):
    """Schema for restoring a snapshot"""
    snapshot_id: int = Field(..., description="ID of the snapshot to restore")


class VMProvisionRequest(BaseModel):
    """Schema for provisioning a Proxmox VM with cloud-init"""
    name: str = Field(..., min_length=3, max_length=50, description="VM name")
    template_id: int = Field(..., description="Proxmox template VM ID")
    
    cpu: int = Field(1, ge=1, le=32, description="Number of CPU cores")
    ram: int = Field(1024, ge=512, le=65536, description="RAM in MB")
    
    username: str = Field("ubuntu", description="Cloud-init username")
    password: Optional[str] = Field(None, description="Cloud-init password (will be hashed)")
    
    ip_mode: Literal["dhcp", "static"] = Field("dhcp", description="IP configuration mode")
    ip_address: Optional[str] = Field(None, description="Static IP address (required if ip_mode is static)")
    dns_nameservers: list[str] = Field(["8.8.8.8", "8.8.4.4"], description="DNS nameservers")
    dns_search: Optional[str] = Field(None, description="DNS search domain")
    
    ssh_public_key: Optional[str] = Field(None, description="SSH public key for cloud-init")
    package_upgrade: bool = Field(False, description="Whether to upgrade packages on first boot")
    auto_start: bool = Field(True, description="Whether to start the VM after provisioning")
    
    description: Optional[str] = Field(None, max_length=500, description="VM description")
    
    network_id: Optional[int] = Field(None, description="Tenant network ID (if None, uses default network)")
    
    disk_size_gb: Optional[int] = Field(None, ge=1, le=10000, description="Target disk size in GB (min = template size)")
    
    skip_cloudinit: bool = Field(False, description="Skip cloud-init configuration (for Windows VMs that don't support cloud-init)")
    
    @field_validator('ip_address')
    @classmethod
    def validate_static_ip(cls, v, info):
        if info.data.get('ip_mode') == 'static' and not v:
            raise ValueError('ip_address required when ip_mode is static')
        return v


class DiskResizeRequest(BaseModel):
    """Schema for resizing a VM disk"""
    disk: str = Field("scsi0", description="Disk identifier (e.g., scsi0, virtio0)")
    size: str = Field(..., description="Relative size to add (e.g., +10G, +512M). Must start with +")
    restart_after_resize: bool = Field(False, description="Restart VM after resize to apply changes")
    
    @field_validator('size')
    @classmethod
    def validate_size_format(cls, v):
        if not v.startswith('+'):
            raise ValueError('Size must be a relative format (e.g., +10G, +512M). Must start with +')
        return v


class DiskResizeResponse(BaseModel):
    """Schema for disk resize response"""
    disk_id: str
    previous_size_mib: int
    new_size_mib: int
    previous_size_gb: float
    new_size_gb: float
    status: str = "resized"
    restarted: bool = False


class VMDiskInfo(BaseModel):
    """Schema for VM disk information"""
    id: str
    storage: str
    volume: str
    size_mib: int
    size_gb: float


class StorageInfoResponse(BaseModel):
    """Schema for storage information"""
    model_config = ConfigDict(from_attributes=True)
    
    total_gb: float
    free_gb: float
    used_gb: float
    content: str = ""


class VMResourcesResponse(BaseModel):
    """Schema for VM resources response"""
    cpu_cores: int
    memory_mb: int
    memory_gb: float
    disks: dict
    digest: Optional[str] = None
    name: Optional[str] = None
    status: str = "running"


class ResourceResizeRequest(BaseModel):
    """Schema for resource resize request"""
    restart_after_resize: bool = True


class CPUResizeRequest(ResourceResizeRequest):
    """Schema for CPU resize request"""
    cores: int = Field(..., ge=1, le=32, description="Number of CPU cores")


class RAMResizeRequest(ResourceResizeRequest):
    """Schema for RAM resize request"""
    memory_mb: int = Field(..., ge=512, le=65536, description="RAM in MB")


class ResourceResizeResponse(BaseModel):
    """Schema for resource resize response"""
    resource_type: str
    previous_value: int
    new_value: int
    status: str = "resized"
    restarted: bool = False


class SshInfoResponse(BaseModel):
    """Schema for SSH connection info"""
    vm_id: int
    vm_name: str
    ssh_user: str
    ip_address: Optional[str] = None
    ssh_public_key: Optional[str] = None
    ssh_command: Optional[str] = None
    has_private_key: bool = False


class SshKeyRegenerateResponse(BaseModel):
    """Schema for SSH key regeneration response"""
    vm_id: int
    ssh_user: str
    ssh_public_key: str
    ssh_private_key: str
    ssh_command: str
    warning: str = "Keep this key secure. Anyone with this file can access your VM."