import logging
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Optional

from app.core.database import get_db
from app.core.dependencies import get_current_user, get_current_tenant
from app.core.iam import has_permission
from app.core.audit import log_audit_event, AUDIT_ACTIONS
from app.models.tenant import Tenant
from app.models.user import User
from app.models.opnsense_firewall_rule import OPNsenseFirewallRule
from app.providers import get_available_providers
from app.workers.tasks.firewall_manager import (
    sync_firewall_rules_task,
    apply_all_pending_rules_task,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/firewall", tags=["firewall"])


def _parse_bool(val) -> str:
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, str):
        return "1" if val.lower() in ("1", "true", "yes", "on") else "0"
    return "0"


def _build_rule_response(db_rule, interface_labels: dict | None = None) -> dict:
    raw_iface = db_rule.interface or "lan"
    label = (interface_labels or {}).get(raw_iface.lower(), raw_iface.upper())
    return {
        "id": db_rule.id,
        "tenant_id": db_rule.tenant_id,
        "uuid": db_rule.uuid,
        "sequence": str(db_rule.sequence),
        "enabled": db_rule.enabled,
        "description": db_rule.description or "",
        "interface": raw_iface,
        "interface_label": label,
        "interfacenot": db_rule.interfacenot or "0",
        "quick": db_rule.quick or "1",
        "action": db_rule.action or "pass",
        "direction": db_rule.direction or "in",
        "ipprotocol": db_rule.ipprotocol or "inet",
        "protocol": db_rule.protocol or "tcp",
        "source_not": db_rule.source_not or "0",
        "source_net": db_rule.source_net or "any",
        "source_port": db_rule.source_port or "any",
        "destination_not": db_rule.destination_not or "0",
        "destination_net": db_rule.destination_net or "any",
        "destination_port": db_rule.destination_port or "any",
        "gateway": db_rule.gateway or "",
        "log": db_rule.log or "0",
        "statetype": db_rule.statetype or "keep",
        "apply_status": db_rule.apply_status,
        "apply_error": db_rule.apply_error,
        "synced_at": db_rule.synced_at.isoformat() if db_rule.synced_at else None,
        "created_at": db_rule.created_at.isoformat() if db_rule.created_at else None,
        "updated_at": db_rule.updated_at.isoformat() if db_rule.updated_at else None,
    }


@router.get("/providers")
def list_firewall_providers(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant),
):
    """List available firewall providers for this tenant."""
    return get_available_providers(db, current_tenant)


@router.get("/providers/status")
def get_firewall_provider_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant),
):
    """Get status of all firewall providers including active provider info."""
    providers = get_available_providers(db, current_tenant)

    for p in providers:
        if p["type"] == "opnsense":
            pending = db.query(OPNsenseFirewallRule).filter(
                OPNsenseFirewallRule.tenant_id == current_tenant.id,
                OPNsenseFirewallRule.apply_status.in_(["pending", "failed"]),
            ).count()
            p["pending_rules"] = pending

            # get sync indicator from latest rule
            latest = db.query(OPNsenseFirewallRule).filter(
                OPNsenseFirewallRule.tenant_id == current_tenant.id,
                OPNsenseFirewallRule.synced_at.isnot(None),
            ).order_by(OPNsenseFirewallRule.synced_at.desc()).first()
            p["last_sync_at"] = latest.synced_at.isoformat() if latest and latest.synced_at else None

    return {"providers": providers, "tenant_id": current_tenant.id}


