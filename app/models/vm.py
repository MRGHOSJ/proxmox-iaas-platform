from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.core.database import Base


VALID_STATUS_TRANSITIONS = {
    "pending": ["creating", "provisioning", "running", "error"],
    "creating": ["pending", "provisioning", "error"],
    "provisioning": ["running", "error"],
    "running": ["stopped", "error"],
    "stopped": ["running", "error"],
    "error": ["pending", "stopped"]
}


class VM(Base):
    __tablename__ = "vms"
    
    VALID_STATUS_TRANSITIONS = VALID_STATUS_TRANSITIONS

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    description = Column(String, nullable=True)
    provider = Column(String, default="proxmox", index=True)
    image = Column(String, nullable=True)
    cpu = Column(Integer)
    ram = Column(Integer)
    disk_size_mb = Column(Integer, default=0)
    ip_address = Column(String, nullable=True)
    status = Column(String, default="pending", index=True)
    terraform_job_id = Column(String, nullable=True)
    celery_task_id = Column(String, nullable=True)
    proxmox_vm_id = Column(Integer, nullable=True)
    template_id = Column(Integer, nullable=True)
    error = Column(String, nullable=True)
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    network_id = Column(Integer, ForeignKey("tenant_networks.id", ondelete="SET NULL"), nullable=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    ssh_user = Column(String(64), default="ubuntu")
    ssh_public_key = Column(Text, nullable=True)
    ssh_private_key_enc = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    owner = relationship("User", backref="vms")
    network = relationship("TenantNetwork", backref="vms")
    tenant = relationship("Tenant", back_populates="vms")
    snapshots = relationship("VMSnapshot", back_populates="vm", cascade="all, delete-orphan", lazy="dynamic")
    disk_resizes = relationship("VMDiskResize", back_populates="vm", cascade="all, delete-orphan", lazy="dynamic")


class VMSnapshot(Base):
    __tablename__ = "vm_snapshots"
    
    id = Column(Integer, primary_key=True, index=True)
    vm_id = Column(Integer, ForeignKey("vms.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    image_tag = Column(String, nullable=False)
    container_config = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    created_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    
    vm = relationship("VM", back_populates="snapshots")
    creator = relationship("User")
    tenant = relationship("Tenant", back_populates="snapshots")


class VMDiskResize(Base):
    __tablename__ = "vm_disk_resizes"
    
    id = Column(Integer, primary_key=True, index=True)
    vm_id = Column(Integer, ForeignKey("vms.id", ondelete="CASCADE"), nullable=False, index=True)
    disk_id = Column(String(20), nullable=False)
    previous_size_mib = Column(Integer, nullable=False)
    new_size_mib = Column(Integer, nullable=False)
    resized_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    vm = relationship("VM", back_populates="disk_resizes")
    user = relationship("User")