"""
Worker tasks for Proxmox CloudOrchestrator.

This is the main entry point that re-exports tasks from the modular task modules.
For backwards compatibility and simplicity, all tasks are imported here.
"""
import logging

from app.workers.celery_app import celery_app
from app.workers.tasks.tenant import provision_tenant_task, destroy_tenant_task, poll_opnsense_wan_ip_task
from app.workers.tasks.vlan import create_opnsense_vlan
from app.workers.tasks.vm import deploy_vm_task, provision_vm_task, _attempt_terraform_rollback
from app.workers.tasks.network import deploy_network_task, destroy_network_task
from app.workers.tasks.firewall_manager import (
    sync_firewall_rules_task,
    apply_firewall_rule_task,
    apply_opnsense_firewall_task,
    apply_all_pending_rules_task,
    reconcile_firewall_rules_task,
    sync_all_firewall_rules_task,
)
from app.workers.tasks.kea import configure_kea_dhcp_task
from app.workers.tasks.images import create_build_vm_task, convert_build_to_template_task
from app.workers.tasks.wireguard import (
    provision_wireguard_tunnel_task,
    destroy_wireguard_tunnel_task,
    provision_wireguard_peer_task,
    destroy_wireguard_peer_task,
)
from app.workers.tasks.helpers import (
    cleanup_expired_reservations_task,
    _cleanup_vm_on_failure,
    _validate_vm_data,
    get_db,
    get_vm_wan_ip,
    get_proxmox_client,
    sanitize_log,
    log_to_vm,
    MAX_RETRIES,
    RETRY_DELAY,
)

logger = logging.getLogger(__name__)

# Re-export tasks for backwards compatibility
__all__ = [
    # Celery Tasks
    "provision_tenant_task",
    "destroy_tenant_task",
    "poll_opnsense_wan_ip_task",
    "create_opnsense_vlan",
    "deploy_vm_task",
    "provision_vm_task",
    "deploy_network_task",
    "destroy_network_task",
    "sync_firewall_rules_task",
    "apply_firewall_rule_task",
    "apply_opnsense_firewall_task",
    "apply_all_pending_rules_task",
    "reconcile_firewall_rules_task",
    "sync_all_firewall_rules_task",
    "configure_kea_dhcp_task",
    "create_build_vm_task",
    "convert_build_to_template_task",
    "provision_wireguard_tunnel_task",
    "destroy_wireguard_tunnel_task",
    "provision_wireguard_peer_task",
    "destroy_wireguard_peer_task",
    "cleanup_expired_reservations_task",
    # Helper Functions
    "sanitize_log",
    "log_to_vm",
    "get_db",
    "get_vm_wan_ip",
    "get_proxmox_client",
    "_validate_vm_data",
    "_cleanup_vm_on_failure",
    "_attempt_terraform_rollback",
    # Constants
    "MAX_RETRIES",
    "RETRY_DELAY",
]