@router.get("/{provider_type}/rules")
def list_rules(
    provider_type: str,
    interface: Optional[str] = Query(None, description="Filter rules by OPNsense interface name"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant),
):
    """
    List firewall rules for the specified provider from DB.
    Fast response — no direct API calls to firewall.
    Optional `interface` query param filters by OPNsense interface name.
    """
    if not current_tenant.opnsense_vm_id and provider_type == "opnsense":
        raise HTTPException(status_code=400, detail="OPNsense not configured for this tenant")

    query = db.query(OPNsenseFirewallRule).filter(
        OPNsenseFirewallRule.tenant_id == current_tenant.id
    )
    if interface:
        query = query.filter(OPNsenseFirewallRule.interface == interface)

    rules = query.order_by(OPNsenseFirewallRule.sequence).all()

    iface_labels = {"wan": "WAN", "lan": "LAN"}
    from app.models.wireguard import WireGuardTunnel
    wg_tunnels = db.query(WireGuardTunnel).filter(
        WireGuardTunnel.tenant_id == current_tenant.id,
        WireGuardTunnel.status == "active",
        WireGuardTunnel.opt_interface.isnot(None),
    ).all()
    for t in wg_tunnels:
        iface_labels[t.opt_interface.lower()] = t.name
    from app.models.network import TenantNetwork
    vlans = db.query(TenantNetwork).filter(
        TenantNetwork.tenant_id == current_tenant.id,
        TenantNetwork.opnsense_interface.isnot(None),
        TenantNetwork.status == "active",
    ).all()
    for v in vlans:
        iface_labels[v.opnsense_interface.lower()] = v.name.upper()

    return {
        "rules": [_build_rule_response(r, iface_labels) for r in rules],
        "total": len(rules),
    }


@router.get("/{provider_type}/interfaces")
def list_interfaces(
    provider_type: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant),
):
    """
    List available interfaces derived from DB.
    Always returns wan + lan as base, plus any VLAN networks from tenant_networks.
    No OPNsense API calls — fast and reliable.
    """
    if provider_type != "opnsense":
        raise HTTPException(status_code=501, detail=f"Provider {provider_type} not implemented")

    interfaces = [
        {"value": "wan", "label": "WAN", "type": "interface"},
        {"value": "lan", "label": "LAN", "type": "interface"},
    ]

    from app.models.network import TenantNetwork
    vlan_networks = db.query(TenantNetwork).filter(
        TenantNetwork.tenant_id == current_tenant.id,
        TenantNetwork.is_default == False,
        TenantNetwork.status == "active",
    ).all()

    for network in vlan_networks:
        if network.opnsense_interface:
            interfaces.append({
                "value": network.opnsense_interface,
                "label": network.name.upper(),
                "type": "interface",
            })

    from app.models.wireguard import WireGuardTunnel
    wg_tunnels = db.query(WireGuardTunnel).filter(
        WireGuardTunnel.tenant_id == current_tenant.id,
        WireGuardTunnel.status == "active",
        WireGuardTunnel.opt_interface.isnot(None),
    ).all()
    for tunnel in wg_tunnels:
        interfaces.append({
            "value": tunnel.opt_interface,
            "label": tunnel.name,
            "type": "wireguard",
        })

    return {"interfaces": interfaces, "total": len(interfaces)}


