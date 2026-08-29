from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime,
    ForeignKey, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship
from datetime import datetime
from app.core.database import Base


class Pod(Base):
    __tablename__ = "pods"

    id            = Column(Integer, primary_key=True)
    name          = Column(String, unique=True, nullable=False)
    provider_type = Column(String, default="proxmox")
    node_names    = Column(String)
    max_tenants   = Column(Integer, default=100)
    tenant_count  = Column(Integer, default=0)
    status        = Column(String, default="active")


class GlobalIPPool(Base):
    __tablename__ = "global_ip_pool"

    id                = Column(Integer, primary_key=True)
    cidr              = Column(String, unique=True, nullable=False)
    gateway_ip        = Column(String, nullable=False)
    pool              = Column(String, nullable=False)
    status            = Column(String, default="free")
    tenant_network_id = Column(Integer, ForeignKey("tenant_networks.id"), nullable=True)
    allocated_at      = Column(DateTime, nullable=True)

    __table_args__ = (
        Index("ix_global_ip_pool_pool_status", "pool", "status"),
    )


class VlanAllocation(Base):
    __tablename__ = "vlan_allocations"

    id                = Column(Integer, primary_key=True)
    pod_id            = Column(Integer, ForeignKey("pods.id"), nullable=False)
    vlan_id           = Column(Integer, nullable=False)
    status            = Column(String, default="free")
    tenant_network_id = Column(Integer, ForeignKey("tenant_networks.id"), nullable=True)

    __table_args__ = (
        UniqueConstraint("pod_id", "vlan_id", name="uq_vlan_pod"),
        Index("ix_vlan_allocations_pod_status", "pod_id", "status"),
    )


class TenantNetwork(Base):
    __tablename__ = "tenant_networks"

    id           = Column(Integer, primary_key=True)
    tenant_id    = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    pod_id       = Column(Integer, ForeignKey("pods.id"), nullable=False)
    ip_pool_id   = Column(Integer, ForeignKey("global_ip_pool.id"), nullable=True)

    cidr         = Column(String, nullable=False)
    gateway_ip   = Column(String, nullable=False)
    vlan_id      = Column(Integer, nullable=True)

    name         = Column(String, default="default")
    is_default   = Column(Boolean, default=False)
    status       = Column(String, default="active")

    provider_ref = Column(String, nullable=True)
    opnsense_interface = Column(String, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_tenant_networks_tenant_status",  "tenant_id", "status"),
        Index("ix_tenant_networks_tenant_default", "tenant_id", "is_default"),
    )

    tenant = relationship("Tenant", back_populates="networks")
