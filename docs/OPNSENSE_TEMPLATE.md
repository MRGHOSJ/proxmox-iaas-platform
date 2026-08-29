# OPNsense Template Preparation

This document describes how to prepare the OPNsense VM template for tenant provisioning using the "NAT Isolation" architecture (overlapping LAN subnets) with **Per-Tenant API Key Rotation**.

## Requirements

- **OPNsense VM ID**: 9000 (or configurable)
- **qemu-guest-agent**: Must be installed for Proxmox to query VM IP addresses and execute scripts.
- **Network Model**: WAN on shared bridge (`vmbr0`), LAN on isolated per-tenant bridges.
- **PHP Script**: A custom script is baked into the template to rotate API keys.

## Preparation Steps

### 1. Install OPNsense

1. Download OPNsense ISO from https://opnsense.org/download/
2. Create a new VM in Proxmox:
   - VM ID: 9000
   - Name: template-opnsense
   - OS Type: Linux
   - CD/DVD: Use ISO image
   - Disk: 8GB SCSI
   - NIC 0: vmbr0 (WAN)
   - NIC 1: vmbr1 (LAN) - temporary, will be replaced per tenant

### 2. Initial OPNsense Setup

Through the console:

1. **Assign Interfaces**:
   - vtnet0 → WAN
   - vtnet1 → LAN

2. **Configure WAN**:
   - Set to **DHCP** (Critical: Ensures cloned VMs get unique IPs from the upstream router).

3. **Configure LAN**:
   - IP: `10.0.0.1`
   - Subnet: `/24`
   - **Note:** This standardizes the tenant gateway. Every tenant will use this IP, isolated by their specific bridge.

4. **Enable DHCP on LAN**:
   - Services → DHCPv4 → LAN → Enable
   - Range: `10.0.0.10` - `10.0.0.200`

### 3. Configure WAN Access (CRITICAL)

By default, OPNsense blocks access to the WAN interface from private networks. You must allow your management network (Lab Subnet) to access the API/GUI.

**Step A: Disable Private Network Blocking**
1. Navigate to **Interfaces → WAN**.
2. Scroll to the bottom.
3. **Uncheck** "Block private networks".
4. **Uncheck** "Block bogon networks".
5. Save and Apply.

**Step B: Add Management Firewall Rule**
1. Navigate to **Firewall → Rules → WAN**.
2. Click **Add** (arrow up).
3. Configure:
   - **Action**: Pass
   - **Protocol**: TCP
   - **Source**: `192.168.0.0/16` (Or your specific lab subnet. This covers local management access).
   - **Destination**: WAN Address
   - **Destination Port Range**: HTTPS (443)
   - **Description**: "Allow Lab Management"
4. Click **Save** and **Apply Changes**.

### 4. Install qemu-guest-agent (CRITICAL)

Required for the provisioning system to detect the VM's WAN IP address **and execute the key rotation script**.

1. Navigate to **System → Firmware → Plugins**.
2. Search for `os-qemu-guest-agent`.
3. Click **Install**.
4. Navigate to **Services → QEMU Guest Agent**.
5. Check **Enable** and click **Start**.
6. Verify status is "Running".

### 5. Create Automation User & Bootstrap Key (CRITICAL)

We use a "Bootstrap Key" (K0) approach. The template has a known key that the backend uses to log in for the first time. The backend then immediately rotates this key to a unique one for the tenant.

1. Navigate to **System → Access → Users**.
2. Click **+** to add a user.
   - **Username**: `provisioner`
   - **Password**: (Set a strong password).
   - **Full Name**: Provisioning Service
3. **Assign Groups**: Select `admins`.
   - *Note: This grants sufficient API access for the health check.*
4. Click **Save**.

**Generate the Bootstrap Key (K0):**
1. After saving the user, click the **pencil (edit)** icon next to `provisioner` again.
2. Scroll down to the **API Keys** section.
3. Click the **+** button to generate a new key.
4. **IMPORTANT**: Copy both the **Key** and **Secret**.
   - You will need to paste these into your backend `.env` file:
     ```
     OPNSENSE_BOOTSTRAP_KEY=<paste_key_here>
     OPNSENSE_BOOTSTRAP_SECRET=<paste_secret_here>
     ```
5. Click **Save**.

### 6. Enable and Configure API

1. Navigate to **System → Settings → Administration**.
2. Scroll to the **API** section.
3. **Enable API Server**: Checked.
4. **Protocol**: HTTPS.
5. **Port**: 443.
6. **Allow API access from**: Set to **Any** (or your Lab Subnet).
7. Click **Save**.

**Verify API Access:**
Run this from a machine on your Lab Network. *Note: OPNsense API requires `key:secret`, not `user:password`.*
```bash
# Replace <KEY> and <SECRET> with the values from Step 5
curl -k -u "<KEY>:<SECRET>" https://<WAN_IP>/api/core/firmware/status
```
You should receive a JSON response, not a 401/403 error.

### Direct SSH to OPNsense

If networking is functional, you can SSH directly into OPNsense from the Proxmox host.

**Enable SSH in OPNsense:**
1. Go to **System > Settings > Administration**.
2. Check **Enable Secure Shell**.
3. Ensure **Permit root login** is checked if you need root access (though generally discouraged for security).

