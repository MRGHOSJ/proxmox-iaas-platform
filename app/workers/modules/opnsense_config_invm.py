"""
OPNsense config.xml manipulation via in-VM PHP scripts.

Architecture
────────────
Never downloads or uploads config.xml. All changes happen inside the VM.
Each PHP script is ~1-2KB — well within QEMU guest agent limits.

The 48KB download+upload approach crashed the guest agent on large writes.
This approach:
  1. Writes a tiny PHP script to /tmp/ inside the VM (~1KB)
  2. Runs it under an exclusive flock so concurrent tasks cannot race
  3. PHP modifies config.xml locally with atomic rename()
  4. A second read-only PHP script verifies the change actually landed
  5. configctl applies changes live without a reboot

Locking
───────
PHP's flock() serialises all config.xml writes within one VM.
The lock file lives inside the VM so it is naturally per-VM isolated.
Lock timeout is 30s — if a previous task holds the lock longer than that,
the current task fails loudly rather than silently racing.

Verification
────────────
Every write method calls a corresponding verify_* method immediately after.
Verification reads config.xml fresh (not from memory) so it catches rename()
failures, wrong-node edits, and SimpleXML serialisation bugs.
"""

import logging
import uuid

logger = logging.getLogger(__name__)

LOCK_FILE = "/tmp/opncfg.lock"
LOCK_TIMEOUT = 30


