from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, Index
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.core.database import Base


class IPReservation(Base):
    """
    Tracks IP address reservations to prevent race conditions.
    An IP is reserved when allocated and released when the VM is created or fails.
    """
    __tablename__ = "ip_reservations"

    id = Column(Integer, primary_key=True, index=True)
    network_id = Column(Integer, ForeignKey("tenant_networks.id"), nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    ip_address = Column(String, nullable=False, index=True)
    vm_id = Column(Integer, ForeignKey("vms.id"), nullable=True)
    status = Column(String, default="reserved")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    expires_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index('ix_ip_reservations_network_status_expires', 'network_id', 'status', 'expires_at'),
        Index('ix_ip_reservations_network_ip', 'network_id', 'ip_address', unique=True),
    )

    tenant = relationship("Tenant", backref="ip_reservations")
