from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from app.core.database import Base


class TenantStatus:
    PENDING = "pending"
    PENDING_APPROVAL = "pending_approval"
    VERIFIED = "verified"
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEPROVISIONED = "deprovisioned"
    ERROR = "error"


class Tenant(Base):
    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    slug = Column(String(50), unique=True, nullable=False, index=True)
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False, index=True)
    status = Column(String, default=TenantStatus.PENDING_APPROVAL)
    settings = Column(String, default="{}")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    bridge_id = Column(Integer, ForeignKey("bridge_pool.bridge_id"), nullable=True)
    pod_id = Column(Integer, ForeignKey("pods.id"), nullable=True)
    opnsense_vm_id = Column(Integer, nullable=True)
    opnsense_vm_name = Column(String, nullable=True)
    lan_ip = Column(String, nullable=True)
    wan_ip = Column(String, nullable=True)
    wan_ip_last_changed_at = Column(DateTime(timezone=True), nullable=True)
    fixed_wan_ip = Column(String, nullable=True)
    fixed_wan_subnet = Column(Integer, default=24)
    fixed_wan_gateway = Column(String, nullable=True)
    wan_bridge = Column(String, default="vmbr0")
    provisioned_at = Column(DateTime(timezone=True), nullable=True)

    dhcp_pool_start = Column(String, nullable=True)
    dhcp_pool_end = Column(String, nullable=True)

    opnsense_api_key = Column(String, nullable=True)
    opnsense_api_secret = Column(String, nullable=True)
    error = Column(String, nullable=True)

    opnsense_interface_list = Column(String, nullable=True)
    opnsense_interface_cached_at = Column(DateTime(timezone=True), nullable=True)

    users = relationship("User", back_populates="tenant")
    vms = relationship("VM", back_populates="tenant")
    networks = relationship("TenantNetwork", back_populates="tenant")
    
    snapshots = relationship("VMSnapshot", back_populates="tenant")
    roles = relationship("Role", back_populates="tenant")
    user_roles = relationship("UserRole", back_populates="tenant")
    firewall_configs = relationship("FirewallProviderConfig", back_populates="tenant", cascade="all, delete-orphan")
    opnsense_firewall_rules = relationship("OPNsenseFirewallRule", back_populates="tenant", cascade="all, delete-orphan")
    image_assignments = relationship("TenantImage", back_populates="tenant")