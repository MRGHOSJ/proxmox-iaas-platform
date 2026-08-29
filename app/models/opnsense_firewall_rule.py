from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func, Text
from sqlalchemy.orm import relationship
from app.core.database import Base


class OPNsenseFirewallRule(Base):
    __tablename__ = "opnsense_firewall_rules"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    uuid = Column(String(36), unique=True, nullable=False, index=True)
    sequence = Column(Integer, default=100)
    enabled = Column(String(1), default="1")
    description = Column(String(500))
    interface = Column(String(20))
    interfacenot = Column(String(1), default="0")
    quick = Column(String(1), default="1")
    action = Column(String(10))
    direction = Column(String(3))
    ipprotocol = Column(String(10))
    protocol = Column(String(10))
    source_not = Column(String(1), default="0")
    source_net = Column(String(100))
    source_port = Column(String(50))
    destination_not = Column(String(1), default="0")
    destination_net = Column(String(100))
    destination_port = Column(String(50))
    gateway = Column(String(50))
    log = Column(String(1), default="0")
    statetype = Column(String(20))
    synced_at = Column(DateTime(timezone=True), nullable=True)
    apply_status = Column(String(20), default="synced")  # synced, pending, failed
    apply_error = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    tenant = relationship("Tenant", back_populates="opnsense_firewall_rules")

    def to_opnsense_payload(self) -> dict:
        proto = (self.protocol or "tcp").lower()
        tcp_udp = proto in ("tcp", "udp", "tcp/udp")
        src_port = self.source_port if (self.source_port and tcp_udp and self.source_port != "any") else ""
        dst_port = self.destination_port if (self.destination_port and tcp_udp and self.destination_port != "any") else ""
        return {
            "enabled": self.enabled,
            "sequence": str(self.sequence),
            "nosync": "0",
            "description": self.description or "",
            "interface": self.interface or "lan",
            "interfacenot": self.interfacenot or "0",
            "quick": self.quick or "1",
            "action": self.action or "pass",
            "allowopts": "0",
            "direction": self.direction or "in",
            "ipprotocol": self.ipprotocol or "inet",
            "protocol": (self.protocol or "tcp").upper(),
            "icmptype": "",
            "icmp6type": "",
            "source_not": self.source_not or "0",
            "source_net": self.source_net or "any",
            "source_port": src_port,
            "destination_not": self.destination_not or "0",
            "destination_net": self.destination_net or "any",
            "destination_port": dst_port,
            "log": self.log or "0",
            "tcpflags1": "",
            "tcpflags2": "",
            "tcpflags_any": "0",
            "sched": "",
            "divert-to": "",
            "statetype": self.statetype or "keep",
            "state-policy": "",
            "nopfsync": "0",
            "statetimeout": "",
            "udp-first": "",
            "udp-single": "",
            "udp-multiple": "",
            "adaptivestart": "",
            "adaptiveend": "",
            "max": "",
            "max-src-nodes": "",
            "max-src-states": "",
            "max-src-conn": "",
            "max-src-conn-rate": "",
            "max-src-conn-rates": "",
            "overload": "",
            "shaper1": "",
            "shaper2": "",
            "gateway": self.gateway or "",
            "disablereplyto": "0",
            "replyto": "",
            "prio": "",
            "set-prio": "",
            "set-prio-low": "",
            "tos": "",
            "tag": "",
            "tagged": "",
        }

    def __repr__(self):
        return f"<OPNsenseFirewallRule id={self.id} uuid={self.uuid} action={self.action} desc={self.description}>"