# VM Service Logic Documentation

## Overview

The `app.services.vm` module contains the core business logic for managing the lifecycle of Virtual Machines. It acts as the bridge between the FastAPI endpoints (database/schemas) and the infrastructure layer (Terraform/Provider Abstraction).

**Key Responsibility:** 
This module handles database transactions, validation rules, and the orchestration of external tools to ensure VM states remain consistent. It implements a **Hybrid Management** approach:
1.  **Provisioning/Destruction:** Uses **Terraform** to ensure robust, stateful infrastructure creation and deletion.
2.  **Lifecycle Operations:** Uses a **Provider Abstraction Layer** (`app.providers`) for fast, stateless operations like start, stop, and log retrieval.

---

## Dependencies

This module relies on the following internal and external libraries:

| Dependency | Usage |
|------------|-------|
| `app.models.vm` | SQLAlchemy `VM` model for database interactions. |
| `app.schemas.vm` | Pydantic schemas (`VMCreate`, `VMUpdate`) for validating input data. |
| `app.services.terraform` | Functions to generate HCL code, manage workspaces, and execute `terraform init/apply/destroy`. |
| `app.providers` | Factory methods `get_hypervisor_provider` and `get_container_provider` for provider-specific operations. |
| `logging` | Standard Python logging for operational visibility. |

---

## State Machine & Validation

The service enforces a strict state machine to prevent invalid operations.

### Valid Status Transitions

The `VALID_STATUS_TRANSITIONS` dictionary (imported from `app.models.vm`) defines allowed paths:

| Current Status | Allowed Next States |
| :--- | :--- |
| `pending` | `provisioning`, `running`, `error` |
| `provisioning` | `running`, `error` |
| `running` | `stopped`, `error` |
| `stopped` | `running`, `error` |
| `error` | `pending`, `running`, `stopped` |

### Action Validation

The `validate_vm_action` function enforces business rules before executing infrastructure commands:

| Action | Required Status | Error Condition |
| :--- | :--- | :--- |
| **Start** | `stopped` or `error` | Cannot start VM if already running or pending. |
| **Stop** | `running` | Cannot stop VM if not running. |
| **Restart** | `running` | Cannot restart VM if not running. |
| **Delete** | Any except `running` | Cannot delete running VM unless `force=True`. |

---

## Functions

### 1. Create VM (`create_vm_logic`)

**Signature:** `def create_vm_logic(db: Session, vm_data: VMCreate, owner_id: int) -> VM`

**Description:**
Synchronously provisions a new Virtual Machine. This function is typically called by the Celery background worker.

**Workflow:**
1.  **Uniqueness Check:** Queries the database to ensure the VM name is unique.
2.  **Database Record:** Creates a new `VM` record with status `pending` and attaches the `owner_id`. Uses `db.flush()` to generate the ID.
3.  **Terraform Context:** Calls `get_terraform_context` (passing the `db` session for network lookups).
4.  **Execution:** Runs `run_terraform_job` with `workspace_prefix="vm"`.
5.  **Success Handling:**
    *   Updates status to `running`.
    *   Parses outputs: Sets IP address (`127.0.0.1:{port}` for Docker).
    *   Commits transaction.
6.  **Error Handling:** Catches exceptions, sets VM status to `error`, commits the status change, and re-raises the exception.

**Returns:**
*   The created `VM` SQLAlchemy object.

**Exceptions:**
*   `ValueError`: If VM name is not unique.
*   `Exception`: Re-raised after setting status to `error` if Terraform fails.

---

### 2. Delete VM (`delete_vm_logic`)

**Signature:** `def delete_vm_logic(db: Session, vm_id: int, force: bool = False)`

**Description:**
Permanently destroys a VM and its infrastructure resources.

**Workflow:**
1.  **Validation:** Checks if VM exists. Validates action (prevents deleting `running` VMs unless `force=True`).
2.  **State Reconstruction:** Reconstructs a `VMCreate` object from the database record. This is crucial because Terraform requires the original configuration (cpu, ram, network) to properly plan the destruction of resources.
3.  **Destruction:** Calls `destroy_terraform_job` targeting the `vm_{id}` workspace.
4.  **Cleanup:** Deletes the record from the `vms` table.

**Exceptions:**
*   `ValueError`: If VM not found or safety check fails.
*   `Exception`: If Terraform destroy fails.

---

### 3. Start VM (`start_vm_logic`)

**Signature:** `def start_vm_logic(db: Session, vm_id: int)`

**Description:**
Boots a stopped VM using the configured provider backend (e.g., Docker).

**Workflow:**
1.  **Validation:** Ensures VM exists and status is `stopped` or `error`.
2.  **Provider Execution:** Resolves the provider via `get_container_provider` and calls `start()`.
3.  **Update:** Sets status to `running` in the database.

**Exceptions:**
*   `ValueError`: If VM not found, validation fails, or provider reports an error.
*   `ProviderException`: Caught and re-raised as `ValueError` for the API layer.

---

### 4. Stop VM (`stop_vm_logic`)

**Signature:** `def stop_vm_logic(db: Session, vm_id: int)`

**Description:**
Gracefully halts a running VM.

**Workflow:**
1.  **Validation:** Ensures VM exists and status is `running`.
2.  **Provider Execution:** Resolves the provider and calls `stop()`.
3.  **Update:** Sets status to `stopped` in the database.

**Exceptions:**
*   `ValueError`: If VM not found or provider reports an error.

---

### 5. Restart VM (`restart_vm_logic`)

**Signature:** `def restart_vm_logic(db: Session, vm_id: int)`

**Description:**
Performs a graceful restart.

**Workflow:**
1.  **Validation:** Ensures VM exists and status is `running`.
2.  **Provider Execution:** Resolves the provider and calls `restart()`.
3.  **Update:** Sets status to `running`.

**Exceptions:**
*   `ValueError`: If VM not found or provider reports an error.

---

### 6. Get VM Logs (`get_vm_logs_logic`)

**Signature:** `def get_vm_logs_logic(db: Session, vm_id: int, tail: int)`

**Description:**
Retrieves the last `N` lines of logs using the provider abstraction.

**Workflow:**
1.  **Validation:** Checks if VM exists.
2.  **Provider Execution:** Calls `get_logs()` on the resolved provider instance.
3.  **Return:** Returns a dictionary containing `logs`, `vm_name`, `vm_id`, and `lines` count.

**Exceptions:**
*   `ValueError`: If VM not found or provider fails to retrieve logs.