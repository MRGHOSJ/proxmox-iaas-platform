from pydantic import BaseModel
from typing import Optional


class TenantNetworkCreate(BaseModel):
    name: str


class TenantNetworkResponse(BaseModel):
    id: int
    tenant_id: int
    pod_id: int
    ip_pool_id: Optional[int]
    cidr: str
    gateway_ip: str
    vlan_id: Optional[int]
    name: str
    is_default: bool
    status: str
    provider_ref: Optional[str]
    opnsense_interface: Optional[str] = None

    class Config:
        from_attributes = True


class TenantNetworkListResponse(BaseModel):
    total: int
    networks: list[TenantNetworkResponse]
