# Proxmox IaaS Platform

A multi-tenant Infrastructure-as-a-Service platform built on Proxmox VE, featuring VM provisioning, network isolation, firewall management (OPNsense), WireGuard VPN, and Terraform integration.

## Features

- **Multi-tenancy** with tenant isolation, quotas, and approval workflows
- **VM provisioning** via Proxmox with cloud-init, SSH key injection, snapshots, and resize
- **IAM** with 30+ granular permissions, multi-role per user per tenant
- **Networking** -- TenantNetworks, VLANs, IPAM with PostgreSQL advisory locks
- **Firewall** -- OPNsense-based with two-phase commit (pending -> apply)
- **VPN** -- WireGuard tunnels with per-tenant IP pools
- **Real-time** -- WebSocket updates via Redis pub/sub
- **Secrets** -- HashiCorp Vault integration with env-var fallback
- **Audit logging** -- 50+ action types with request ID tracking
- **Rate limiting** -- Redis-based distributed rate limiting with fail-closed production mode
- **Image management** -- Packer-based template builds, Proxmox template registry
- **Reconciler** -- Drift detection and auto-remediation

## Architecture

```
Internet
    |
    v
[Shared WAN Bridge vmbr0] --- DHCP WAN IP
    |
    v
[OPNsense Firewall VM] --- NAT/Firewall
    |
    v
[Tenant LAN Bridge vmbrN] --- 172.x.x.0/24
    |
    v
[Tenant VMs] --- Static/DHCP IPs
```

## Tech Stack

| Component | Technology |
|-----------|------------|
| API | FastAPI (Python 3.11) |
| Database | SQLAlchemy + PostgreSQL 15 |
| Task Queue | Celery + Redis 7 |
| Virtualization | Proxmox VE |
| Firewall | OPNsense (REST API) |
| VPN | WireGuard |
| IaC | Terraform + Packer |
| Auth | JWT + bcrypt |
| Real-time | WebSocket + Redis Pub/Sub |
| Deployment | Docker + docker-compose |

## Prerequisites

- Python 3.11+
- PostgreSQL 14+
- Redis 7+
- Proxmox VE 7+ (for virtualization)
- Docker & docker-compose (for containerized deployment)

## Quick Start

### Option 1: Docker (Recommended)

```bash
git clone https://github.com/yourusername/proxmox-iaas-platform.git
cd proxmox-iaas-platform

# Configure environment
cp .env.example .env
# Edit .env with your Proxmox credentials

# Start all services
docker-compose up -d

# Access the API
open http://localhost:8000/docs
```

### Option 2: Local Development

```bash
git clone https://github.com/yourusername/proxmox-iaas-platform.git
cd proxmox-iaas-platform

# Install dependencies
pip install -r app/requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your database and Proxmox credentials

# Run database migrations
python -c "from app.main import engine, Base; Base.metadata.create_all(bind=engine)"

# Start the API server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Start Celery worker (in a separate terminal)
celery -A app.workers.celery_app worker --loglevel=info --concurrency=2
```

## Default Credentials

After first run, the default admin is created:

| Field | Value |
|-------|-------|
| Username | `admin` |
| Password | `admin123` (or `DEFAULT_ADMIN_PASSWORD`) |

**Change this in production!**

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `POST /v1/auth/register` | Register user + tenant |
| `POST /v1/auth/login` | Get JWT token |
| `GET /v1/auth/me` | Get current user |
| `GET /v1/vm/list` | List VMs |
| `POST /v1/vm/provision` | Provision Proxmox VM |
| `POST /v1/vm/{id}/start` | Start VM |
| `POST /v1/vm/{id}/stop` | Stop VM |
| `POST /v1/vm/{id}/restart` | Restart VM |
| `DELETE /v1/vm/{id}` | Delete VM |
| `GET /v1/networks/` | List networks |
| `POST /v1/networks/` | Create network |
| `GET /v1/firewall/providers` | List firewall providers |
| `POST /v1/firewall/{provider}/rules` | Create firewall rule |
| `GET /v1/wireguard/tunnels` | List WireGuard tunnels |
| `POST /v1/wireguard/tunnels` | Create WireGuard tunnel |
| `GET /v1/images` | List images |
| `POST /v1/images/build` | Start image build |
| `GET /v1/tenants/my-tenants` | Get my tenants |
| `GET /v1/admin/audit` | View audit logs |

Full API documentation is available at `/docs` (Swagger UI) and `/redoc` (ReDoc).

## Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=html

# Run specific test file
pytest app/tests/test_auth.py -v
```

## Project Structure

```
proxmox-iaas-platform/
├── app/
│   ├── api/              # FastAPI routers (presentation layer)
│   ├── core/             # Security, config, IAM, audit, rate limiting
│   ├── models/           # SQLAlchemy ORM models
│   ├── schemas/          # Pydantic request/response models
│   ├── services/         # Business logic layer
│   ├── providers/        # Hypervisor provider abstraction
│   ├── workers/          # Celery background tasks
│   ├── tests/            # Test suite
│   ├── terraform/        # Terraform templates
│   ├── Dockerfile        # API server container
│   └── Dockerfile.worker # Celery worker container
├── docs/                 # Technical documentation
├── packer/               # Packer templates for VM image builds
├── docker-compose.yml    # Multi-service deployment
├── .env.example          # Environment variable template
└── readme.md             # This file
```

## Documentation

- [Technical Documentation](docs/readme.md) -- Architecture, API reference, data models
- [Authentication API](docs/app/api/AUTH_API.md) -- Auth endpoints and JWT details
- [VM API](docs/app/api/VM_API.md) -- VM lifecycle management
- [Network API](docs/app/api/NETWORK_API.md) -- Network and IPAM management
- [Firewall API](docs/app/api/FIREWALL_API.md) -- OPNsense firewall management
- [WireGuard API](docs/app/api/WIREGUARD_API.md) -- VPN tunnel management
- [IAM API](docs/app/api/IAM_API.md) -- Permissions and roles
- [Images API](docs/app/api/IMAGES_API.md) -- Image/template management
- [OPNsense Template](docs/OPNSENSE_TEMPLATE.md) -- OPNsense deployment guide

## Environment Variables

See [`.env.example`](.env.example) for all configurable options. Key variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `PROXMOX_URL` | Proxmox API URL | `https://10.0.0.51:8006` |
| `PROXMOX_USERNAME` | Proxmox API username | `root@pam!packer-builder` |
| `PROXMOX_TOKEN` | Proxmox API token | - |
| `PROXMOX_NODE` | Proxmox node name | `pve` |
| `DATABASE_URL` | PostgreSQL connection string | `postgresql://user:pass@localhost:5432/db` |
| `REDIS_URL` | Redis connection string | `redis://localhost:6379/0` |
| `SECRET_KEY` | Application secret key | - |
| `JWT_SECRET_KEY` | JWT signing key | - |
| `VAULT_ENABLED` | Enable HashiCorp Vault | `false` |

## Contributing

Contributions are welcome! Please open an issue first to discuss what you would like to change.
