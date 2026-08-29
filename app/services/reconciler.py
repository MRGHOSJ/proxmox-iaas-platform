"""
State Reconciliation Service

Handles state reconciliation between Database and actual infrastructure.
Now uses the Provider Abstraction Layer for multi-provider support.
"""
import json
import logging
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from app.models.vm import VM
from app.providers import get_container_provider, ProviderException
from app.providers.base import ContainerInfo, ProviderType

logger = logging.getLogger(__name__)


class Reconciler:
    """
    Handles state reconciliation between Database and Infrastructure using Provider Abstraction.
    Supports multiple providers (docker, vsphere, etc.).
    """

    def get_container_status(self, provider: str = "docker") -> Dict[str, str]:
        """
        Returns a dictionary of { vm_name: simplified_status }.
        Uses the provider abstraction layer.
        """
        try:
            container_provider = get_container_provider(provider)
            containers = container_provider.list_containers(
                label_filter={"managed_by": "proxmox-automation-cloud"}
            )
            
            status_map = {}
            for container in containers:
                status_map[container.name] = container.status
                logger.debug(f"Found Container: {container.name} -> {container.status}")
            
            logger.info(f"Found {len(status_map)} containers in {provider}.")
            return status_map
            
        except ProviderException as e:
            logger.error(f"Failed to get container status from {provider}: {e}")
            return {}

    def get_all_providers(self) -> List[str]:
        """
        Get list of all configured providers.
        Currently returns supported providers based on available implementations.
        """
        providers = ["docker"]
        try:
            vsphere_provider = get_container_provider("vsphere")
            providers.append("vsphere")
        except ProviderException:
            pass
        return providers

    def audit(self, db: Session, provider: str = None):
        """
        Compares DB state vs actual infrastructure state.
        If provider is None, audits all providers.
        """
        if provider:
            return self._audit_single_provider(db, provider)
        
        combined_report = {
            "orphans": [],
            "ghosts": [],
            "drift": [],
            "synced": [],
            "provider_reports": {}
        }
        
        for prov in self.get_all_providers():
            provider_report = self._audit_single_provider(db, prov)
            combined_report["provider_reports"][prov] = provider_report
            combined_report["orphans"].extend(provider_report.get("orphans", []))
            combined_report["ghosts"].extend(provider_report.get("ghosts", []))
            combined_report["drift"].extend(provider_report.get("drift", []))
            combined_report["synced"].extend(provider_report.get("synced", []))
        
        return combined_report

    def _audit_single_provider(self, db: Session, provider: str):
        """
        Audit a single provider.
        """
        container_status = self.get_container_status(provider)
        vms = db.query(VM).filter(VM.provider == provider).all()
        
        report = {
            "orphans": [],
            "ghosts": [],
            "drift": [],
            "synced": []
        }

        db_vm_names = {vm.name: vm for vm in vms}

        for name, status in container_status.items():
            if name not in db_vm_names:
                report["orphans"].append({"name": name, "status": status, "provider": provider})

        for vm in vms:
            if vm.name in container_status:
                real_status = container_status[vm.name]
                
                db_is_running = vm.status == "running"
                real_is_running = real_status == "running"
                
                if db_is_running != real_is_running:
                    report["drift"].append({
                        "vm_id": vm.id, 
                        "name": vm.name, 
                        "db_status": vm.status, 
                        "real_status": real_status,
                        "provider": provider
                    })
                else:
                    report["synced"].append(vm.name)
            else:
                logger.warning(f"Ghost detected: {vm.name} not found in infrastructure.")
                report["ghosts"].append({
                    "vm_id": vm.id, 
                    "name": vm.name, 
                    "db_status": vm.status,
                    "provider": provider
                })
        
        return report

    def reconcile_all(self, db: Session, provider: str = None):
        """
        Performs a full reconciliation across all providers or specified provider:
        1. Deletes Orphans from infrastructure.
        2. Deletes Ghosts from DB.
        3. Updates DB status for Drift.
        """
        if provider:
            return self._reconcile_single_provider(db, provider)
        
        combined_results = {
            "orphan_purged": [],
            "ghost_purged": [],
            "drift_corrected": [],
            "provider_results": {}
        }
        
        for prov in self.get_all_providers():
            provider_result = self._reconcile_single_provider(db, prov)
            combined_results["provider_results"][prov] = provider_result
            combined_results["orphan_purged"].extend(provider_result.get("orphan_purged", []))
            combined_results["ghost_purged"].extend(provider_result.get("ghost_purged", []))
            combined_results["drift_corrected"].extend(provider_result.get("drift_corrected", []))
        
        return combined_results

    def _reconcile_single_provider(self, db: Session, provider: str):
        """
        Reconcile a single provider.
        """
        report = self.audit(db, provider)
        
        results = {
            "orphan_purged": [],
            "ghost_purged": [],
            "drift_corrected": []
        }

        try:
            container_provider = get_container_provider(provider)
        except ProviderException as e:
            logger.error(f"Failed to get provider {provider} for reconciliation: {e}")
            return results

        # --- FIX ORPHANS (Infrastructure Cleanup) ---
        for orphan in report.get("orphans", []):
            name = orphan["name"]
            logger.info(f"Reconcile: Purging orphan container {name}...")
            try:
                container_provider.remove(name, force=True)
                results["orphan_purged"].append(name)
            except ProviderException as e:
                logger.error(f"Failed to purge orphan {name}: {e}")

        # --- FIX GHOSTS (DB Cleanup) ---
        for ghost in report.get("ghosts", []):
            vm_id = ghost["vm_id"]
            vm = db.query(VM).filter(VM.id == vm_id).first()
            if vm:
                logger.info(f"Reconcile: Purging ghost VM record {vm.name} (ID: {vm_id})...")
                
                # First, release all IP reservations for this VM (clear vm_id to avoid FK conflict)
                from app.models.ip_reservation import IPReservation
                reservations = db.query(IPReservation).filter(IPReservation.vm_id == vm_id).all()
                for res in reservations:
                    res.status = "released"
                    res.vm_id = None  # Clear FK reference before deleting VM
                    logger.debug(f"Released IP reservation {res.ip_address} for ghost VM {vm_id}")
                
                # Then release the IP if it was assigned
                if vm.ip_address and vm.network_id:
                    try:
                        from app.services.ipam import release_ip_reservation
                        release_ip_reservation(db, vm.network_id, vm.ip_address)
                        logger.info(f"Released IP {vm.ip_address} from ghost VM {vm.name}")
                    except Exception as ip_error:
                        logger.warning(f"Could not release IP for ghost VM {vm.name}: {ip_error}")
                
                db.delete(vm)
                results["ghost_purged"].append(ghost["name"])
        
        db.commit()

        # --- FIX DRIFT (Status Update) ---
        for item in report.get("drift", []):
            vm_id = item["vm_id"]
            real_status = item["real_status"]
            
            vm = db.query(VM).filter(VM.id == vm_id).first()
            if vm:
                logger.info(f"Reconcile: Correcting drift for {vm.name}. Setting status to {real_status}.")
                vm.status = real_status
                results["drift_corrected"].append({
                    "name": vm.name, 
                    "old_status": item["db_status"], 
                    "new_status": real_status
                })
        
        db.commit()
        return results

    def fix_ghost_vm(self, db: Session, vm_id: int):
        vm = db.query(VM).filter(VM.id == vm_id).first()
        if not vm:
            return False, "VM not found"

        logger.warning(f"Fixing Ghost VM {vm.name}...")
        vm.status = "pending"
        db.commit()

        from app.workers.task_scheduler import deploy_vm_task

        vm_data_dict = {
            "name": vm.name,
            "provider": vm.provider,
            "cpu": vm.cpu,
            "ram": vm.ram,
            "disk_size": vm.disk_size,
            "network_id": vm.network_id
        }
        
        deploy_vm_task.delay(vm_id, vm_data_dict, {})
        return True, "Re-provision task dispatched successfully"
