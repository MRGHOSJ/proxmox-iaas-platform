# Platform - Testing Documentation

This document outlines the testing strategy, fixtures, execution guidelines, and coverage for the Platform. The test suite utilizes **Pytest** to ensure API reliability, data integrity, and service logic correctness without requiring a full infrastructure environment.

---

## Table of Contents
- [Technology Stack](#technology-stack)
- [Project Structure](#project-structure)
- [Test Organization & Markers](#test-organization--markers)
- [Core Fixtures (`conftest.py`)](#core-fixtures-conftestpy)
- [Test Modules Breakdown](#test-modules-breakdown)
- [Mocking Strategy](#mocking-strategy)
- [Running Tests](#running-tests)

---

## Technology Stack

| Tool/Library | Purpose |
|--------------|---------|
| **Pytest** | Test runner, fixture management, and parametrization |
| **SQLite (File-Based)** | Isolated database for testing; created/dropped per function (`test.db`) |
| **FastAPI TestClient** | Simulates HTTP requests to the API endpoints |
| **Unittest.mock** | Mocks external dependencies (Terraform, Docker CLI, Providers) to prevent side effects |
| **SQLAlchemy** | ORM for interacting with the test database |
| **Pytest-Cov** | Generates code coverage reports |
| **Pytest-Xdist** | Enables parallel test execution (`-n` flag) |

---

## Project Structure

Tests are located in `app/tests/`.

```text
app/tests/
├── conftest.py          # Global fixtures, DB setup, Mocks, Helper classes
├── test_admin.py        # Admin API: Audit, Fix, Reconcile endpoints
├── test_auth.py         # Authentication: Register, Login, JWT, User Management
├── test_exceptions.py   # Unit tests for custom exceptions
├── test_ipam.py         # IPAM Service: CIDR validation, Allocation logic
├── test_network.py      # Network API: CRUD, Status, Deletion
├── test_reconciler.py   # Reconciler Service: Orphans, Ghosts, Drift detection
├── test_schemas.py      # Pydantic Schema validation (Unit)
├── test_security.py     # Security Service: Hashing, JWT creation (Unit)
├── test_tasks.py        # Celery Task logic (Unit)
├── test_terraform.py    # Terraform Service: Template rendering (Unit)
├── test_vm_service.py   # VM Service Layer logic (Unit)
├── test_vm.py           # VM API: CRUD, Lifecycle, Logs (Integration)
├── test_factory.py      # Provider factory tests
├── test_rate_limit.py   # Rate limiting tests
├── test_token_blacklist.py  # Token blacklist tests
└── test_tenant_isolation.py # Multi-tenant isolation tests
```

---

## Test Organization & Markers

We use **Pytest Markers** to categorize tests. This allows you to run specific subsets of tests.

### Available Markers

| Marker | Description |
|--------|-------------|
| `unit` | Fast tests that do not hit the database or API |
| `integration` | Tests that hit the API endpoints (use `TestClient`) |
| `auth` | Authentication specific tests |
| `vm` | Virtual Machine API tests |
| `network` | Network API tests |
| `ipam` | IP Address Management service tests |
| `admin` | Admin/Reconciler endpoint tests |
| `reconciler` | Infrastructure reconciliation logic tests |
| `security` | Security/Token service tests |

### Pytest Configuration

Registered in `conftest.py`:

```python
def pytest_configure(config):
    markers = [
        "unit: Unit tests",
        "integration: Integration tests",
        # ... other markers
    ]
    for marker in markers:
        config.addinivalue_line("markers", marker)
```

### Usage Examples

```bash
# Run only unit tests (fastest)
pytest -m unit

# Run only integration tests
pytest -m integration

# Run only VM and Network tests
pytest -m "vm or network"
```

---

## Core Fixtures (`conftest.py`)

Fixtures are reusable components that set up the initial state for tests.

### 1. Database & Client Setup

| Fixture | Scope | Description |
|---------|-------|-------------|
| `test_engine` | session | Creates SQLite engine for `test.db` |
| `test_session_factory` | session | Creates session factory bound to test engine |
| `setup_db` | function | Creates all tables before test, drops after. Also resets the rate limiter. |
| `client` | function | Provides `TestClient` with overridden `get_db` dependency |
| `db_session` | function | Provides direct database session for direct DB manipulation in tests |

**Implementation Detail:**
The `setup_db` fixture runs automatically (`autouse=True`) and ensures a clean slate for every test function, resetting both the database schema and the in-memory rate limiter state.

### 2. Helper Classes

#### HelperUtils
Utility for generating random strings to avoid naming conflicts.

```python
class HelperUtils:
    @staticmethod
    def random_suffix():
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
```

#### AuthHelper
Automates user registration and login flows. It supports dynamic role assignment.

| Method | Description |
|--------|-------------|
| `create_authenticated_user(client, db_session, role)` | Registers user, updates role in DB (if session provided), logs in, and returns auth headers. |
| `create_admin_user(client)` | Helper for `role="admin"`. |
| `create_vm_operator_user(client)` | Helper for `role="vm_operator"`. |

**Key Logic:** If a `db_session` is passed, the helper manually updates the user's role in the database immediately after registration to bypass default "viewer" assignment.

#### VMHelper
Simplifies VM API calls.

| Method | Description |
|--------|-------------|
| `create_vm(client, headers, name, **kwargs)` | POST to `/vm/create` with defaults for provider, CPU, RAM. |
| `stop_vm`, `start_vm`, `restart_vm` | Lifecycle endpoints. |
| `delete_vm(client, headers, vm_id, force)` | DELETE `/vm/{id}`. |

#### NetworkHelper
Simplifies Network API calls.

| Method | Description |
|--------|-------------|
| `create_network(client, headers, name, **kwargs)` | POST to `/networks/` with default CIDR. |
| `get_network`, `list_networks`, `delete_network` | Standard CRUD wrappers. |

### 3. Authentication Fixtures

| Fixture | Role | Description |
|---------|------|-------------|
| `auth_headers` | `vm_operator` | Default authenticated user headers |
| `admin_headers` | `admin` | Admin role headers |
| `network_admin_headers` | `network_admin` | Network admin headers |
| `viewer_headers` | `viewer` | Read-only user headers |

### 4. Resource Fixtures (Auto-Cleanup)

#### `created_vm`
Creates a VM and guarantees deletion after the test finishes. It mocks `destroy_terraform_job` during the cleanup phase to prevent errors.

```python
@pytest.fixture(scope="function")
def created_vm(client, auth_headers, mock_terraform_dynamic, db_session):
    name = f"auto-vm-{HelperUtils.random_suffix()}"
    response = VMHelper.create_vm(client, auth_headers, name)
    vm_data = response.json()
    vm_id = vm_data["id"]

    yield vm_data  # Test runs here

    # Cleanup (always runs, even on failure)
    with patch('app.services.vm.destroy_terraform_job') as mock_destroy:
        mock_destroy.return_value = {"status": "destroyed"}
        VMHelper.delete_vm(client, auth_headers, vm_id, force=True)
```

#### `created_network`
Creates a Network and guarantees deletion after the test finishes. Mocks the Celery task dispatch for deletion during cleanup.

### 5. Mock Fixtures

| Fixture | Target | Description |
|---------|--------|-------------|
| `mock_terraform_dynamic` | `app.services.terraform.run_terraform_job` | Returns success with random port (8000-9000) to allow parallel testing. |
| `mock_docker_commands` | `subprocess.run` | Fakes Docker CLI responses for start/stop/restart. |
| `mock_deploy_network_task` | `app.api.networks.deploy_network_task.delay` | Fakes Celery network creation. |
| `mock_destroy_network_task` | `app.api.networks.destroy_network_task.delay` | Fakes Celery network deletion. |
| `mock_docker_subprocess` | `subprocess.run` | Fakes Docker network commands for IPAM tests. |

---

## Test Modules Breakdown

### Authentication Tests (`test_auth.py`)

**Coverage:**
- **Registration:** Success, duplicate handling, invalid email, weak password validation.
- **Login:** Success, wrong password, inactive user blocking.
- **Token Validation:** Expired tokens, malformed tokens, invalid signatures.
- **User Management:** List users, create user, update role, update status.
- **Self-Modification Prevention:** Admins cannot delete/deactivate/change role of themselves.

**Key Tests:**
- `test_register_weak_password`: Ensures Pydantic validators reject weak passwords.
- `test_login_inactive_user`: Verifies 403 Forbidden response.

### VM Tests (`test_vm.py`)

**Coverage:**
- **CRUD:** Create, Read, Update, Delete, List.
- **Lifecycle:** Start, Stop, Restart logic using mocks for `get_container_provider`.
- **Networking:** Tests creating VMs attached to networks (mocking IP allocation).
- **Pagination & Filtering:** Tests `status_filter`, `provider_filter`, `limit`, and `offset`.
- **Stats:** Aggregation verification.

**Key Tests:**
- `test_create_vm_duplicate_name`: Expects 409 Conflict.
- `test_lifecycle_stop_start_restart`: Full lifecycle flow simulation.
- `test_create_vm_with_network`: Validates IP assignment logic integration.

### Network Tests (`test_network.py`)

**Coverage:**
- **CRUD:** Create, List, Get, Delete.
- **Validation:** Invalid CIDR formats.
- **Authorization:** 403 for non-network-admins.

**Key Tests:**
- `test_create_network_invalid_cidr`: Tests Pydantic validation.
- `test_delete_network_success`: Simulates successful deletion flow.

### Admin & Reconciler Tests (`test_admin.py` & `test_reconciler.py`)

**Coverage:**
- **Audit:** Mocks `Reconciler.audit` to return orphans/ghosts/drift.
- **Fix:** Mocks `Reconciler.fix_ghost_vm`.
- **Reconcile:** Mocks `Reconciler.reconcile_all`.
- **Provider Abstraction:** Tests that the reconciler correctly uses the `get_container_provider` factory and handles `ProviderException`.

**Key Tests:**
- `test_audit_detects_orphans`: Validates JSON structure of the audit report.
- `test_reconcile_purges_orphans`: Verifies provider `remove` method calls.

### Unit Tests

| Module | Coverage |
|--------|----------|
| `test_schemas.py` | Pydantic validation: VM name regex, RAM limits, Password strength (uppercase, lowercase, digit requirements), `VMStatusUpdate` requiring `reason`. |
| `test_security.py` | `bcrypt` hashing verification, JWT encoding/decoding, expiration claims, subject validation. |
| `test_ipam.py` | IP allocation logic, CIDR overlap checks, Docker subnet parsing, Gateway skipping, `free_ip` cleanup. |
| `test_terraform.py` | Jinja2 context generation, template rendering mocks, HCL value formatting logic. |
| `test_vm_service.py` | Service layer state transitions (pending -> running -> stopped), provider abstraction integration. |
| `test_factory.py` | Provider factory: correct provider selection, fallback handling. |
| `test_rate_limit.py` | Rate limiting: request throttling, window resets, per-user limits. |
| `test_token_blacklist.py` | Token blacklist: logout invalidation, expired token cleanup. |
| `test_tenant_isolation.py` | Multi-tenant isolation: cross-tenant access prevention, data segregation. |
| `test_tasks.py` | Celery task failure handling, status updates to 'error', `wait_for_docker_network` timeout logic. |
| `test_exceptions.py` | Custom `TerraformExecutionError` attributes (message/logs). |

---

## Mocking Strategy

### Why Heavy Mocking?

1.  **Speed**: Real Terraform apply takes minutes; mocks take milliseconds.
2.  **Isolation**: Tests run without Docker/Terraform installed.
3.  **Parallelism**: Random ports and mocked subprocesses allow `pytest-xdist` to run tests safely in parallel.

### Provider Abstraction Mocking

For VM lifecycle tests (start/stop/restart/logs), we mock the provider factory rather than raw `subprocess` calls. This allows testing the service logic without depending on the Docker CLI implementation details.

```python
@patch('app.services.vm.get_container_provider')
def test_lifecycle_stop_start_restart(mock_get_provider, client, auth_headers, created_vm):
    mock_provider = MagicMock()
    mock_provider.stop.return_value = True
    mock_get_provider.return_value = mock_provider
    
    # Test logic...
```

### Random Port Generation

To prevent port conflicts when running tests in parallel:

```python
@pytest.fixture
def mock_terraform_dynamic():
    with patch('app.services.terraform.run_terraform_job') as mock:
        random_port = random.randint(8000, 9000)
        mock.return_value = {
            "status": "success", 
            "outputs": {"port": random_port, "ip": "127.0.0.1"}
        }
        yield mock
```

---

## Running Tests

### Basic Commands

```bash
# Run all tests
pytest

# Run with coverage report (HTML)
pytest --cov=app --cov-report=html

# Run specific file
pytest app/tests/test_auth.py

# Run specific test class
pytest app/tests/test_vm.py::TestLifecycle

# Run in parallel (4 workers)
pytest -n 4

# Run only unit tests
pytest -m unit

# Run VM and network tests
pytest -m "vm or network"
```