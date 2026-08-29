from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base


class BridgePool(Base):
    __tablename__ = "bridge_pool"

    bridge_id = Column(Integer, primary_key=True)
    status = Column(String, default="available")
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=True)
    allocated_at = Column(DateTime, nullable=True)