@router.post("/{provider_type}/rules")
def create_rule(
    provider_type: str,
    rule_data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant),
):
    """
    Create a firewall rule. Saves to DB with apply_status="pending".
    User must click Apply to push changes to OPNsense.
    """
    if provider_type != "opnsense":
        raise HTTPException(status_code=501, detail=f"Provider {provider_type} not implemented")

    if not has_permission(current_user, current_tenant.id, "firewall:create", db):
        raise HTTPException(status_code=403, detail="Not authorized")

    if not current_tenant.opnsense_vm_id:
        raise HTTPException(status_code=400, detail="OPNsense not configured")

    # Build DB record
    sequence = rule_data.get("sequence")
    if not sequence:
        max_seq = db.query(OPNsenseFirewallRule).filter(
            OPNsenseFirewallRule.tenant_id == current_tenant.id
        ).count()
        sequence = (max_seq + 1) * 100

    new_rule = OPNsenseFirewallRule(
        tenant_id=current_tenant.id,
        uuid=f"pending-{uuid4().hex[:24]}",
        sequence=sequence,
        enabled=rule_data.get("enabled", "1"),
        description=rule_data.get("description", ""),
        interface=rule_data.get("interface", "lan"),
        interfacenot=rule_data.get("interfacenot", "0"),
        quick=rule_data.get("quick", "1"),
        action=rule_data.get("action", "pass"),
        direction=rule_data.get("direction", "in"),
        ipprotocol=rule_data.get("ipprotocol", "inet"),
        protocol=rule_data.get("protocol", "tcp"),
        source_not=rule_data.get("source_not", "0"),
        source_net=rule_data.get("source_net", "any"),
        source_port=rule_data.get("source_port", "any"),
        destination_not=rule_data.get("destination_not", "0"),
        destination_net=rule_data.get("destination_net", "any"),
        destination_port=rule_data.get("destination_port", "any"),
        gateway=rule_data.get("gateway", ""),
        log=rule_data.get("log", "0"),
        statetype=rule_data.get("statetype", "keep"),
        apply_status="pending",
    )
    db.add(new_rule)
    db.commit()
    db.refresh(new_rule)

    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["FIREWALL_RULE_CREATE"],
        target_type="opnsense_firewall_rule",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=new_rule.id,
        target_name=f"OPNsense Rule: {new_rule.description}",
        new_value=f"Created rule: {new_rule.description} on interface {new_rule.interface}",
        details=f"Rule created via API for tenant {current_tenant.id}",
        tenant_id=current_tenant.id,
    )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "result": "saved",
            "applied": False,
            "rule": _build_rule_response(new_rule),
            "message": "Rule saved. Click Apply to push changes to OPNsense.",
        },
    )


@router.put("/{provider_type}/rules/{uuid}")
def update_rule(
    provider_type: str,
    uuid: str,
    rule_data: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant),
):
    """Update an existing firewall rule. Sets apply_status="pending" until Apply is clicked."""
    if provider_type != "opnsense":
        raise HTTPException(status_code=501, detail=f"Provider {provider_type} not implemented")

    if not has_permission(current_user, current_tenant.id, "firewall:create", db):
        raise HTTPException(status_code=403, detail="Not authorized")

    db_rule = db.query(OPNsenseFirewallRule).filter(
        OPNsenseFirewallRule.tenant_id == current_tenant.id,
        OPNsenseFirewallRule.uuid == uuid,
    ).first()

    if not db_rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rule not found — it may have been deleted in another session.",
        )

    old_values = {f: getattr(db_rule, f) for f in rule_data.keys() if hasattr(db_rule, f)}

    for field, value in rule_data.items():
        if hasattr(db_rule, field):
            setattr(db_rule, field, value)

    db_rule.apply_status = "pending"
    db_rule.apply_error = None
    db.commit()
    db.refresh(db_rule)

    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["FIREWALL_RULE_UPDATE"],
        target_type="opnsense_firewall_rule",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=db_rule.id,
        target_name=f"OPNsense Rule: {db_rule.description}",
        old_value=str(old_values),
        new_value=str(rule_data),
        details=f"Rule updated via API for tenant {current_tenant.id}",
        tenant_id=current_tenant.id,
    )

    return {
        "result": "saved",
        "applied": False,
        "rule": _build_rule_response(db_rule),
        "message": "Rule updated. Click Apply to push changes to OPNsense.",
    }


