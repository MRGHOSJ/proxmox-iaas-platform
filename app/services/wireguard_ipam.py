"""
WireGuard IPAM.

- allocate_tunnel_subnet(db) -> WireGuardPool row
    Picks the next free /24 from `wireguard_ip_pool` using
    `SELECT FOR UPDATE SKIP LOCKED` to prevent race conditions.
    Pool rows are seeded on first startup by `app.services.seed.seed_wireguard_pool`
    (called from the FastAPI lifespan handler).

- release_tunnel_subnet(db, pool_id) -> None
    Returns a /24 to the free pool. Called when a WireGuardTunnel is destroyed.

- allocate_peer_ip(db, tunnel) -> str
    Sequential /32 picked from the tunnel's /24 starting at gateway_ip + 1.
    Persists a high-water marker on the tunnel via the peer index column
    (we just use COUNT(peers) for simplicity — it is monotonically increasing
    and safe for /24s which give us 253 usable addresses).

- release_peer_ip is a no-op (peer IPs are not returned to the pool on delete
    to avoid renumbering existing peers).
"""
import ipaddress
import logging
from typing import Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.wireguard import WireGuardPool, WireGuardTunnel, WireGuardPeer

logger = logging.getLogger(__name__)


def allocate_tunnel_subnet(db: Session) -> WireGuardPool:
    """
    Allocate the next free /24 from the global WireGuard pool.
    Raises ValueError if the pool is exhausted or not seeded.
    """
    subnet = (
        db.query(WireGuardPool)
        .filter_by(status="free")
        .with_for_update(skip_locked=True)
        .first()
    )
    if not subnet:
        raise ValueError("WireGuard IP pool exhausted — add more rows to wireguard_ip_pool or expand WIREGUARD_GLOBAL_POOL_CIDR")
    subnet.status = "allocated"
    subnet.allocated_at = func.now()
    return subnet


def release_tunnel_subnet(db: Session, pool_id: int) -> None:
    """Return a /24 to the free pool. Idempotent."""
    subnet = db.query(WireGuardPool).filter_by(id=pool_id).first()
    if subnet:
        subnet.status = "free"
        subnet.wireguard_tunnel_id = None
        subnet.allocated_at = None


def allocate_peer_ip(db: Session, tunnel: WireGuardTunnel) -> str:
    """
    Allocate the next /32 inside the tunnel's /24.

    Order:
      1. gateway_ip + 1, +2, ... (skipping already-allocated addresses).
    Returns the address WITHOUT /32 suffix (e.g. "10.200.0.2").
    Raises ValueError when the /24 is exhausted.
    """
    network = ipaddress.ip_network(tunnel.cidr, strict=False)
    base = ".".join(tunnel.gateway_ip.split("/")[0].split(".")[:3])

    existing_ips = set()
    for p in db.query(WireGuardPeer).filter(
        WireGuardPeer.tunnel_id == tunnel.id
    ).all():
        if p.allowed_ip and "/" in p.allowed_ip:
            try:
                existing_ips.add(int(p.allowed_ip.split("/")[0].rsplit(".", 1)[1]))
            except (IndexError, ValueError):
                pass

    last_octet = int(tunnel.gateway_ip.split("/")[0].rsplit(".", 1)[1])
    for octet in range(last_octet + 1, 255):
        if octet in existing_ips:
            continue
        if ipaddress.ip_address(f"{base}.{octet}") in network:
            return f"{base}.{octet}"

    raise ValueError(f"WireGuard tunnel {tunnel.id} subnet {tunnel.cidr} is full")


def compute_tunnel_address_for_subnet(cidr: str, gateway_ip: str, index: int) -> str:
    """
    Build a /32 tunnel-address string `gateway_ip + index`.

    Used when caller wants an explicit server address; index defaults to 0
    meaning "use the gateway itself".
    """
    base = gateway_ip.rsplit(".", 1)[0]
    last = int(gateway_ip.rsplit(".", 1)[1])
    return f"{base}.{last}/32"
