import os
import subprocess
import sys
from pathlib import Path
from dotenv import load_dotenv

# ── Paths ─────────────────────────────────────────────────────────────────────
script_dir   = Path(__file__).parent.resolve()
project_root = script_dir.parent
env_file     = project_root / ".env"

print("============================================")
print("  Building OPNsense Golden Image (Docker)")
print("============================================")

# ── Load .env ─────────────────────────────────────────────────────────────────
if env_file.exists():
    print(f">>> Loading environment from {env_file}")
    load_dotenv(env_file)
else:
    print(f"ERROR: .env file not found at {env_file}")
    sys.exit(1)

PROXMOX_HOST     = os.getenv("PROXMOX_URL", "").split("//")[-1].split(":")[0]
OPNSENSE_VERSION = os.getenv("OPNSENSE_VERSION", "26.1.2")
ROOT_PASSWORD    = os.getenv("ROOT_PASSWORD", "opnsense")
VM_ID            = 9000

# ── SSH helper (password prompt allowed — no BatchMode) ───────────────────────
def ssh(command: str, check=True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=10",
            f"root@{PROXMOX_HOST}",
            command
        ],
        text=True,
        # No capture_output — let stdin/stdout flow so password prompt works
    )
    if check and result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, command)
    return result

def ssh_capture(command: str, check=True) -> subprocess.CompletedProcess:
    """SSH but capture output (for steps where we need the result)."""
    result = subprocess.run(
        [
            "ssh",
            "-o", "StrictHostKeyChecking=no",
            "-o", "ConnectTimeout=10",
            f"root@{PROXMOX_HOST}",
            command
        ],
        capture_output=True, text=True
    )
    if check and result.returncode != 0:
        print(f"    SSH ERROR: {result.stderr.strip()}")
        raise subprocess.CalledProcessError(result.returncode, command)
    return result

# ── Step 1: Cleanup VM ────────────────────────────────────────────────────────
def cleanup_vm():
    print(f"\n>>> [1/4] Cleaning up VM {VM_ID} on {PROXMOX_HOST} (if exists)...")
    print(f"    (enter Proxmox root password if prompted)")
    ssh(
        f"qm stop {VM_ID} --skiplock 2>/dev/null; "
        f"qm destroy {VM_ID} --purge 2>/dev/null; "
        f"echo '    VM cleaned up.'",
        check=False
    )

# ── Step 2: Create seed ISO on Proxmox ───────────────────────────────────────
def create_seed_iso():
    print(f"\n>>> [2/4] Creating seed ISO on Proxmox...")
    print(f"    (enter Proxmox root password if prompted)")

    config_xml = f"""<?xml version="1.0"?>
<opnsense>
  <version>{OPNSENSE_VERSION}</version>
  <system>
    <hostname>opnsense-template</hostname>
    <domain>local</domain>
    <password>{ROOT_PASSWORD}</password>
    <timezone>Etc/UTC</timezone>
    <language>en_US</language>
  </system>
  <interfaces>
    <wan>
      <if>vtnet0</if>
      <enable>1</enable>
      <descr>WAN</descr>
      <ipaddr>dhcp</ipaddr>
    </wan>
    <lan>
      <if>vtnet1</if>
      <enable>1</enable>
      <descr>LAN</descr>
      <ipaddr>192.168.1.1</ipaddr>
      <subnet>24</subnet>
    </lan>
  </interfaces>
  <dhcpd>
    <lan>
      <enable>1</enable>
      <range>
        <from>192.168.1.100</from>
        <to>192.168.1.200</to>
      </range>
    </lan>
  </dhcpd>
  <sshd>
    <enabled>enabled</enabled>
  </sshd>
</opnsense>"""

    # Write config.xml locally then SCP to Proxmox
    local_xml = script_dir / "config.xml"
    local_xml.write_text(config_xml, encoding="utf-8")

    print(f"    Uploading config.xml to Proxmox...")
    print(f"    (enter Proxmox root password if prompted)")
    subprocess.run(
        [
            "scp",
            "-o", "StrictHostKeyChecking=no",
            str(local_xml),
            f"root@{PROXMOX_HOST}:/tmp/config.xml"
        ],
        check=True
    )
    local_xml.unlink()

    # Build ISO on Proxmox
    print(f"    Building ISO on Proxmox...")
    print(f"    (enter Proxmox root password if prompted)")
    result = ssh_capture(
        """
        set -e
        apt-get install -y genisoimage -qq 2>/dev/null || true
        rm -rf /tmp/opnsense-seed
        mkdir -p /tmp/opnsense-seed/conf
        cp /tmp/config.xml /tmp/opnsense-seed/conf/config.xml
        genisoimage \
            -o /var/lib/vz/template/iso/seed.iso \
            -R -J \
            -V "OPNsense-Seed" \
            /tmp/opnsense-seed/ 2>/dev/null
        rm -rf /tmp/opnsense-seed /tmp/config.xml
        ls -lh /var/lib/vz/template/iso/seed.iso
        """,
        check=True
    )
    print(f"    {result.stdout.strip().splitlines()[-1]}")
    print(f"    seed.iso ready.")

