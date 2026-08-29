"""
Firewall Manager Celery Tasks.

Contains tasks for syncing firewall rules between provider and DB,
and applying rule changes asynchronously with retry support.
"""
import logging
import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.audit import log_audit_event, AUDIT_ACTIONS
from app.core.websocket import publish_status_update
from app.providers import get_firewall_provider, OPNsenseFirewallProvider
from app.models.opnsense_firewall_rule import OPNsenseFirewallRule
from app.models.tenant import Tenant
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _db():
    db = SessionLocal()
    try:
        return db
    except Exception:
        db.close()
        raise


def _parse_bool(val) -> str:
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, str):
        return "1" if val.lower() in ("1", "true", "yes", "on") else "0"
    return "0"


@celery_app.task(name="tasks.sync_firewall_rules", bind=True, max_retries=3, default_retry_delay=30)
def sync_firewall_rules_task(self, tenant_id: int, provider_type: str = "opnsense"):
    """
    Sync OPNsense firewall rules to DB. Detects drift (rules created/updated/deleted elsewhere).
    Creates one audit log entry per changed rule.
    """
    logger.info(f"Task: syncing {provider_type} firewall rules for tenant {tenant_id}")
    db = SessionLocal()

    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            return {"status": "error", "error": f"Tenant {tenant_id} not found"}

        if provider_type != "opnsense":
            logger.warning(f"Sync only implemented for opnsense, got {provider_type}")
            return {"status": "error", "error": f"Provider {provider_type} sync not implemented"}

        provider = OPNsenseFirewallProvider(tenant)
        opnsense_rules = provider.list_rules()

        db_rules = {
            r.uuid: r for r in db.query(OPNsenseFirewallRule).filter(
                OPNsenseFirewallRule.tenant_id == tenant_id
            ).all()
        }
        opnsense_uuids = {r.get("uuid") for r in opnsense_rules}

        added = 0
        updated = 0
        deleted = 0

        now = datetime.now(timezone.utc)

        for rule_dict in opnsense_rules:
            uuid = rule_dict.get("uuid", "")
            existing = db_rules.get(uuid)

            rule_data = {
                "sequence": int(rule_dict.get("sequence", "100")),
                "enabled": _parse_bool(rule_dict.get("enabled", "1")),
                "description": rule_dict.get("description", ""),
                "interface": rule_dict.get("interface", "lan"),
                "interfacenot": _parse_bool(rule_dict.get("interfacenot", "0")),
                "quick": _parse_bool(rule_dict.get("quick", "1")),
                "action": rule_dict.get("action", "pass"),
                "direction": rule_dict.get("direction", "in"),
                "ipprotocol": rule_dict.get("ipprotocol", "inet"),
                "protocol": rule_dict.get("protocol", "tcp"),
                "source_not": _parse_bool(rule_dict.get("source_not", "0")),
                "source_net": rule_dict.get("source_net", "any"),
                "source_port": rule_dict.get("source_port", "any"),
                "destination_not": _parse_bool(rule_dict.get("destination_not", "0")),
                "destination_net": rule_dict.get("destination_net", "any"),
                "destination_port": rule_dict.get("destination_port", "any"),
                "gateway": rule_dict.get("gateway", ""),
                "log": _parse_bool(rule_dict.get("log", "0")),
                "statetype": rule_dict.get("statetype", "keep"),
            }

            if existing:
                if existing.apply_status in ("pending", "pending_delete", "failed"):
                    existing.synced_at = now
                else:
                    changed = any(
                        getattr(existing, field, None) != rule_data.get(field)
                        for field in rule_data
                    )
                    if changed:
                        old_desc = existing.description
                        for field, value in rule_data.items():
                            setattr(existing, field, value)
                        existing.synced_at = now
                        existing.apply_status = "synced"
                        existing.apply_error = None

                        log_audit_event(
                            db=db,
                            action=AUDIT_ACTIONS["FIREWALL_RULE_UPDATE"],
                            target_type="opnsense_firewall_rule",
                            actor_id=None,
                            actor_username="system",
                            target_id=existing.id,
                            target_name=f"OPNsense Rule: {existing.description}",
                            old_value=f"desc={old_desc}",
                            new_value=f"Detected change from OPNsense: {', '.join(rule_data.keys())}",
                            details=f"Sync detected changes in rule uuid={uuid}",
                            tenant_id=tenant_id,
                        )
                        updated += 1
                    else:
                        existing.synced_at = now
            else:
                new_rule = OPNsenseFirewallRule(
                    tenant_id=tenant_id,
                    uuid=uuid,
                    synced_at=now,
                    apply_status="synced",
                    **rule_data,
                )
                db.add(new_rule)
                db.flush()

                log_audit_event(
                    db=db,
                    action=AUDIT_ACTIONS["FIREWALL_RULE_CREATE"],
                    target_type="opnsense_firewall_rule",
                    actor_id=None,
                    actor_username="system",
                    target_id=new_rule.id,
                    target_name=f"OPNsense Rule: {rule_data['description']}",
                    new_value=f"Sync detected new rule from OPNsense: {rule_data['description']}",
                    details=f"Sync added rule uuid={uuid} from OPNsense",
                    tenant_id=tenant_id,
                )
                added += 1

        for uuid, db_rule in db_rules.items():
            if uuid not in opnsense_uuids:
                if db_rule.apply_status != "synced":
                    continue
                rule_desc = db_rule.description
                rule_id = db_rule.id
                db.delete(db_rule)

                log_audit_event(
                    db=db,
                    action=AUDIT_ACTIONS["FIREWALL_RULE_DELETE"],
                    target_type="opnsense_firewall_rule",
                    actor_id=None,
                    actor_username="system",
                    target_id=rule_id,
                    target_name=f"OPNsense Rule: {rule_desc}",
                    old_value=f"uuid={uuid}, desc={rule_desc}",
                    new_value="Sync detected rule deleted from OPNsense",
                    details=f"Sync removed rule uuid={uuid} (deleted from OPNsense)",
                    tenant_id=tenant_id,
                )
                deleted += 1

        db.commit()
        logger.info(f"Tenant {tenant_id}: sync complete — added={added}, updated={updated}, deleted={deleted}")

        try:
            provider = OPNsenseFirewallProvider(tenant)
            items = provider.get_interface_list()
            tenant.opnsense_interface_list = json.dumps(items)
            tenant.opnsense_interface_cached_at = datetime.now(timezone.utc)
            db.commit()
            logger.info(f"Tenant {tenant_id}: interface cache refreshed ({len(items)} interfaces)")
        except Exception as iface_err:
            logger.warning(f"Tenant {tenant_id}: failed to refresh interface cache: {iface_err}")

        return {"status": "success", "added": added, "updated": updated, "deleted": deleted}

    except Exception as e:
        logger.error(f"Tenant {tenant_id}: sync failed: {e}")
        db.rollback()
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=30 * (2 ** self.request.retries))
        return {"status": "error", "error": str(e)}
    finally:
        db.close()


