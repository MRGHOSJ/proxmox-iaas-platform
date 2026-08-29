from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.core.database import Base


class ImageTemplate(Base):
    __tablename__ = "image_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)
    provider = Column(String, nullable=False, index=True)
    template_id = Column(String, nullable=False)
    category = Column(String, nullable=False, default="client_vm", index=True)
    description = Column(Text, nullable=True)
    version = Column(String, nullable=True)
    os_type = Column(String, nullable=True)
    tags = Column(JSON, nullable=True)

    recommended_cpu = Column(Integer, default=2)
    recommended_ram_mb = Column(Integer, default=4096)
    recommended_disk_gb = Column(Integer, default=20)
    provisioning_notes = Column(Text, nullable=True)

    is_active = Column(Boolean, default=True)
    is_public = Column(Boolean, default=False)
    api_enabled = Column(Boolean, default=False)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    tenant_assignments = relationship("TenantImage", back_populates="image", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("provider", "template_id", name="uq_provider_template"),
    )


class TenantImage(Base):
    __tablename__ = "tenant_images"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    image_id = Column(Integer, ForeignKey("image_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    tenant = relationship("Tenant", back_populates="image_assignments")
    image = relationship("ImageTemplate", back_populates="tenant_assignments")

    __table_args__ = (
        UniqueConstraint("tenant_id", "image_id", name="uq_tenant_image"),
    )


class ImageBuild(Base):
    __tablename__ = "image_builds"

    id = Column(Integer, primary_key=True, index=True)
    vmid = Column(Integer, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)
    node = Column(String, nullable=False)
    storage = Column(String, nullable=False)
    iso_volid = Column(String, nullable=True)
    iso_url = Column(String, nullable=True)
    download_upid = Column(String, nullable=True)
    description = Column(Text, nullable=True)
    recommended_cpu = Column(Integer, default=2)
    recommended_ram_mb = Column(Integer, default=4096)
    recommended_disk_gb = Column(Integer, default=20)
    status = Column(String, default="downloading_iso", index=True)
    celery_task_id = Column(String, nullable=True)
    download_only = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
