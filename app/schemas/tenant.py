from typing import Optional, List
from pydantic import BaseModel, field_validator
from datetime import datetime
import json


class QuotaSettings(BaseModel):
    max_vms: Optional[int] = None
    max_cpu_cores: Optional[int] = None
    max_ram_mb: Optional[int] = None
    max_disk_gb: Optional[int] = None
    max_networks: Optional[int] = None

    @field_validator('max_vms', 'max_cpu_cores', 'max_ram_mb', 'max_disk_gb', 'max_networks', mode='before')
    @classmethod
    def validate_positive_or_null(cls, v):
        if v is not None and v < 0:
            raise ValueError('Value must be positive or null')
        return v

    def to_settings_json(self) -> str:
        return json.dumps(self.model_dump(exclude_none=False))

    @classmethod
    def from_settings_json(cls, settings_json: Optional[str]) -> "QuotaSettings":
        if not settings_json:
            return cls()
        try:
            data = json.loads(settings_json)
            return cls(**{k: v for k, v in data.items() if k in cls.model_fields})
        except (json.JSONDecodeError, TypeError):
            return cls()


class TenantVerifyRequest(BaseModel):
    quota: QuotaSettings


class QuotaUpdate(BaseModel):
    max_vms: Optional[int] = None
    max_cpu_cores: Optional[int] = None
    max_ram_mb: Optional[int] = None
    max_disk_gb: Optional[int] = None
    max_networks: Optional[int] = None

    @field_validator('max_vms', 'max_cpu_cores', 'max_ram_mb', 'max_disk_gb', 'max_networks', mode='before')
    @classmethod
    def validate_positive_or_null(cls, v):
        if v is not None and v < 0:
            raise ValueError('Value must be positive or null')
        return v


class TenantQuotaResponse(BaseModel):
    tenant_id: int
    quota: QuotaSettings
    current_usage: dict


class TenantApproveRequest(BaseModel):
    template_vm_id: Optional[int] = 9000
    dhcp_pool_start: Optional[str] = None
    dhcp_pool_end: Optional[str] = None


class TenantApproveResponse(BaseModel):
    tenant_id: int
    status: str
    bridge_id: Optional[int] = None
    opnsense_vm_id: Optional[int] = None
    opnsense_vm_name: Optional[str] = None
    bridge_name: Optional[str] = None
    lan_ip: Optional[str] = None
    message: str


class TenantResponse(BaseModel):
    id: int
    name: str
    slug: str
    is_active: bool
    is_verified: bool
    status: str
    bridge_id: Optional[int] = None
    opnsense_vm_id: Optional[int] = None
    opnsense_vm_name: Optional[str] = None
    lan_ip: Optional[str] = None
    wan_ip: Optional[str] = None
    wan_bridge: Optional[str] = None
    provisioned_at: Optional[datetime] = None
    opnsense_api_key: Optional[str] = None
    dhcp_pool_start: Optional[str] = None
    dhcp_pool_end: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
