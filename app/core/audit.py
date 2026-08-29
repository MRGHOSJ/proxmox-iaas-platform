"""
Audit logging utility for tracking sensitive operations.
"""
import logging
from typing import Optional
from sqlalchemy.orm import Session
from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


def log_audit_event(
    db: Session,
    action: str,
    target_type: str,
    actor_id: Optional[int],
    actor_username: str,
    target_id: Optional[int] = None,
    target_name: Optional[str] = None,
    old_value: Optional[str] = None,
    new_value: Optional[str] = None,
    details: Optional[str] = None,
    request_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    tenant_id: Optional[int] = None,
    impersonated_by: Optional[str] = None
) -> AuditLog:
    """
    Create an immutable audit log entry for sensitive operations.
    
    Args:
        db: Database session
        action: Action performed (e.g., "role_change", "user_delete", "status_override")
        target_type: Type of target (e.g., "user", "vm", "network")
        actor_id: ID of user performing the action (None for system actions)
        actor_username: Username of user performing the action
        target_id: ID of target resource
        target_name: Name of target resource
        old_value: Previous value (for changes)
        new_value: New value (for changes)
        details: Additional details
        request_id: Request ID for tracing
        ip_address: Client IP address
        tenant_id: Tenant ID for tenant-scoped audit logs
        impersonated_by: Username of super admin when action is taken during impersonation
    """
    try:
        # Safeguard: If actor_id is None, use "system" as actor_username
        if actor_id is None and (not actor_username or actor_username == "unknown"):
            actor_username = "system"
        
        # Safeguard: Ensure actor_username is never empty
        if not actor_username:
            actor_username = "unknown"
        
        # Append impersonation context to details if present
        final_details = details
        if impersonated_by:
            prefix = f"impersonated_by={impersonated_by}"
            final_details = f"{prefix}, {details}" if details else prefix

        audit_log = AuditLog(
            actor_id=actor_id,
            actor_username=actor_username,
            action=action,
            target_type=target_type,
            target_id=target_id,
            target_name=target_name,
            old_value=old_value,
            new_value=new_value,
            details=final_details,
            request_id=request_id,
            ip_address=ip_address,
            tenant_id=tenant_id
        )
        db.add(audit_log)
        db.commit()
        logger.info(f"Audit log created: {action} on {target_type}:{target_id} by {actor_username}")
        return audit_log
    except Exception as e:
        logger.error(f"Failed to create audit log: {e}")
        db.rollback()
        return None


AUDIT_ACTIONS = {
    "ROLE_CHANGE": "role_change",
    "USER_CREATE": "user_create",
    "USER_DELETE": "user_delete",
    "USER_STATUS_CHANGE": "user_status_change",
    "USER_PROFILE_UPDATE": "user_profile_update",
    "PASSWORD_CHANGE": "password_change",
    "VM_CREATE": "vm_create",
    "VM_START": "vm_start",
    "VM_STOP": "vm_stop",
    "VM_RESTART": "vm_restart",
    "VM_DELETE": "vm_delete",
    "VM_STATUS_OVERRIDE": "vm_status_override",
    "VM_SNAPSHOT_CREATE": "vm_snapshot_create",
    "VM_SNAPSHOT_RESTORE": "vm_snapshot_restore",
    "VM_SNAPSHOT_DELETE": "vm_snapshot_delete",
    "VM_DISK_RESIZE": "disk.resize",
    "NETWORK_CREATE": "network_create",
    "NETWORK_DELETE": "network_delete",
    "LOGIN": "login",
    "LOGOUT": "logout",
    "LOGIN_FAILED": "login_failed",
    "ADMIN_ACTION": "admin_action",
    "RECONCILE": "reconcile",
    "FIREWALL_RULE_CREATE": "firewall_rule_create",
    "FIREWALL_RULE_UPDATE": "firewall_rule_update",
    "FIREWALL_RULE_DELETE": "firewall_rule_delete",
    "FIREWALL_RULE_APPLY": "firewall_rule_apply",
    "OPNSENSE_FIREWALL_SYNC": "opnsense_firewall_sync",
    "FIREWALL_PROVIDER_SWITCH": "firewall_provider_switch",
    "INVITE_CREATE": "invite_create",
    "INVITE_ACCEPT": "invite_accept",
    "INVITE_REVOKE": "invite_revoke",
    "TENANT_CREATE": "tenant_create",
    "TENANT_UPDATE": "tenant_update",
    "TENANT_DELETE": "tenant_delete",
    "TENANT_APPROVED": "tenant_approved",
    "TENANT_PROVISIONED": "tenant_provisioned",
    "WAN_IP_ASSIGNED": "wan_ip_assigned",
    "WAN_IP_CHANGED": "wan_ip_changed",
    "VM_UPDATE": "vm_update",
    "IMPERSONATION_START": "impersonation_start",
    "IMPERSONATION_END": "impersonation_end",
    "IMAGE_REGISTER": "image_register",
    "IMAGE_UPDATE": "image_update",
    "IMAGE_DELETE": "image_delete",
    "IMAGE_ASSIGN": "image_assign",
    "IMAGE_UNASSIGN": "image_unassign",
    "IMAGE_BUILD_START": "image_build_start",
    "IMAGE_BUILD_COMPLETE": "image_build_complete",
    "IMAGE_BUILD_CANCEL": "image_build_cancel",
    "IMAGE_BUILD_ERROR": "image_build_error",
    "WIREGUARD_TUNNEL_CREATE": "wireguard_tunnel_create",
    "WIREGUARD_TUNNEL_UPDATE": "wireguard_tunnel_update",
    "WIREGUARD_TUNNEL_DELETE": "wireguard_tunnel_delete",
    "WIREGUARD_PEER_CREATE": "wireguard_peer_create",
    "WIREGUARD_PEER_UPDATE": "wireguard_peer_update",
    "WIREGUARD_PEER_DELETE": "wireguard_peer_delete",
    "WIREGUARD_RECONFIGURE": "wireguard_reconfigure",
    "WIREGUARD_PROVISION_ERROR": "wireguard_provision_error",
}
