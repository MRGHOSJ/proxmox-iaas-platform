# Packer Templates for Proxmox IaaS Platform

This directory contains Packer templates for building VM templates on Proxmox.

## Prerequisites

1. **Packer** must be installed:
   ```bash
   # Download from https://www.hashicorp.com/products/packer/downloads
   # Or via package manager:
   brew install packer  # macOS
   choco install packer # Windows
   ```

2. **Proxmox VE** must be accessible at the configured URL

## Setup

### 1. Create Proxmox API Token

1. **Login** to Proxmox Web UI (https://YOUR_PROXMOX_HOST:8006)
2. Click **Datacenter** -> **Permissions** -> **API Tokens**
3. Click **Add**:
   - **User**: `root@pam`
   - **Token ID**: `packer-builder`
   - **Uncheck "Privilege Separation"**
4. **Copy the Token Secret** (you won't see it again)

### 2. Configure Environment Variables

Create/Update `proxmox-iaas-platform/.env`:
```env
# Proxmox Configuration
PROXMOX_URL=https://YOUR_PROXMOX_HOST:8006/api2/json
PROXMOX_USERNAME=root@pam!packer-builder
PROXMOX_PASSWORD=your-token-secret-here
PROXMOX_NODE=pve
```

### 3. Build the Template

```bash
cd packer
python build_opnsense.py
```

The script will:
- Load credentials from `.env`
- Initialize Packer
- Download OPNsense ISO (if not cached)
- Build the template
- Install `os-api-backup` plugin
- Convert to Proxmox template

## What Gets Built

| Property | Value |
|----------|-------|
| Template Name | `opnsense` |
| VM ID | `9000` |
| OS | OPNsense 24.1 |
| CPU | 1 core |
| RAM | 512 MB |
| Disk | 8 GB |
| Plugin | `os-api-backup` (REST API) |
| Build Time | ~5-10 minutes |

## File Structure

```
packer/
├── build_opnsense.py           # Build script (loads .env)
├── Dockerfile                  # Packer container image
├── README.md                   # This file
└── templates/
    ├── opnsense.pkr.hcl       # Main Packer configuration
    └── variables.pkrvars.hcl  # Static config (no secrets)
```

## Security

- **API Token**: Uses token authentication, not root password
- **Default Password**: Template uses `opnsense` as default
- **Change on Clone**: When cloning for tenants, backend changes password to unique value
- **No Secrets in Git**: Credentials are loaded from `.env`, not committed

## Troubleshooting

### Boot Command Issues

If the installer gets stuck:
1. Check Proxmox console output in Web UI
2. Adjust `<wait>` times in `opnsense.pkr.hcl`
3. Common issue: OPNsense 24.1 installer may need explicit key presses

### ISO Download Fails

If checksum verification fails:
```hcl
iso_checksum = "none"  # Temporarily disable
```

### SSH Timeout

If SSH connection times out:
```hcl
ssh_timeout = "30m"  # Increase timeout
```

### Permission Denied

If you get permission errors:
1. Verify API token was created with full privileges
2. Check Proxmox user has required permissions on storage

## Next Steps

After successful build:
1. Verify template in Proxmox Web UI (Datacenter -> VM Templates)
2. Implement tenant provisioning flow (Phase 3)