@celery_app.task(name="tasks.apply_firewall_rule", bind=True, max_retries=3, default_retry_delay=30)
def apply_firewall_rule_task(
    self,
    tenant_id: int,
    provider_type: str,
    operation: str,
    rule_id: int,
    rule_uuid: str = None,
    rule_data: dict = None,
):
    """
    Apply a firewall rule change to OPNsense (add/set/delete). Does NOT call apply_rules().
    A separate task (triggered by user or apply_all_pending_rules) handles the reload.
    operation: "create" | "update" | "delete"
    Auto-retries on failure, but apply_rules is NOT called here.
    """
    logger.info(f"Task: apply_firewall_rule tenant={tenant_id} op={operation} rule_id={rule_id}")
    db = SessionLocal()

    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            return {"status": "error", "error": f"Tenant {tenant_id} not found"}

        if provider_type != "opnsense":
            return {"status": "error", "error": f"Provider {provider_type} not implemented"}

        provider = OPNsenseFirewallProvider(tenant)

        db_rule = db.query(OPNsenseFirewallRule).filter(OPNsenseFirewallRule.id == rule_id).first()
        if not db_rule and operation != "create":
            logger.warning(f"Rule {rule_id} not found in DB, skipping apply")
            return {"status": "error", "error": f"Rule {rule_id} not found in DB"}

        error_msg = None

        if operation == "create":
            payload = rule_data or {}
            if "sequence" not in payload:
                max_seq = db.query(OPNsenseFirewallRule).filter(
                    OPNsenseFirewallRule.tenant_id == tenant_id
                ).count()
                payload["sequence"] = str((max_seq + 1) * 100)

            new_uuid, _ = provider.add_rule(payload)
            db_rule.uuid = new_uuid
            db_rule.apply_status = "synced"
            db_rule.synced_at = datetime.now(timezone.utc)
            db.commit()

        elif operation == "update":
            if db_rule:
                payload = db_rule.to_opnsense_payload()
                provider.set_rule(db_rule.uuid, payload)
                db_rule.apply_status = "synced"
                db_rule.synced_at = datetime.now(timezone.utc)

        elif operation == "delete":
            if db_rule:
                provider.del_rule(db_rule.uuid)

        db.commit()

        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["FIREWALL_RULE_APPLY"],
            target_type="opnsense_firewall_rule",
            actor_id=None,
            actor_username="system",
            target_id=rule_id,
            target_name=f"OPNsense Rule: {db_rule.description if db_rule else 'unknown'}",
            new_value=f"Rule {operation} sent to OPNsense (pending apply)",
            details=f"Celery task completed {operation} for rule {rule_id}",
            tenant_id=tenant_id,
        )
        return {"status": "success", "operation": operation, "rule_id": rule_id}

    except Exception as e:
        error_str = str(e)
        logger.error(f"apply_firewall_rule_task failed: {error_str}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=30 * (2 ** self.request.retries))

        if db_rule:
            db_rule.apply_status = "failed"
            db_rule.apply_error = error_str[:500]
        db.commit()

        publish_status_update(
            "firewall", tenant_id, "applying", "failed",
            additional_data={"type": "firewall_apply_failed", "rule_id": rule_id, "error": error_str[:500]}
        )

        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["FIREWALL_RULE_APPLY"],
            target_type="opnsense_firewall_rule",
            actor_id=None,
            actor_username="system",
            target_id=rule_id,
            target_name=f"OPNsense Rule: {db_rule.description if db_rule else 'unknown'}",
            new_value=f"Apply failed after {self.max_retries} retries: {error_str}",
            details=f"Celery task apply failed for rule {rule_id}",
            tenant_id=tenant_id,
        )
        return {"status": "error", "error": error_str, "retries": self.request.retries}
    finally:
        db.close()


