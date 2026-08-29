"""
WireGuard VPN models.

A tenant can have many WireGuardTunnel rows (each is its own OPNsense WG instance
with its own /24, listen port, set of peers). Each tunnel has many WireGuardPeer
rows. Subnet allocation is handled by `app.services.wireguard_ipam` against the
`wireguard_ip_pool` table.

Sensitive fields on WireGuardPeer (`private_key`, `preshared_key`) are stored
encrypted via `app.core.crypto` and are never returned in plain text on list /
get endpoints. The .conf file is generated server-side on demand and returned
through a dedicated endpoint.
"""
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, JSON,
    UniqueConstraint, Index, func
)
from sqlalchemy.orm import relationship
from app.core.database import Base


class WireGuardPool(Base):
    """Global pool of /24 subnets available for WireGuard tunnels."""
    __tablename__ = "wireguard_ip_pool"

    id = Column(Integer, primary_key=True)
    cidr = Column(String, unique=True, nullable=False)
    gateway_ip = Column(String, nullable=False)
    status = Column(String, default="free")
    wireguard_tunnel_id = Column(Integer, ForeignKey("wireguard_tunnels.id"), nullable=True)
    allocated_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_wg_ip_pool_status", "status"),
    )


class WireGuardTunnel(Base):
    __tablename__ = "wireguard_tunnels"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)

    name = Column(String(100), nullable=False)
    opnsense_server_uuid = Column(String(36), nullable=True, index=True)

    listen_port = Column(Integer, default=51820)
    mtu = Column(Integer, default=1420)
    dns = Column(String(200), nullable=True)

    tunnel_address = Column(String(64), nullable=False)
    cidr = Column(String, nullable=False)
    gateway_ip = Column(String, nullable=False)
    subnet_mask = Column(Integer, default=24)
    pool_id = Column(Integer, ForeignKey("wireguard_ip_pool.id"), nullable=True)

    public_key = Column(String(64), nullable=False)
    private_key = Column(String(200), nullable=False)

    endpoint = Column(String(200), nullable=True)

    opt_interface = Column(String(20), nullable=True)
    status = Column(String, default="pending")
    error = Column(String(500), nullable=True)
    peer_keepalive = Column(Integer, default=25)
    is_enabled = Column(Boolean, default=True)
    allowed_network_ids = Column(JSON, nullable=True, default=list)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    provisioned_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_wg_tunnel_tenant_name"),
        Index("ix_wg_tunnels_tenant_status", "tenant_id", "status"),
    )

    peers = relationship(
        "WireGuardPeer",
        back_populates="tunnel",
        cascade="all, delete-orphan",
        order_by="WireGuardPeer.id",
    )


class WireGuardPeer(Base):
    __tablename__ = "wireguard_peers"

    id = Column(Integer, primary_key=True)
    tunnel_id = Column(Integer, ForeignKey("wireguard_tunnels.id", ondelete="CASCADE"), nullable=False, index=True)
    opnsense_client_uuid = Column(String(36), nullable=True, index=True)

    name = Column(String(100), nullable=False)
    public_key = Column(String(64), nullable=False)
    private_key_enc = Column(String(500), nullable=False)
    preshared_key_enc = Column(String(500), nullable=False)
    allowed_ip = Column(String(64), nullable=False)

    endpoint = Column(String(200), nullable=True)
    keepalive = Column(Integer, default=25)
    is_enabled = Column(Boolean, default=True)
    status = Column(String, default="pending")
    error = Column(String(500), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("tunnel_id", "name", name="uq_wg_peer_tunnel_name"),
        Index("ix_wg_peers_tunnel", "tunnel_id"),
    )

    tunnel = relationship("WireGuardTunnel", back_populates="peers")
