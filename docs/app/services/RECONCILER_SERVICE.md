# Reconciler Service Documentation

## Overview
The `app.services.reconciler` module contains the `Reconciler` class, responsible for auditing and fixing infrastructure drift. It acts as the synchronization layer between the "Source of Truth" (the Database) and the "Actual State" (the Infrastructure Providers).

The service identifies inconsistencies such as orphaned resources, missing VMs (ghosts), or status mismatches (drift) and automatically corrects them to ensure the database reflects reality.

---

## Dependencies

| Library/Module | Usage |
|:---------------|:------|
| `app.providers` | Uses the Provider Abstraction Layer (`get_container_provider`) to interact with infrastructure. |
| `app.providers.base` | Defines `ProviderException`, `ProviderType` for error handling and type routing. |
| `sqlalchemy.orm.Session` | Database session to query and update VM records. |
| `app.models.vm` | Database model representing the VM state. |
| `app.workers.tasks.vm` | Celery tasks used to trigger re-provisioning of ghost VMs (`deploy_vm_task`). |
| `logging` | Logs discrepancies and reconciliation actions. |

---

## Key Concepts

The Reconciler categorizes infrastructure discrepancies into three specific types:

| Term | Definition | Resolution Action |
| :--- | :--- | :--- |
| **Orphans** | Resources found in the Provider that are **not** present in the Database. These are unmanaged or abandoned resources. | **Delete from Provider.** The resource is forcibly removed via the provider interface. |
| **Ghosts** | VMs present in the Database but **missing** from the Provider. This indicates a crash, external deletion, or failed provisioning. | **Delete from DB** (during full reconcile) or **Re-provision** (via specific fix method). |
| **Drift** | VMs that exist in both places but have mismatched statuses (e.g., DB says `running`, Provider says `stopped`). | **Update DB.** The database status is updated to match the reality of the infrastructure. |

### Managed Resources
The reconciler only interacts with resources that are explicitly labeled as managed by the application.
*   **Label Filter:** `label=managed_by=proxmox-automation-cloud`
*   This prevents the system from accidentally modifying user-owned resources or resources from other applications.

### Multi-Provider Support
The reconciler supports multiple providers (Docker, vSphere, Proxmox). When `provider=None`, it audits **all** providers sequentially.

---

## Class: `Reconciler`

### Method: `get_container_status`
**Signature:** `def get_container_status(self, provider: str) -> Dict[str, str]`

**Description:**
Queries the infrastructure provider for all managed resources and returns a simplified map of their states.

**Workflow:**
1.  Calls `get_container_provider(provider)` to retrieve the correct driver.
2.  Calls `list_containers(label_filter={"managed_by": "proxmox-automation-cloud"})`.
3.  Maps resource names to their simplified status (`running` or `stopped`).

**Returns:**
*   `Dict[str, str]`: A dictionary where keys are resource names and values are status strings.

**Error Handling:**
*   Catches `ProviderException` and returns an empty dictionary `{}` if the provider fails to list resources.

---

### Method: `audit`
**Signature:** `def audit(self, db: Session, provider: str = None)`

**Description:**
Performs a read-only comparison between the Database and Provider state to generate a discrepancy report. When `provider=None`, audits all providers.

**Workflow:**
1.  Fetches the actual state from the provider using `get_container_status`.
2.  Fetches VMs from the Database, filtered by `VM.provider == provider`.
3.  **Detects Orphans:** Iterates through provider resources. If a resource name is not found in the DB list, it is added to the `orphans` list.
4.  **Detects Ghosts & Drift:** Iterates through DB VMs.
    *   If the VM name does not exist in the provider status map, it is added to the `ghosts` list.
    *   If the VM name exists but the status differs (comparing boolean running states), it is added to the `drift` list.
5.  Returns a report dictionary.

**Returns:**
```json
{
  "orphans": [
    { "name": "lost_resource", "status": "stopped" }
  ],
  "ghosts": [
    { "vm_id": 5, "name": "db-server-01", "db_status": "running" }
  ],
  "drift": [
    { "vm_id": 2, "name": "web-01", "db_status": "running", "real_status": "stopped" }
  ],
  "synced": ["cache-01"]
}
```

---

### Method: `reconcile_all`
**Signature:** `def reconcile_all(self, db: Session, provider: str = None)`

**Description:**
Performs a full reconciliation to correct all issues found during the audit. When `provider=None`, reconciles all providers.

**Workflow:**
1.  Calls `audit(db, provider)` to identify discrepancies.
2.  Initializes the container provider.
3.  **Fix Orphans:** For every orphan resource, calls `provider.remove(name, force=True)`.
4.  **Fix Ghosts:** Queries the DB for the ghost VM record, releases IP reservation, and deletes the record (`db.delete(vm)`).
    *   Commits the database transaction after ghost deletion.
5.  **Fix Drift:** Updates the `status` column in the database to match the actual provider status.
    *   Commits the database transaction after status updates.

**Returns:**
*   A dictionary containing lists of successfully purged orphans, ghosts, and corrected drift items.

**Error Handling:**
*   If the provider fails to initialize, logs an error and returns empty results immediately.

---

### Method: `fix_ghost_vm`
**Signature:** `def fix_ghost_vm(self, db: Session, vm_id: int) -> Tuple[bool, str]`

**Description:**
Attempts to repair a specific VM identified as a "Ghost" by resetting its state and triggering the re-provisioning pipeline.

**Workflow:**
1.  Fetches the VM from the database by `vm_id`.
2.  Sets the VM status to `pending` to reset its state.
3.  Commits the change to the database.
4.  Reconstructs the VM configuration dictionary (`cpu`, `ram`, `disk_size_mb`, `network_id`, etc.).
5.  Dispatches an asynchronous `deploy_vm_task` via Celery to recreate the infrastructure.

**Returns:**
*   `(True, "Re-provision task dispatched successfully")` on success.
*   `(False, "VM not found")` if the VM ID does not exist.

---

## API Integration

The reconciler is exposed via admin endpoints:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `GET /v1/admin/audit` | Read-only audit report |
| `POST /v1/admin/reconcile` | Full reconciliation |
| `POST /v1/admin/fix/{vm_id}` | Fix specific ghost VM |

### Authorization
Requires either:
- **Super Admin** role (system-wide)
- **Tenant Admin** role (tenant-scoped)
- Admin permission (`vm:delete` or equivalent)
