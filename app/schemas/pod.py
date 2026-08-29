from pydantic import BaseModel


class PodCreate(BaseModel):
    name: str
    provider_type: str = "proxmox"
    node_names: str
    max_tenants: int = 100


class PodUpdate(BaseModel):
    name: str | None = None
    max_tenants: int | None = None
    status: str | None = None


class PodResponse(BaseModel):
    id: int
    name: str
    provider_type: str
    node_names: str
    max_tenants: int
    tenant_count: int
    status: str

    class Config:
        from_attributes = True


class PodListResponse(BaseModel):
    total: int
    pods: list[PodResponse]