@celery_app.task(name="tasks.apply_opnsense_firewall", bind=True, max_retries=5, default_retry_delay=30)
def apply_opnsense_firewall_task(self, tenant_id: int):
    """
    Trigger OPNsense firewall reload. Called after rule mutations or by apply_all_pending_rules.
    Handles the blocking apply_rules() call with exponential backoff retry.
    """
    logger.info(f"Task: apply_opnsense_firewall for tenant {tenant_id}")
    db = SessionLocal()

    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            return {"status": "error", "error": f"Tenant {tenant_id} not found"}

        provider = OPNsenseFirewallProvider(tenant)
        provider.apply_rules()

        db.query(OPNsenseFirewallRule).filter(
            OPNsenseFirewallRule.tenant_id == tenant_id,
            OPNsenseFirewallRule.apply_status == "pending",
        ).update({"apply_status": "synced", "synced_at": datetime.now(timezone.utc)})
        db.commit()

        logger.info(f"Tenant {tenant_id}: OPNsense firewall rules reloaded successfully")

        return {"status": "success", "tenant_id": tenant_id}

    except Exception as e:
        error_str = str(e)
        logger.error(f"apply_opnsense_firewall failed for tenant {tenant_id}: {error_str}")
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=30 * (2 ** self.request.retries))

        return {"status": "error", "error": error_str, "retries": self.request.retries}
    finally:
        db.close()


