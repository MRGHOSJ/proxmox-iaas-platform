"""
Pydantic schemas for WireGuard tunnels and peers.

`tunnel_address` is the WG server's IP inside the tunnel (e.g. 10.200.0.1/24).
`cidr` is the tunnel subnet (e.g. 10.200.0.0/24). `gateway_ip` is the server IP
on the tunnel (e.g. 10.200.0.1).

`endpoint` is the public address clients connect to (auto-populated from
`tenant.wan_ip` if not provided at creation time).
"""
from typing import Optional
from pydantic import BaseModel, Field, field_validator
from datetime import datetime
import re


class WireGuardTunnelCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100, description="Tunnel name (lowercase a-z, 0-9, -)")
    listen_port: Optional[int] = Field(None, ge=1024, le=65535, description="UDP listen port (default 51820)")
    mtu: Optional[int] = Field(None, ge=1280, le=1500, description="MTU (default 1420)")
    dns: Optional[str] = Field(None, description="Comma-separated DNS servers for clients")
    endpoint: Optional[str] = Field(None, description="Public endpoint client config uses (auto from tenant WAN IP if blank)")
    tunnel_address: Optional[str] = Field(None, description="Server IP inside the tunnel, e.g. 10.200.0.1/24 (auto-allocated if blank)")
    peer_keepalive: Optional[int] = Field(None, ge=0, le=65535, description="Persistent keepalive seconds (default 25)")
    allowed_network_ids: list[int] = Field(
        ..., min_length=1,
        description="List of tenant network IDs the tunnel may access via firewall rules",
    )

    @field_validator("name")
    @classmethod
    def _name_format(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9][a-z0-9-]{1,98}[a-z0-9]$", v):
            raise ValueError("name must be lowercase letters, digits, and hyphens (2-100 chars)")
        return v


class WireGuardTunnelUpdate(BaseModel):
    name: Optional[str] = None
    mtu: Optional[int] = Field(None, ge=1280, le=1500)
    dns: Optional[str] = None
    endpoint: Optional[str] = None
    peer_keepalive: Optional[int] = Field(None, ge=0, le=65535)
    is_enabled: Optional[bool] = None
    allowed_network_ids: Optional[list[int]] = Field(
        None, min_length=1,
        description="Replace the tunnel's allowed network access list (firewall rules reconciled automatically)",
    )


class WireGuardTunnelResponse(BaseModel):
    id: int
    tenant_id: int
    name: str
    opnsense_server_uuid: Optional[str] = None
    listen_port: int
    mtu: int
    dns: Optional[str] = None
    tunnel_address: str
    cidr: str
    gateway_ip: str
    subnet_mask: int
    public_key: str
    endpoint: Optional[str] = None
    opt_interface: Optional[str] = None
    status: str
    error: Optional[str] = None
    peer_keepalive: int
    is_enabled: bool
    peer_count: int = 0
    allowed_network_ids: Optional[list[int]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    provisioned_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class WireGuardTunnelListResponse(BaseModel):
    total: int
    tunnels: list[WireGuardTunnelResponse]


class WireGuardPeerCreate(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    endpoint: Optional[str] = Field(None, description="Optional remote endpoint (host:port) for the peer")
    keepalive: Optional[int] = Field(None, ge=0, le=65535)

    @field_validator("name")
    @classmethod
    def _name_format(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9][a-z0-9-]{1,98}[a-z0-9]$", v):
            raise ValueError("name must be lowercase letters, digits, and hyphens (2-100 chars)")
        return v


class WireGuardPeerUpdate(BaseModel):
    name: Optional[str] = None
    endpoint: Optional[str] = None
    keepalive: Optional[int] = Field(None, ge=0, le=65535)
    is_enabled: Optional[bool] = None


class WireGuardPeerResponse(BaseModel):
    id: int
    tunnel_id: int
    name: str
    public_key: str
    allowed_ip: str
    endpoint: Optional[str] = None
    keepalive: int
    is_enabled: bool
    status: str = "pending"
    error: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class WireGuardPeerListResponse(BaseModel):
    total: int
    peers: list[WireGuardPeerResponse]


class WireGuardPeerConfigResponse(BaseModel):
    """Returned once on peer creation and on demand via /config."""
    peer_id: int
    tunnel_id: int
    name: str
    config: str
    client_private_key: str
    client_address: str
    server_public_key: str
    server_endpoint: str
    preshared_key: str
    allowed_ips: str
    dns: Optional[str] = None


class WireGuardStatusResponse(BaseModel):
    total_tunnels: int
    active_tunnels: int
    provisioning_tunnels: int
    error_tunnels: int
    total_peers: int
