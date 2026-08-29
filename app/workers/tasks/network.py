"""
Network tasks.

Contains tasks for network deployment and destruction.
"""
import logging
from typing import Optional

from app.workers.celery_app import celery_app
from app.core.database import SessionLocal
from app.workers.tasks.helpers import MAX_RETRIES, RETRY_DELAY, get_db
from app.services.terraform import (
    get_network_terraform_context,
    render_terraform_code,
    run_terraform_job,
    destroy_terraform_job
)
from app.core.websocket import publish_status_update, publish_log_update

logger = logging.getLogger(__name__)


@celery_app.task(name="tasks.deploy_network", bind=True, max_retries=0)
def deploy_network_task(self, network_id: int):
    """
    DEPRECATED: Docker network provisioning is no longer supported.
    Use TenantNetwork for Proxmox tenant networks instead.
    """
    return {"status": "error", "error": "Docker network provisioning is deprecated"}


@celery_app.task(name="tasks.destroy_network_task", bind=True, max_retries=2, default_retry_delay=10)
def destroy_network_task(self, network_id: int, network_name: str, cidr: Optional[str] = None, provider: str = "docker"):
    """
    Destroys the actual network infrastructure (Docker Bridge).
    """
    logger.info(f"Starting destruction of network {network_id} ({network_name})")
    publish_log_update(network_id, f"Starting destruction of network {network_name}...")
    
    db = SessionLocal()
    network = None
    
    try:
        from app.models.network import Network
        network = db.query(Network).filter(Network.id == network_id).first()
        
        if network:
            cidr = network.cidr or cidr
            provider = network.provider or provider
        
        if not cidr:
            raise ValueError(f"Cannot destroy network {network_id}: CIDR not found")
        
        template_name, variables = get_network_terraform_context(
            network_name=network_name,
            cidr=cidr,
            network_id=network_id,
            provider=provider
        )
        tf_code = render_terraform_code(template_name, variables)
        
        result = destroy_terraform_job(
            identifier=network_id,
            name=network_name,
            tf_code=tf_code,
            variables=variables,
            workspace_prefix="network"
        )
        
        if result['status'] == 'destroyed':
            if network:
                old_status = network.status
                db.delete(network)
                db.commit()
                
                try:
                    publish_status_update("network", network.id, old_status, "destroyed")
                except Exception as ws_err:
                    logger.warning(f"Failed to broadcast status change: {ws_err}")
                    
            logger.info(f"Network {network_id} destroyed and removed from DB.")
            return {"status": "success", "network_id": network_id}
        else:
            if network:
                old_status = network.status
                network.status = "error"
                db.commit()
                
                try:
                    publish_status_update("network", network.id, old_status, "error")
                except Exception as ws_err:
                    logger.warning(f"Failed to broadcast status change: {ws_err}")
                    
            return {"status": "error", "error": "Destroy failed"}
                
    except Exception as e:
        error_msg = str(e)
        return {"status": "error", "error": error_msg, "network_id": network_id}
    
    finally:
        db.close()