@router.delete("/{provider_type}/rules/{uuid}")
def delete_rule(
    provider_type: str,
    uuid: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant),
):
    """Delete a firewall rule. Marks as pending_delete until Apply is clicked."""
    if provider_type != "opnsense":
        raise HTTPException(status_code=501, detail=f"Provider {provider_type} not implemented")

    if not has_permission(current_user, current_tenant.id, "firewall:delete", db):
        raise HTTPException(status_code=403, detail="Not authorized")

    db_rule = db.query(OPNsenseFirewallRule).filter(
        OPNsenseFirewallRule.tenant_id == current_tenant.id,
        OPNsenseFirewallRule.uuid == uuid,
    ).first()

    if not db_rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Rule not found — it may have been deleted in another session.",
        )

    rule_desc = db_rule.description
    rule_id = db_rule.id

    db_rule.apply_status = "pending_delete"
    db.commit()

    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["FIREWALL_RULE_DELETE"],
        target_type="opnsense_firewall_rule",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=rule_id,
        target_name=f"OPNsense Rule: {rule_desc}",
        old_value=f"uuid={uuid}, desc={rule_desc}",
        details=f"Rule marked for deletion via API for tenant {current_tenant.id}",
        tenant_id=current_tenant.id,
    )

    return {"result": "saved", "applied": False, "message": "Rule marked for deletion. Click Apply to push changes."}


@router.post("/{provider_type}/rules/{uuid}/move_up")
def move_rule_up(
    provider_type: str,
    uuid: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant),
):
    """Move rule up by swapping sequence. Sets apply_status="pending" until Apply is clicked."""
    if provider_type != "opnsense":
        raise HTTPException(status_code=501, detail=f"Provider {provider_type} not implemented")

    if not has_permission(current_user, current_tenant.id, "firewall:create", db):
        raise HTTPException(status_code=403, detail="Not authorized")

    rules = db.query(OPNsenseFirewallRule).filter(
        OPNsenseFirewallRule.tenant_id == current_tenant.id
    ).order_by(OPNsenseFirewallRule.sequence).all()

    rule_map = {r.uuid: r for r in rules}
    if uuid not in rule_map:
        raise HTTPException(status_code=404, detail="Rule not found")

    index = next(i for i, r in enumerate(rules) if r.uuid == uuid)
    if index == 0:
        raise HTTPException(status_code=400, detail="Rule is already at the top")

    rule_a = rules[index]
    rule_b = rules[index - 1]

    seq_a = rule_a.sequence
    seq_b = rule_b.sequence

    rule_a.sequence = seq_b
    rule_b.sequence = seq_a
    rule_a.apply_status = "pending"
    rule_b.apply_status = "pending"
    db.commit()

    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["FIREWALL_RULE_UPDATE"],
        target_type="opnsense_firewall_rule",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=rule_a.id,
        target_name=f"Rule: {rule_a.description}",
        new_value=f"Moved up (seq {seq_a} <-> {seq_b})",
        details=f"Rule order changed via API",
        tenant_id=current_tenant.id,
    )

    return {
        "result": "moved",
        "applied": False,
        "moved_uuid": uuid,
        "swapped_with_uuid": rule_b.uuid,
    }


@router.post("/{provider_type}/rules/{uuid}/move_down")
def move_rule_down(
    provider_type: str,
    uuid: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant),
):
    """Move rule down by swapping sequence. Sets apply_status="pending" until Apply is clicked."""
    if provider_type != "opnsense":
        raise HTTPException(status_code=501, detail=f"Provider {provider_type} not implemented")

    if not has_permission(current_user, current_tenant.id, "firewall:create", db):
        raise HTTPException(status_code=403, detail="Not authorized")

    rules = db.query(OPNsenseFirewallRule).filter(
        OPNsenseFirewallRule.tenant_id == current_tenant.id
    ).order_by(OPNsenseFirewallRule.sequence).all()

    rule_map = {r.uuid: r for r in rules}
    if uuid not in rule_map:
        raise HTTPException(status_code=404, detail="Rule not found")

    index = next(i for i, r in enumerate(rules) if r.uuid == uuid)
    if index == len(rules) - 1:
        raise HTTPException(status_code=400, detail="Rule is already at the bottom")

    rule_a = rules[index]
    rule_b = rules[index + 1]

    seq_a = rule_a.sequence
    seq_b = rule_b.sequence

    rule_a.sequence = seq_b
    rule_b.sequence = seq_a
    rule_a.apply_status = "pending"
    rule_b.apply_status = "pending"
    db.commit()

    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["FIREWALL_RULE_UPDATE"],
        target_type="opnsense_firewall_rule",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=rule_a.id,
        target_name=f"Rule: {rule_a.description}",
        new_value=f"Moved down (seq {seq_a} <-> {seq_b})",
        details=f"Rule order changed via API",
        tenant_id=current_tenant.id,
    )

    return {
        "result": "moved",
        "applied": False,
        "moved_uuid": uuid,
        "swapped_with_uuid": rule_b.uuid,
    }