**Connect via SSH:**
From the Proxmox shell, run:
```bash
ssh root@<OPNsense-IP-Address>
```

Log in with your OPNsense root credentials. The default password for a fresh install is `opnsense`.


### 7. Deploy Key Rotation Script (NEW - CRITICAL)

This script allows the backend to generate a unique API key for every tenant by modifying the OPNsense configuration directly.

1. Navigate to **Diagnostics → Edit File**.
2. Paste the following PHP code into the editor:
3. or use ee (the built-in editor) OPNsense includes ee (“easy editor”). It avoids heredoc headaches entirely:
```
ee /conf/rotate_keys.php
```

Paste your PHP code inside, press Esc, then Enter to save and exit.

```php
cat << 'EOF' > /conf/rotate_keys.php
<?php
function generateRandomBase64($length = 60) {
    return base64_encode(random_bytes($length));
}

$xml = new DOMDocument();
if (!$xml->load('/conf/config.xml')) {
    fwrite(STDERR, "ERROR: Could not load config.xml\n");
    exit(1);
}

$xpath = new DOMXPath($xml);
$user = $xpath->query("/opnsense/system/user[name='provisioner']")->item(0);
if (!$user) {
    fwrite(STDERR, "ERROR: provisioner user not found\n");
    exit(1);
}

$apikeys = $user->getElementsByTagName('apikeys')->item(0);
if (!$apikeys) {
    $apikeys = $xml->createElement('apikeys');
    $user->appendChild($apikeys);
}

// Remove ALL existing keys — kills K0 (Bootstrap Key)
while ($apikeys->firstChild) {
    $apikeys->removeChild($apikeys->firstChild);
}

// Generate K1 (New Per-Tenant Key)
$newKey       = generateRandomBase64();
$newSecret    = generateRandomBase64();
$hashedSecret = crypt($newSecret, '$6$' . bin2hex(random_bytes(8)));

$item = $xml->createElement('item');
$item->appendChild($xml->createElement('key', $newKey));
$item->appendChild($xml->createElement('secret', $hashedSecret));
$apikeys->appendChild($item);

$xml->save('/conf/config.xml');

// Clear cache and reload configd
@unlink('/tmp/config.cache');
exec('kill -HUP $(cat /var/run/configd.pid) 2>/dev/null');

// Output to STDOUT (Backend captures this)
echo "APIKEY={$newKey}\n";
echo "APISECRET={$newSecret}\n";
?>

EOF
```

3. Save the file as: `/conf/rotate_keys.php`
4. **Test the script**:
   - Go to **Diagnostics → Command Prompt**.
   - Run: `php /conf/rotate_keys.php`
   - It should output a new `APIKEY` and `APISECRET`.
   - **IMPORTANT**: If you test the script, you have effectively rotated the key. You must go back to **Step 5** and generate a new Bootstrap Key (K0) for the template, otherwise your backend will fail to connect on the first boot.

### 8. Finalize Configuration (Setup Wizard)

It is recommended to run the setup wizard once to clear "first boot" flags.

1. Access the Web GUI.
2. If the Wizard pops up, run it.
   - **General Info**: Set Timezone.
   - **WAN**: Ensure set to DHCP.
   - **LAN**: **CRITICAL** - Ensure it is set to `10.0.0.1/24`.
   - **Root Password**: Set a known "break-glass" password.
3. Finish and Reload.

### 9. Clean Up and Shutdown

1. Clear logs and history (optional but recommended):
   ```bash
   rm -rf /var/log/*
   rm -rf /conf/backup/*
   ```
2. Shutdown the VM:
   ```bash
   shutdown -h now
   ```

### 10. Convert to Template

1. In Proxmox UI, right-click VM 9000.
2. Select **Convert to template**.

---

## Template Configuration Summary

| Setting | Value |
|---------|-------|
| VM ID | 9000 |
| WAN Interface | vtnet0 (DHCP) |
| LAN Interface | vtnet1 (Static: 10.0.0.1/24) |
| Firewall (WAN) | Allow Lab Subnet (TCP 443) |
| Block Private Networks | Disabled on WAN |
| qemu-guest-agent | Installed & running |
| API Access | Allowed from Any/Lab Subnet |
| Automation User | `provisioner` (Group: admins) |
| Bootstrap Key (K0) | Present in config (matches backend `.env`) |
| Rotation Script | `/conf/rotate_keys.php` exists |

---

## Troubleshooting

### VM doesn't get WAN IP
1. Check qemu-guest-agent is running:
   ```bash
   service qemu-guest-agent status
   ```
2. Check Proxmox can query the agent:
   - Proxmox UI → VM → Summary → IP

### Provisioning fails at health check / 401 Unauthorized
- **Check Bootstrap Key:** Ensure the Key/Secret in `.env` matches the key currently inside the Template VM.
- **Check API Auth:** Remember OPNsense uses `key:secret` auth, NOT `user:password`.
- **Check Firewall:** Ensure the "Allow Lab Subnet" rule exists on WAN.

### Key Rotation Fails (Tenant stays at K0)
- **Check Script:** Ensure `/conf/rotate_keys.php` exists and has no syntax errors (run `php -l /conf/rotate_keys.php`).
- **Check Guest Agent:** Ensure the Proxmox Guest Agent is enabled and running inside the VM.
