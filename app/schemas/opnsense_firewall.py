from pydantic import BaseModel, Field
from typing import Optional, Literal


class OPNsenseRuleBase(BaseModel):
    enabled: Literal["0", "1"] = Field("1", description="Rule enabled (1) or disabled (0)")
    sequence: Optional[str] = Field(None, description="Sort order/priority. Lower = higher in list.")
    description: str = Field(..., description="Rule label/description")
    interface: str = Field("lan", description="Interface key: lan, wan, opt1, opt2, ...")
    interfacenot: Literal["0", "1"] = Field("0", description="Invert interface match")
    quick: Literal["0", "1"] = Field("1", description="Stop evaluating further rules on match")
    action: Literal["pass", "block", "reject"] = Field("pass", description="pass=allow, block=drop, reject=deny")
    direction: Literal["in", "out"] = Field("in", description="in=filter arriving packets, out=outgoing")
    ipprotocol: Literal["inet", "inet6", "inet46"] = Field("inet", description="inet=IPv4, inet6=IPv6, inet46=both")
    protocol: str = Field("tcp", description="TCP, UDP, ICMP, or any")
    source_not: Literal["0", "1"] = Field("0", description="Invert source match")
    source_net: str = Field("any", description="Source address: any, alias name, or CIDR")
    source_port: str = Field("any", description="Source port: any, number, alias name, or range")
    destination_not: Literal["0", "1"] = Field("0", description="Invert destination match")
    destination_net: str = Field("any", description="Destination address: any, alias name, or CIDR")
    destination_port: str = Field("any", description="Destination port: any, number, alias, or range")
    gateway: str = Field("", description="Policy-based routing gateway (empty=default)")
    log: Literal["0", "1"] = Field("0", description="Log matching packets to firewall log")
    statetype: Literal["keep", "sloppy", "synproxy", "none"] = Field("keep", description="State handling mode")


class OPNsenseRuleCreate(OPNsenseRuleBase):
    uuid: Optional[str] = None
    pass


class OPNsenseRuleUpdate(OPNsenseRuleBase):
    pass


class OPNsenseRuleResponse(OPNsenseRuleBase):
    uuid: str

    model_config = {"from_attributes": True}


class OPNsenseRuleList(BaseModel):
    rules: list[OPNsenseRuleResponse]
    total: int


class OPNsenseInterface(BaseModel):
    device: str
    name: str
    mac: str
    ipaddr: str
    status: str


class OPNsenseInterfaceList(BaseModel):
    interfaces: list[OPNsenseInterface]
    total: int


class OPNsenseRuleActionResponse(BaseModel):
    result: str
    applied: bool
    uuid: Optional[str] = None
    message: Optional[str] = None


class OPNsenseRuleMoveResponse(BaseModel):
    result: str
    applied: bool
    moved_uuid: Optional[str] = None
    swapped_with_uuid: Optional[str] = None