@router.post("/{provider_type}/apply")
def apply_pending_rules(
    provider_type: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant),
):
    """Apply all pending rule changes. Triggered by Retry Apply button."""
    if provider_type != "opnsense":
        raise HTTPException(status_code=501, detail=f"Provider {provider_type} not implemented")

    if not has_permission(current_user, current_tenant.id, "firewall:create", db):
        raise HTTPException(status_code=403, detail="Not authorized")

    apply_all_pending_rules_task.delay(tenant_id=current_tenant.id, provider_type="opnsense")

    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["FIREWALL_RULE_APPLY"],
        target_type="opnsense_firewall_rule",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_name=f"Tenant {current_tenant.id} OPNsense",
        new_value="Manual apply triggered via UI",
        details=f"Apply pending rules task dispatched for tenant {current_tenant.id}",
        tenant_id=current_tenant.id,
    )

    return {"result": "applied", "message": "Apply task queued. Rules will be applied shortly."}


@router.post("/{provider_type}/sync")
def sync_rules(
    provider_type: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant),
):
    """Manual sync trigger. Pulls all rules from provider into DB."""
    if provider_type != "opnsense":
        raise HTTPException(status_code=501, detail=f"Provider {provider_type} not implemented")

    if not has_permission(current_user, current_tenant.id, "firewall:create", db):
        raise HTTPException(status_code=403, detail="Not authorized")

    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["RECONCILE"],
        target_type="opnsense_firewall_rule",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_name=f"Tenant {current_tenant.id} OPNsense",
        new_value="Manual sync triggered via UI",
        details=f"Manual sync triggered for tenant {current_tenant.id}",
        tenant_id=current_tenant.id,
    )

    sync_firewall_rules_task.delay(tenant_id=current_tenant.id, provider_type="opnsense")

    return {"result": "syncing", "message": "Sync task queued. Rules will be refreshed."}


@router.post("/{provider_type}/rules/{uuid}/toggle")
def toggle_rule(
    provider_type: str,
    uuid: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    current_tenant: Tenant = Depends(get_current_tenant),
):
    """Toggle rule enabled/disabled. Sets apply_status="pending" until Apply is clicked."""
    if provider_type != "opnsense":
        raise HTTPException(status_code=501, detail=f"Provider {provider_type} not implemented")

    if not has_permission(current_user, current_tenant.id, "firewall:create", db):
        raise HTTPException(status_code=403, detail="Not authorized")

    db_rule = db.query(OPNsenseFirewallRule).filter(
        OPNsenseFirewallRule.tenant_id == current_tenant.id,
        OPNsenseFirewallRule.uuid == uuid,
    ).first()

    if not db_rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    new_enabled = "0" if db_rule.enabled == "1" else "1"
    db_rule.enabled = new_enabled
    db_rule.apply_status = "pending"
    db.commit()
    db.refresh(db_rule)

    log_audit_event(
        db=db,
        action=AUDIT_ACTIONS["FIREWALL_RULE_UPDATE"],
        target_type="opnsense_firewall_rule",
        actor_id=current_user.id,
        actor_username=current_user.username,
        target_id=db_rule.id,
        target_name=f"OPNsense Rule: {db_rule.description}",
        new_value=f"Toggled enabled={new_enabled}",
        details=f"Rule enabled toggle via API",
        tenant_id=current_tenant.id,
    )

    return {
        "result": "saved",
        "applied": False,
        "rule": _build_rule_response(db_rule),
    }