from app.core.database import Base
from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey
from sqlalchemy.sql import func


class AuditLog(Base):
    """
    Immutable audit log for sensitive operations.
    Records user actions for security and compliance purposes.
    """
    __tablename__ = "audit_logs"
    
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    
    actor_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_username = Column(String(50), nullable=False)
    
    action = Column(String(50), nullable=False, index=True)
    
    target_type = Column(String(50), nullable=False)
    target_id = Column(Integer, nullable=True)
    target_name = Column(String(255), nullable=True)
    
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    
    details = Column(Text, nullable=True)
    request_id = Column(String(36), nullable=True)
    ip_address = Column(String(45), nullable=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True)