# ── Step 3: Build Docker image ────────────────────────────────────────────────
def build_docker():
    print("\n>>> [3/4] Building Packer Docker Image...")
    subprocess.run(
        ["docker", "build", "-t", "cloud-packer:latest", str(script_dir)],
        check=True
    )

# ── Step 4: Run Packer ────────────────────────────────────────────────────────
def run_packer(secrets_path: Path):
    print("\n>>> [4/4] Starting Packer build...")
    print("    This will take 10-15 minutes...")

    cache_dir   = script_dir / "packer_cache"
    plugins_dir = script_dir / ".packer_plugins"
    cache_dir.mkdir(exist_ok=True)
    plugins_dir.mkdir(exist_ok=True)

    docker_run_cmd = [
        "docker", "run", "--rm",
        "-w", "/app",
        "-v", f"{plugins_dir}:/root/.config/packer",
        "-v", f"{script_dir}/templates:/app/templates",
        "-v", f"{cache_dir}:/opt/packer"
    ]

    print("    Initializing Packer plugins...")
    subprocess.run(
        docker_run_cmd + ["cloud-packer:latest", "init", "templates/opnsense.pkr.hcl"],
        check=True
    )

    print("    Running build...\n")
    subprocess.run(
        docker_run_cmd + [
            "cloud-packer:latest", "build",
            "-var-file=templates/variables.pkrvars.hcl",
            "-var-file=templates/secrets.pkrvars.hcl",
            "templates/opnsense.pkr.hcl"
        ],
        check=True
    )

# ── Main ──────────────────────────────────────────────────────────────────────
secrets_path = script_dir / "templates" / "secrets.pkrvars.hcl"

try:
    cleanup_vm()
    create_seed_iso()
    build_docker()

    # Write secrets with guaranteed LF endings
    secrets_content = "\n".join([
        f'proxmox_url      = "{os.getenv("PROXMOX_URL")}"',
        f'proxmox_username = "{os.getenv("PROXMOX_USERNAME")}"',
        f'proxmox_token    = "{os.getenv("PROXMOX_TOKEN")}"',
        f'proxmox_node     = "{os.getenv("PROXMOX_NODE")}"',
        ""
    ])
    secrets_path.write_bytes(secrets_content.encode("utf-8"))
    print(f">>> Generated secrets file at {secrets_path}")

    run_packer(secrets_path)

    print("\n============================================")
    print("  Build Complete!")
    print("  Template 'opnsense' is ready in Proxmox")
    print("  VM ID: 9000")
    print("============================================")

except subprocess.CalledProcessError as e:
    print(f"\nERROR: Build failed with exit code {e.returncode}")
    sys.exit(1)

finally:
    if secrets_path.exists():
        secrets_path.unlink()
        print(">>> Cleaned up secrets file")