from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from app.core.database import Base


class FirewallProviderConfig(Base):
    __tablename__ = "firewall_provider_configs"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    provider_type = Column(String(20), nullable=False, index=True)
    vm_id = Column(Integer, nullable=True)
    api_key = Column(String(255), nullable=True)
    api_secret = Column(String(255), nullable=True)
    base_url = Column(String(255), nullable=True)
    is_active = Column(String(1), default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    tenant = relationship("Tenant", back_populates="firewall_configs")

    def __repr__(self):
        return f"<FirewallProviderConfig tenant={self.tenant_id} type={self.provider_type} active={self.is_active}>"