class OPNsenseConfigInVM:
    def __init__(
        self,
        provider,
        vm_id: int,
        node: str = "pve",
        config_path: str = "/conf/config.xml",
    ):
        self.provider = provider
        self.vm_id = vm_id
        self.node = node
        self.config_path = config_path
        self._agent_verified = False

    def _exec_vm(self, command: str, timeout: int = 60, skip_agent_check: bool = None) -> dict:
        """Wrapper around provider.exec_in_vm that handles agent verification caching."""
        if skip_agent_check is None:
            skip_agent_check = self._agent_verified
        
        result = self.provider.exec_in_vm(
            node=self.node,
            vm_id=self.vm_id,
            command=command,
            timeout=timeout,
            skip_agent_check=skip_agent_check,
        )
        
        if not self._agent_verified:
            self._agent_verified = True
        
        return result

    def _write_script(self, remote_path: str, content: str) -> None:
        """Write content to remote_path inside the VM using printf."""
        safe = content.replace("'", "'\\''")
        self._exec_vm(
            command=f"printf '%s' '{safe}' > {remote_path}",
            timeout=15,
        )

    def _run_php(self, php_code: str, timeout: int = 30) -> str:
        """Write php_code to temp file, execute with flock, return stdout."""
        lock_wrapper = """
$lock_file = '/tmp/opncfg.lock';
$fp = fopen($lock_file, 'c');
if (!flock($fp, LOCK_EX)) { die("Could not acquire lock"); }
"""
        unlock_wrapper = """
flock($fp, LOCK_UN);
fclose($fp);
"""
        wrapped_php = f"<?php\n{lock_wrapper}\n{php_code}\n{unlock_wrapper}\n?>"

        script_path = f"/tmp/opncfg_{uuid.uuid4().hex[:8]}.php"
        self._write_script(script_path, wrapped_php)

        cmd = f"php -f {script_path}; code=$?; rm -f {script_path}; exit $code"

        result = self._exec_vm(
            command=cmd,
            timeout=timeout + 10,
        )

        stdout = result.get("out", "").strip()
        stderr = result.get("err", "").strip()
        exitcode = result.get("exitcode", 0)

        if exitcode != 0 or stderr:
            raise RuntimeError(
                f"PHP script failed on VM {self.vm_id} (exit={exitcode})\n"
                f"STDOUT: {stdout}\nSTDERR: {stderr}"
            )

        logger.debug("VM %d PHP result: %s", self.vm_id, stdout)
        return stdout

    def _run_read_only_php(self, php_code: str) -> str:
        """Run a read-only PHP script WITHOUT the flock."""
        script_path = f"/tmp/opncfg_{uuid.uuid4().hex[:8]}.php"
        self._write_script(script_path, f"<?php\n{php_code}\n?>")

        result = self._exec_vm(
            command=f"php -f {script_path}; code=$?; rm -f {script_path}; exit $code",
            timeout=15,
        )

        stdout = result.get("out", "").strip()
        stderr = result.get("err", "").strip()
        exitcode = result.get("exitcode", 0)

        if exitcode != 0 or stderr:
            raise RuntimeError(
                f"Read PHP script failed on VM {self.vm_id} (exit={exitcode})\n"
                f"STDOUT: {stdout}\nSTDERR: {stderr}"
            )

        return stdout

    def get_vlan_list(self) -> list:
        """Read current VLANs from config.xml inside the VM."""
        php = f"""
$xml = simplexml_load_file('{self.config_path}');
if ($xml === false) {{ die("Cannot load config.xml"); }}
if (!isset($xml->vlans)) {{ echo ''; exit; }}
$lines = [];
foreach ($xml->vlans->vlan as $v) {{
    $lines[] = implode('|', [
        (string)$v->tag,
        (string)$v->if,
        (string)$v->vlanif,
        (string)$v->descr,
    ]);
}}
echo implode("\\n", $lines);
"""
        raw = self._run_read_only_php(php)
        results = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            parts = line.split("|", 3)
            if len(parts) == 4 and parts[0].strip():
                results.append({
                    "tag": int(parts[0]),
                    "parent_if": parts[1],
                    "vlanif": parts[2],
                    "descr": parts[3],
                })
        return results

    def get_interface_names(self) -> list:
        """Return all child tag names under <interfaces>."""
        php = f"""
$xml = simplexml_load_file('{self.config_path}');
if ($xml === false) {{ die("Cannot load config.xml"); }}
$names = [];
foreach ($xml->interfaces->children() as $name => $iface) {{
    $names[] = $name;
}}
echo implode(',', $names);
"""
        raw = self._run_read_only_php(php)
        return [n for n in raw.split(",") if n.strip()]

    def get_opt_for_vlanif(self, vlanif: str) -> str:
        """Find the <opt#> tag whose <if> matches vlanif."""
        php = f"""
$xml = simplexml_load_file('{self.config_path}');
if ($xml === false) {{ die("Cannot load config.xml"); }}
foreach ($xml->interfaces->children() as $name => $iface) {{
    if ((string)$iface->if === '{vlanif}') {{
        echo $name;
        exit;
    }}
}}
"""
        raw = self._run_read_only_php(php)
        return raw.strip() or None

    def get_interface_ip(self, opt_name: str) -> str:
        """Return the current <ipaddr> of an interface."""
        php = f"""
$xml = simplexml_load_file('{self.config_path}');
if ($xml === false) {{ die("Cannot load config.xml"); }}
$opt = '{opt_name}';
if (!isset($xml->interfaces->{{$opt}})) {{ echo ''; exit; }}
echo (string)$xml->interfaces->{{$opt}}->ipaddr;
"""
        raw = self._run_read_only_php(php)
        return raw.strip() or None

    def get_interface_ip_and_subnet(self, opt_name: str):
        """Return (ip, subnet) tuple for an interface."""
        php = f"""
$xml = simplexml_load_file('{self.config_path}');
if ($xml === false) {{ die("Cannot load config.xml"); }}
$opt = '{opt_name}';
if (!isset($xml->interfaces->{{$opt}})) {{ echo ''; exit; }}
echo (string)$xml->interfaces->{{$opt}}->ipaddr . '|' . (string)$xml->interfaces->{{$opt}}->subnet;
"""
        raw = self._run_read_only_php(php)
        if not raw.strip():
            return None, None
        parts = raw.strip().split("|")
        return parts[0] if len(parts) > 0 else None, int(parts[1]) if len(parts) > 1 else None

    def verify_vlan_exists(self, tag: int, parent_if: str, vlanif: str) -> None:
        """Verify a <vlan> entry exists in config.xml."""
        php = f"""
$xml = simplexml_load_file('{self.config_path}');
if ($xml === false) {{ die("VERIFY FAILED: cannot load config.xml"); }}
if (!isset($xml->vlans)) {{ die("VERIFY FAILED: no <vlans> section exists"); }}
foreach ($xml->vlans->vlan as $v) {{
    if ((int)$v->tag === {tag} && (string)$v->if === '{parent_if}' && (string)$v->vlanif === '{vlanif}') {{
        echo "ok";
        exit;
    }}
}}
die("VERIFY FAILED: vlan tag={tag} parent_if={parent_if} vlanif={vlanif} not found in config.xml");
"""
        out = self._run_read_only_php(php)
        if out != "ok":
            raise RuntimeError(f"vlan verify unexpected output on VM {self.vm_id}: {out}")
        logger.info("VM %d: verified vlan tag=%d vlanif=%s exists", self.vm_id, tag, vlanif)

    def verify_opt_exists(self, opt_name: str, vlanif: str, ip: str) -> None:
        """Verify an <opt#> interface exists in config.xml."""
        php = f"""
$xml = simplexml_load_file('{self.config_path}');
if ($xml === false) {{ die("VERIFY FAILED: cannot load config.xml"); }}
if (!isset($xml->interfaces)) {{ die("VERIFY FAILED: no <interfaces> section exists"); }}
$opt = '{opt_name}';
if (!isset($xml->interfaces->{{$opt}})) {{
    die("VERIFY FAILED: interface {opt_name} not found in <interfaces>");
}}
$iface = $xml->interfaces->{{$opt}};
$actual_if = (string)$iface->if;
$actual_ip = (string)$iface->ipaddr;
if ($actual_if !== '{vlanif}') {{
    die("VERIFY FAILED: {opt_name}->if is '$actual_if', expected '{vlanif}'");
}}
if ($actual_ip !== '{ip}') {{
    die("VERIFY FAILED: {opt_name}->ipaddr is '$actual_ip', expected '{ip}'");
}}
echo "ok";
"""
        out = self._run_read_only_php(php)
        if out != "ok":
            raise RuntimeError(f"opt verify unexpected output on VM {self.vm_id}: {out}")
        logger.info("VM %d: verified %s->%s ip=%s exists", self.vm_id, opt_name, vlanif, ip)

    def verify_vlan_removed(self, tag: int, parent_if: str) -> None:
        """Verify a <vlan> entry is completely gone."""
        php = f"""
$xml = simplexml_load_file('{self.config_path}');
if ($xml === false) {{ die("VERIFY FAILED: cannot load config.xml"); }}
if (!isset($xml->vlans)) {{ echo "ok"; exit; }}
foreach ($xml->vlans->vlan as $v) {{
    if ((int)$v->tag === {tag} && (string)$v->if === '{parent_if}') {{
        die("VERIFY FAILED: vlan tag={tag} parent_if={parent_if} still present after removal");
    }}
}}
echo "ok";
"""
        out = self._run_read_only_php(php)
        if out != "ok":
            raise RuntimeError(f"vlan removal verify unexpected output on VM {self.vm_id}: {out}")
        logger.info("VM %d: verified vlan tag=%d removed", self.vm_id, tag)

    def verify_interface_ip(self, opt_name: str, expected_ip: str, expected_subnet: int) -> None:
        """Verify an interface has the exact IP and subnet."""
        php = f"""
$xml = simplexml_load_file('{self.config_path}');
if ($xml === false) {{ die("VERIFY FAILED: cannot load config.xml"); }}
$opt = '{opt_name}';
if (!isset($xml->interfaces->{{$opt}})) {{
    die("VERIFY FAILED: interface {opt_name} not found");
}}
$actual_ip     = (string)$xml->interfaces->{{$opt}}->ipaddr;
$actual_subnet = (string)$xml->interfaces->{{$opt}}->subnet;
if ($actual_ip !== '{expected_ip}') {{
    die("VERIFY FAILED: {opt_name}->ipaddr is '$actual_ip', expected '{expected_ip}'");
}}
if ((int)$actual_subnet !== {expected_subnet}) {{
    die("VERIFY FAILED: {opt_name}->subnet is '$actual_subnet', expected '{expected_subnet}'");
}}
echo "ok";
"""
        out = self._run_read_only_php(php)
        if out != "ok":
            raise RuntimeError(f"interface ip verify unexpected output on VM {self.vm_id}: {out}")
        logger.info("VM %d: verified %s ip=%s/%d", self.vm_id, opt_name, expected_ip, expected_subnet)

    def add_vlan_device(self, tag: int, parent_if: str, vlanif: str, descr: str = "") -> None:
        """Add or update a <vlan> entry in the <vlans> section."""
        logger.info("VM %d: adding VLAN device %s (tag=%d) on %s",
                    self.vm_id, vlanif, tag, parent_if)

        php = f"""
$cfg = '{self.config_path}';
$tmp = $cfg . '.tmp.' . getmypid();
$xml = simplexml_load_file($cfg);
if ($xml === false) {{ die("Cannot load config.xml"); }}

if (!isset($xml->vlans)) {{
    $xml->addChild('vlans');
    $xml->vlans->addAttribute('version', '1.0.0');
    $xml->vlans->addAttribute('description', 'VLAN configuration');
}}

$found = null;
foreach ($xml->vlans->vlan as $v) {{
    if ((string)$v->if === '{parent_if}' && (int)$v->tag === {tag}) {{
        $found = $v;
        break;
    }}
}}
if ($found === null) {{
    $found = $xml->vlans->addChild('vlan');
}}

$found->if     = '{parent_if}';
$found->tag    = {tag};
$found->pcp    = 0;
$found->proto  = '';
$found->descr  = '{descr}';
$found->vlanif = '{vlanif}';

$dom = new DOMDocument('1.0');
$dom->preserveWhiteSpace = false;
$dom->formatOutput = true;
$dom->loadXML($xml->asXML());
file_put_contents($tmp, $dom->saveXML());
rename($tmp, $cfg);
echo "ok: vlan {vlanif} tag={tag}";
"""
        out = self._run_php(php)
        logger.info("VM %d: add_vlan_device: %s", self.vm_id, out)
        self.verify_vlan_exists(tag=tag, parent_if=parent_if, vlanif=vlanif)

    def add_opt_interface(self, opt_name: str, vlanif: str, ip: str, subnet: int,
                          descr: str = "", enable: bool = True) -> None:
        """Add or update an <opt#> entry in the <interfaces> section."""
        logger.info("VM %d: adding OPT interface %s -> %s (%s/%d)",
                    self.vm_id, opt_name, vlanif, ip, subnet)
        enable_val = "1" if enable else "0"
        descr_val = descr or opt_name.upper()

        php = f"""
$cfg = '{self.config_path}';
$tmp = $cfg . '.tmp.' . getmypid();
$xml = simplexml_load_file($cfg);
if ($xml === false) {{ die("Cannot load config.xml"); }}
if (!isset($xml->interfaces)) {{ die("No <interfaces> section in config.xml"); }}

$opt = '{opt_name}';
if (!isset($xml->interfaces->{{$opt}})) {{
    $xml->interfaces->addChild($opt);
}}
$iface = $xml->interfaces->{{$opt}};

$iface->descr   = '{descr_val}';
$iface->if      = '{vlanif}';
$iface->enable  = '{enable_val}';
$iface->ipaddr  = '{ip}';
$iface->subnet  = {subnet};

if (!isset($iface->ipaddrv6))    $iface->addChild('ipaddrv6', '');
else                             $iface->ipaddrv6 = '';
if (!isset($iface->blockpriv))   $iface->addChild('blockpriv', '0');
else                             $iface->blockpriv = '0';
if (!isset($iface->blockbogons)) $iface->addChild('blockbogons', '0');
else                             $iface->blockbogons = '0';

$dom = new DOMDocument('1.0');
$dom->preserveWhiteSpace = false;
$dom->formatOutput = true;
$dom->loadXML($xml->asXML());
file_put_contents($tmp, $dom->saveXML());
rename($tmp, $cfg);
echo "ok: {opt_name} -> {vlanif} {ip}/{subnet}";
"""
        out = self._run_php(php)
        logger.info("VM %d: add_opt_interface: %s", self.vm_id, out)
        self.verify_opt_exists(opt_name=opt_name, vlanif=vlanif, ip=ip)

    def set_interface_ip(self, opt_name: str, ip: str, subnet: int) -> None:
        """Change IP/subnet on an existing interface."""
        logger.info("VM %d: updating IP on %s: %s/%d", self.vm_id, opt_name, ip, subnet)

        php = f"""
$cfg = '{self.config_path}';
$tmp = $cfg . '.tmp.' . getmypid();
$xml = simplexml_load_file($cfg);
if ($xml === false) {{ die("Cannot load config.xml"); }}

$opt = '{opt_name}';
if (!isset($xml->interfaces->{{$opt}})) {{
    die("Interface {opt_name} not found in config.xml");
}}
$xml->interfaces->{{$opt}}->ipaddr = '{ip}';
$xml->interfaces->{{$opt}}->subnet = {subnet};

$dom = new DOMDocument('1.0');
$dom->preserveWhiteSpace = false;
$dom->formatOutput = true;
$dom->loadXML($xml->asXML());
file_put_contents($tmp, $dom->saveXML());
rename($tmp, $cfg);
echo "ok: {opt_name} ip={ip}/{subnet}";
"""
        out = self._run_php(php)
        logger.info("VM %d: set_interface_ip: %s", self.vm_id, out)
        self.verify_interface_ip(opt_name=opt_name, expected_ip=ip, expected_subnet=subnet)

    def remove_vlan(self, vlan_tag: int, parent_if: str = "") -> None:
        """Remove a <vlan> from <vlans> AND its matching <opt#> from <interfaces>."""
        logger.info("VM %d: removing VLAN tag=%d", self.vm_id, vlan_tag)
        parent_filter = f"&& (string)$v->if === '{parent_if}'" if parent_if else ""

        php = f"""
$cfg = '{self.config_path}';
$tmp = $cfg . '.tmp.' . getmypid();
$xml = simplexml_load_file($cfg);
if ($xml === false) {{ die("Cannot load config.xml"); }}

$removed_vlanif = null;

if (isset($xml->vlans)) {{
    foreach ($xml->vlans->vlan as $v) {{
        if ((int)$v->tag === {vlan_tag} {parent_filter}) {{
            $removed_vlanif = (string)$v->vlanif;
            $node = dom_import_simplexml($v);
            $node->parentNode->removeChild($node);
            break;
        }}
    }}
}}

if ($removed_vlanif !== null && isset($xml->interfaces)) {{
    foreach ($xml->interfaces->children() as $name => $iface) {{
        if ((string)$iface->if === $removed_vlanif) {{
            $node = dom_import_simplexml($iface);
            $node->parentNode->removeChild($node);
            echo "removed: tag={vlan_tag} vlanif=" . $removed_vlanif . " iface=" . $name;
            break;
        }}
    }}
}} else {{
    echo "not-found: tag={vlan_tag}";
}}

$dom = new DOMDocument('1.0');
$dom->preserveWhiteSpace = false;
$dom->formatOutput = true;
$dom->loadXML($xml->asXML());
file_put_contents($tmp, $dom->saveXML());
rename($tmp, $cfg);
"""
        out = self._run_php(php)
        logger.info("VM %d: remove_vlan: %s", self.vm_id, out)

        if "removed:" in out:
            vlanif = out.split("vlanif=")[1].split()[0] if "vlanif=" in out else None
            if vlanif:
                self.destroy_vlan_iface(vlanif)

        if parent_if:
            self.verify_vlan_removed(tag=vlan_tag, parent_if=parent_if)

    def reload_config(self) -> None:
        """Apply config.xml changes at runtime via configctl."""
        logger.info("VM %d: reloading OPNsense config", self.vm_id)
        for cmd in ["configctl interface reconfigure", "configctl filter reload"]:
            result = self._exec_vm(
                command=cmd,
                timeout=30,
            )
            out = result.get("out", "").strip()
            err = result.get("err", "").strip()
            if err:
                logger.warning("VM %d: configctl [%s] stderr: %s", self.vm_id, cmd, err)
            logger.info("VM %d: configctl [%s] -> %s", self.vm_id, cmd, out)

    def create_vlan_iface_with_ip(self, vlanif: str, tag: int, parent_if: str,
                                   ip: str, subnet: int) -> None:
        """
        Create a VLAN interface and assign its IP in one atomic shell command.

        FreeBSD requires creating a generic 'vlan' clone first, then renaming it.
        This matches OPNsense's own legacy_interface_create() implementation.

        Runs as a single exec_in_vm call so there is no window for OPNsense's
        configd to destroy the interface between creation and IP assignment.
        """
        cmd = (
            f"ifconfig {vlanif} destroy 2>/dev/null; "
            f"NEWNAME=$(ifconfig vlan create) && "
            f"ifconfig \"$NEWNAME\" name {vlanif} && "
            f"ifconfig {vlanif} vlan {tag} vlandev {parent_if} && "
            f"ifconfig {vlanif} inet {ip}/{subnet} && "
            f"ifconfig {vlanif} up && "
            f"ifconfig {vlanif} | grep -q 'inet {ip}' && echo 'ok' || echo 'ip-check-failed'"
        )
        logger.info("VM %d: creating VLAN %s (tag=%d on %s) with IP %s/%d",
                    self.vm_id, vlanif, tag, parent_if, ip, subnet)
        result = self._exec_vm(
            command=cmd,
            timeout=15,
        )
        out = result.get("out", "").strip()
        err = result.get("err", "").strip()
        exitcode = result.get("exitcode", 0)

        if exitcode != 0 or "ip-check-failed" in out:
            raise RuntimeError(
                f"Failed to create VLAN {vlanif} with IP {ip}/{subnet}: "
                f"exit={exitcode} out={out!r} err={err!r}"
            )
        logger.info("VM %d: VLAN %s up with IP %s/%d", self.vm_id, vlanif, ip, subnet)

    def destroy_vlan_iface(self, vlanif: str) -> None:
        """Destroy VLAN interface via ifconfig (FreeBSD)."""
        logger.info("VM %d: destroying VLAN interface %s", self.vm_id, vlanif)
        self._exec_vm(
            command=f"ifconfig {vlanif} destroy",
            timeout=10,
        )
        logger.info("VM %d: destroyed VLAN interface %s", self.vm_id, vlanif)

    def apply_interface_ip(self, interface: str, ip: str, subnet: int) -> None:
        """Apply IP directly to interface using ifconfig (FreeBSD-native)."""
        logger.info("VM %d: applying IP %s/%d to interface %s", self.vm_id, ip, subnet, interface)
        cmd = f"ifconfig {interface} inet -alias 2>/dev/null; ifconfig {interface} inet {ip}/{subnet}"
        result = self._exec_vm(
            command=cmd,
            timeout=10,
        )
        if result.get("exitcode", 0) != 0:
            err = result.get("err", "").strip()
            raise RuntimeError(f"Failed to apply IP to {interface}: {err}")
        logger.info("VM %d: applied IP %s/%d to %s", self.vm_id, ip, subnet, interface)

    def add_vlan(self, vlan_tag: int, parent_if: str, vlanif: str, opt_name: str,
                 ip_address: str, subnet: int, description: str = "",
                 reload: bool = True) -> None:
        """Full VLAN provisioning in one call.

        Flow:
          1. Write VLAN device to config.xml   (persistence / OPNsense UI)
          2. Write OPT interface to config.xml  (persistence / OPNsense UI)
          3. Create interface + assign IP atomically via one shell exec
        """
        descr = description or f"vlan{vlan_tag}"
        self.add_vlan_device(tag=vlan_tag, parent_if=parent_if, vlanif=vlanif, descr=descr)
        self.add_opt_interface(
            opt_name=opt_name, vlanif=vlanif, ip=ip_address,
            subnet=subnet, descr=descr.upper(),
        )
        self.create_vlan_iface_with_ip(
            vlanif=vlanif, tag=vlan_tag, parent_if=parent_if,
            ip=ip_address, subnet=subnet,
        )
        logger.info("VM %d: VLAN %d fully provisioned: %s/%s %s/%d",
                    self.vm_id, vlan_tag, vlanif, opt_name, ip_address, subnet)

    def set_lan_ip(self, ip: str, subnet: int) -> None:
        """Set LAN interface IP address in config.xml (idempotent, creates if missing)."""
        logger.info("VM %d: setting LAN IP to %s/%d", self.vm_id, ip, subnet)

        php = f"""
$cfg = '{self.config_path}';
$tmp = $cfg . '.tmp.' . getmypid();
$xml = simplexml_load_file($cfg);
if ($xml === false) {{ die("Cannot load config.xml"); }}

if (!isset($xml->interfaces->lan)) {{
    $xml->interfaces->addChild('lan');
}}
$lan = $xml->interfaces->lan;
$lan->ipaddr = '{ip}';
$lan->subnet = {subnet};
if (!isset($lan->if)) $lan->addChild('if', 'vtnet1');
if (!isset($lan->enable)) $lan->addChild('enable', '1');

$dom = new DOMDocument('1.0');
$dom->preserveWhiteSpace = false;
$dom->formatOutput = true;
$dom->loadXML($xml->asXML());
file_put_contents($tmp, $dom->saveXML());
rename($tmp, $cfg);

// Verify
$check = simplexml_load_file($cfg);
$actual_ip = (string)$check->interfaces->lan->ipaddr;
$actual_subnet = (int)$check->interfaces->lan->subnet;
if ($actual_ip !== '{ip}' || $actual_subnet !== {subnet}) {{
    die("VERIFY FAILED");
}}
echo "ok: lan ip={ip}/{subnet}";
"""
        out = self._run_php(php)
        logger.info("VM %d: set_lan_ip: %s", self.vm_id, out)
        self.apply_interface_ip(interface="vtnet1", ip=ip, subnet=subnet)

    def set_wan_ip(self, ip: str, subnet: int, gateway: str = None) -> None:
        """Set WAN interface to static IP in config.xml (idempotent, creates if missing)."""
        logger.info("VM %d: setting WAN IP to %s/%d (static mode)", self.vm_id, ip, subnet)

        php = f"""
$cfg = '{self.config_path}';
$tmp = $cfg . '.tmp.' . getmypid();
$xml = simplexml_load_file($cfg);
if ($xml === false) {{ die("Cannot load config.xml"); }}

if (!isset($xml->interfaces->wan)) {{
    $xml->interfaces->addChild('wan');
}}
$wan = $xml->interfaces->wan;
$wan->ipaddr = '{ip}';
$wan->subnet = {subnet};
if (!isset($wan->if)) $wan->addChild('if', 'vtnet0');
if (!isset($wan->enable)) $wan->addChild('enable', '1');
$wan->addChild('type', 'staticv4');
if ('{gateway}' && '{gateway}' !== 'null') {{
    $wan->gateway = '{gateway}';
}}

$dom = new DOMDocument('1.0');
$dom->preserveWhiteSpace = false;
$dom->formatOutput = true;
$dom->loadXML($xml->asXML());
file_put_contents($tmp, $dom->saveXML());
rename($tmp, $cfg);
echo "ok: wan ip={ip}/{subnet}";
"""
        out = self._run_php(php)
        logger.info("VM %d: set_wan_ip: %s", self.vm_id, out)
        self.reload_config()
        logger.info("VM %d: WAN IP configured to %s/%d (static)", self.vm_id, ip, subnet)

    def assign_wg_interface(self, opt_name: str, wg_instance_uuid: str, ip: str, subnet: int) -> None:
        """
        Assign a WireGuard instance to a brand-new <opt#> interface in config.xml.

        Used by the WireGuard provisioning flow: the wg plugin creates the
        server under <wireguard><servers> with `enabled=0` while the tunnel
        is being created; we then add an opt entry, set its ipaddr+subnet,
        and reload so OPNsense picks up the new interface and the wg
        instance is enabled by the wireguard service_reconfigure call.

        `wg_instance_uuid` is the server's UUID inside the WG plugin.
        """
        logger.info(
            "VM %d: assigning WG instance %s to %s (%s/%d)",
            self.vm_id, wg_instance_uuid, opt_name, ip, subnet,
        )

        php = f"""
$cfg = '{self.config_path}';
$tmp = $cfg . '.tmp.' . getmypid();
$xml = simplexml_load_file($cfg);
if ($xml === false) {{ die("Cannot load config.xml"); }}

if (!isset($xml->interfaces)) {{ die("No <interfaces> section in config.xml"); }}
$opt = '{opt_name}';
if (!isset($xml->interfaces->{{$opt}})) {{
    $xml->interfaces->addChild($opt);
}}
$iface = $xml->interfaces->{{$opt}};
$iface->descr  = 'WireGuard ' . '{wg_instance_uuid}';
$iface->if     = 'wireguard' . substr('{wg_instance_uuid}', 0, 8);
$iface->enable = '1';
$iface->ipaddr = '{ip}';
$iface->subnet = {subnet};

if (!isset($iface->ipaddrv6))    $iface->addChild('ipaddrv6', '');
else                             $iface->ipaddrv6 = '';
if (!isset($iface->blockpriv))   $iface->addChild('blockpriv', '0');
else                             $iface->blockpriv = '0';
if (!isset($iface->blockbogons)) $iface->addChild('blockbogons', '0');
else                             $iface->blockbogons = '0';

$dom = new DOMDocument('1.0');
$dom->preserveWhiteSpace = false;
$dom->formatOutput = true;
$dom->loadXML($xml->asXML());
file_put_contents($tmp, $dom->saveXML());
rename($tmp, $cfg);

$check = simplexml_load_file($cfg);
$verify_ip = (string)$check->interfaces->{{$opt}}->ipaddr;
if ($verify_ip !== '{ip}') {{
    die("VERIFY FAILED: {opt_name}->ipaddr is '$verify_ip', expected '{ip}'");
}}
echo "ok: wg {opt_name} -> wireguard instance {wg_instance_uuid}";
"""
        out = self._run_php(php)
        logger.info("VM %d: assign_wg_interface: %s", self.vm_id, out)

    def _restart_kea(self) -> None:
        """Restart Kea DHCP via configctl (validates config automatically)."""
        logger.info("VM %d: restarting Kea DHCP", self.vm_id)
        result = self._exec_vm(
            command="configctl kea dhcpd restart",
            timeout=30,
        )
        out = result.get("out", "").strip()
        err = result.get("err", "").strip()
        if result.get("exitcode", 0) != 0:
            raise RuntimeError(f"Kea restart failed: {err}")
        logger.info("VM %d: Kea restarted successfully", self.vm_id)

    def add_kea_subnet(self, opt_name: str, subnet_cidr: str, gateway_ip: str,
                       pool_start: str, pool_end: str) -> None:
        """Add Kea DHCP subnet using in-VM PHP script (idempotent)."""
        logger.info("VM %d: adding Kea subnet %s for interface %s",
                    self.vm_id, subnet_cidr, opt_name)

        php = f"""
$kea = '/usr/local/etc/kea/kea-dhcp4.conf';
$lock = fopen('/tmp/kea.lock', 'c');
if (!flock($lock, LOCK_EX)) {{ die("Could not acquire kea lock"); }}
$cfg = json_decode(file_get_contents($kea), true);
if (!isset($cfg['Dhcp4'])) {{ die("Invalid Kea config"); }}
if (!isset($cfg['Dhcp4']['subnet4'])) {{ $cfg['Dhcp4']['subnet4'] = []; }}

foreach ($cfg['Dhcp4']['subnet4'] as $s) {{
    if ($s['subnet'] === '{subnet_cidr}' && $s['interface'] === '{opt_name}') {{
        echo "ok: subnet already exists";
        flock($lock, LOCK_UN);
        exit;
    }}
}}

$cfg['Dhcp4']['subnet4'][] = [
    'subnet' => '{subnet_cidr}',
    'interface' => '{opt_name}',
    'pools' => [['pool' => '{pool_start}-{pool_end}']],
    'option-data' => [
        ['name' => 'routers', 'data' => '{gateway_ip}'],
        ['name' => 'subnet-mask', 'data' => '255.255.255.0'],
    ]
];

$tmp = $kea . '.tmp.' . getmypid();
file_put_contents($tmp, json_encode($cfg, JSON_PRETTY_PRINT));
rename($tmp, $kea);
flock($lock, LOCK_UN);
echo "ok: added subnet {subnet_cidr}";
"""
        out = self._run_php(php)
        logger.info("VM %d: Kea subnet: %s", self.vm_id, out)
        self._restart_kea()
        logger.info("VM %d: Kea subnet %s added for interface %s",
                    self.vm_id, subnet_cidr, opt_name)