@celery_app.task(name="tasks.apply_all_pending_rules", bind=True, max_retries=3, default_retry_delay=60)
def apply_all_pending_rules_task(self, tenant_id: int, provider_type: str = "opnsense"):
    """
    Apply all rules with apply_status='pending', 'pending_delete', or 'failed'.
    Uses set_rule for property updates and move_rule_before for reordering.
    Sends WebSocket notification on completion so the frontend clears the
    "Applying..." spinner.
    """
    logger.info(f"Task: apply_all_pending_rules for tenant {tenant_id}")
    db = SessionLocal()

    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            return {"status": "error", "error": f"Tenant {tenant_id} not found"}

        if provider_type != "opnsense":
            return {"status": "error", "error": f"Provider {provider_type} not implemented"}

        pending_rules = db.query(OPNsenseFirewallRule).filter(
            OPNsenseFirewallRule.tenant_id == tenant_id,
            OPNsenseFirewallRule.apply_status.in_(["pending", "pending_delete"]),
        ).all()

        if not pending_rules:
            publish_status_update(
                "firewall", tenant_id, "applying", "applied",
                additional_data={"type": "firewall_apply", "applied": 0},
            )
            return {"status": "success", "applied": 0, "message": "No pending rules to apply"}

        provider = OPNsenseFirewallProvider(tenant)
        applied_count = 0
        failed_count = 0
        errors = []

        rules_to_delete = [r for r in pending_rules if r.apply_status == "pending_delete"]
        rules_to_update = [r for r in pending_rules if r.apply_status == "pending" and not r.uuid.startswith("pending")]
        rules_to_create = [r for r in pending_rules if r.apply_status == "pending" and r.uuid.startswith("pending")]

        for rule in rules_to_delete:
            try:
                if not rule.uuid.startswith("pending"):
                    provider.del_rule(rule.uuid)
                applied_count += 1
            except Exception as e:
                failed_count += 1
                errors.append(f"Delete rule {rule.id}: {str(e)}")
                rule.apply_status = "failed"
                rule.apply_error = str(e)[:500]

        for rule in rules_to_update:
            try:
                payload = rule.to_opnsense_payload()
                provider.set_rule(rule.uuid, payload)
                rule.apply_status = "synced"
                rule.apply_error = None
                rule.synced_at = datetime.now(timezone.utc)
                applied_count += 1
            except Exception as e:
                failed_count += 1
                errors.append(f"Update rule {rule.id}: {str(e)}")
                rule.apply_status = "failed"
                rule.apply_error = str(e)[:500]

        for rule in rules_to_create:
            try:
                payload = rule.to_opnsense_payload()
                new_uuid, _ = provider.add_rule(payload)
                rule.uuid = new_uuid
                rule.apply_status = "synced"
                rule.apply_error = None
                rule.synced_at = datetime.now(timezone.utc)
                applied_count += 1
            except Exception as e:
                failed_count += 1
                errors.append(f"Create rule {rule.id}: {str(e)}")
                rule.apply_status = "failed"
                rule.apply_error = str(e)[:500]

        all_rules = db.query(OPNsenseFirewallRule).filter(
            OPNsenseFirewallRule.tenant_id == tenant_id
        ).order_by(OPNsenseFirewallRule.sequence).all()

        try:
            opnsense_rules = provider.list_rules()
            opnsense_uuid_order = [r.get("uuid") for r in opnsense_rules if r.get("uuid")]
        except Exception as e:
            logger.warning(f"Failed to fetch OPNsense rules for reorder: {e}")
            opnsense_uuid_order = []

        if len(all_rules) > 1 and opnsense_uuid_order:
            desired_order = [r.uuid for r in all_rules]
            for i in range(len(desired_order) - 2, -1, -1):
                rule_uuid = desired_order[i]
                next_uuid = desired_order[i + 1]
                if rule_uuid in opnsense_uuid_order and next_uuid in opnsense_uuid_order:
                    try:
                        provider.move_rule_before(rule_uuid, next_uuid)
                    except Exception as e:
                        logger.warning(f"Failed to move rule {rule_uuid} before {next_uuid}: {e}")

        if applied_count > 0:
            try:
                provider.apply_rules()
            except Exception as apply_err:
                logger.error(f"OPNsense apply_rules failed: {apply_err}")
                for rule in rules_to_update + rules_to_create:
                    if rule.apply_status == "synced":
                        rule.apply_status = "failed"
                        rule.apply_error = str(apply_err)[:500]

        for rule in rules_to_delete:
            if rule.apply_status != "failed":
                db.delete(rule)

        db.commit()

        publish_status_update(
            "firewall", tenant_id, "applying", "applied",
            additional_data={"type": "firewall_apply", "applied": applied_count, "failed": failed_count},
        )

        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["FIREWALL_RULE_APPLY"],
            target_type="opnsense_firewall_rule",
            actor_id=None,
            actor_username="system",
            target_id=None,
            target_name=f"Tenant {tenant_id} OPNsense rules",
            new_value=f"Applied {applied_count} rules, {failed_count} failed",
            details=f"Bulk apply task: {applied_count} applied, {failed_count} failed",
            tenant_id=tenant_id,
        )

        return {
            "status": "success",
            "applied": applied_count,
            "failed": failed_count,
            "errors": errors,
        }

    except Exception as e:
        logger.error(f"apply_all_pending_rules failed: {e}")
        db.rollback()
        publish_status_update(
            "firewall", tenant_id, "applying", "failed",
            additional_data={"type": "firewall_apply_failed", "error": str(e)[:500]},
        )
        if self.request.retries < self.max_retries:
            raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))
        return {"status": "error", "error": str(e)}
    finally:
        db.close()


@celery_app.task(name="tasks.reconcile_firewall_rules", bind=True, max_retries=1)
def reconcile_firewall_rules_task(self, tenant_id: int, provider_type: str = "opnsense"):
    """
    Full reconcile: pull all from provider, replace DB, log every change.
    Used for manual admin action or first-time sync.
    """
    logger.info(f"Task: reconcile_firewall_rules for tenant {tenant_id}")
    db = SessionLocal()

    try:
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if not tenant:
            return {"status": "error", "error": f"Tenant {tenant_id} not found"}

        if provider_type != "opnsense":
            return {"status": "error", "error": f"Provider {provider_type} not implemented"}

        provider = OPNsenseFirewallProvider(tenant)
        opnsense_rules = provider.list_rules()

        db.query(OPNsenseFirewallRule).filter(
            OPNsenseFirewallRule.tenant_id == tenant_id
        ).delete()
        db.commit()

        now = datetime.now(timezone.utc)
        added = 0

        sorted_rules = sorted(opnsense_rules, key=lambda r: int(r.get("sequence", "0")))

        for i, rule_dict in enumerate(sorted_rules):
            uuid = rule_dict.get("uuid", "")
            rule_data = {
                "sequence": (i + 1) * 100,
                "enabled": _parse_bool(rule_dict.get("enabled", "1")),
                "description": rule_dict.get("description", ""),
                "interface": rule_dict.get("interface", "lan"),
                "interfacenot": _parse_bool(rule_dict.get("interfacenot", "0")),
                "quick": _parse_bool(rule_dict.get("quick", "1")),
                "action": rule_dict.get("action", "pass"),
                "direction": rule_dict.get("direction", "in"),
                "ipprotocol": rule_dict.get("ipprotocol", "inet"),
                "protocol": rule_dict.get("protocol", "tcp"),
                "source_not": _parse_bool(rule_dict.get("source_not", "0")),
                "source_net": rule_dict.get("source_net", "any"),
                "source_port": rule_dict.get("source_port", "any"),
                "destination_not": _parse_bool(rule_dict.get("destination_not", "0")),
                "destination_net": rule_dict.get("destination_net", "any"),
                "destination_port": rule_dict.get("destination_port", "any"),
                "gateway": rule_dict.get("gateway", ""),
                "log": _parse_bool(rule_dict.get("log", "0")),
                "statetype": rule_dict.get("statetype", "keep"),
            }

            new_rule = OPNsenseFirewallRule(
                tenant_id=tenant_id,
                uuid=uuid,
                synced_at=now,
                apply_status="synced",
                **rule_data,
            )
            db.add(new_rule)
            added += 1

        db.commit()

        log_audit_event(
            db=db,
            action=AUDIT_ACTIONS["RECONCILE"],
            target_type="opnsense_firewall_rule",
            actor_id=None,
            actor_username="system",
            target_name=f"Tenant {tenant_id} OPNsense Firewall",
            new_value=f"Reconcile: replaced {added} rules from OPNsense",
            details=f"Full reconcile for tenant {tenant_id}: {added} rules loaded",
            tenant_id=tenant_id,
        )

        logger.info(f"Tenant {tenant_id}: reconcile complete — {added} rules")
        return {"status": "success", "reconciled": added}

    except Exception as e:
        logger.error(f"Tenant {tenant_id}: reconcile failed: {e}")
        db.rollback()
        return {"status": "error", "error": str(e)}
    finally:
        db.close()


@celery_app.task(name="tasks.sync_all_firewall_rules", bind=True, max_retries=3, default_retry_delay=60)
def sync_all_firewall_rules_task(self):
    """
    Sync firewall rules for all active tenants. Called by Celery beat every 2 minutes.
    Dispatches sync_firewall_rules_task for each active tenant.
    """
    logger.info("Task: syncing firewall rules for all active tenants")
    db = SessionLocal()
    try:
        tenants = db.query(Tenant).filter(Tenant.is_active == True).all()
        dispatched = 0
        for tenant in tenants:
            sync_firewall_rules_task.delay(tenant.id)
            dispatched += 1
        logger.info(f"Dispatched sync_firewall_rules_task for {dispatched} tenants")
        return {"status": "success", "tenants_synced": dispatched}
    except Exception as e:
        logger.error(f"sync_all_firewall_rules failed: {e}")
        raise self.retry(exc=e)
    finally:
        db.close()