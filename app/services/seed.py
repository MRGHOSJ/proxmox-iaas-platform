from sqlalchemy import text


def seed_default_pod(db):
    """
    Creates the first pod. Edit node_names to match your Proxmox node.
    Safe to re-run (ON CONFLICT DO NOTHING).
    """
    db.execute(text("""
        INSERT INTO pods (name, provider_type, node_names, max_tenants, tenant_count, status)
        VALUES ('pod-1', 'proxmox', 'pve1', 100, 0, 'active')
        ON CONFLICT (name) DO NOTHING
    """))
    db.commit()


def seed_global_ip_pool(db):
    """
    Seeds 172.16.x.x (safe, 4096 subnets) and 10.x.x.x (overflow, 65536 subnets).
    Safe to re-run. Expect this to take 10-30 seconds on first run.
    """
    rows = []

    for i in range(4096):
        second = 16 + (i // 256)
        third  = i % 256
        rows.append({
            "cidr":       f"172.{second}.{third}.0/24",
            "gateway_ip": f"172.{second}.{third}.1",
            "pool":       "safe",
            "status":     "free",
        })

    for i in range(65536):
        second = i // 256
        third  = i % 256
        rows.append({
            "cidr":       f"10.{second}.{third}.0/24",
            "gateway_ip": f"10.{second}.{third}.1",
            "pool":       "overflow",
            "status":     "free",
        })

    db.execute(text("""
        INSERT INTO global_ip_pool (cidr, gateway_ip, pool, status)
        VALUES (:cidr, :gateway_ip, :pool, :status)
        ON CONFLICT (cidr) DO NOTHING
    """), rows)
    db.commit()


def seed_vlan_pool(db, pod_id: int):
    """
    Seeds VLAN IDs 10–4094 for a pod. VLANs 1–9 are reserved for infrastructure.
    Run once per new pod.
    """
    rows = [
        {"pod_id": pod_id, "vlan_id": v, "status": "free"}
        for v in range(10, 4095)
    ]
    db.execute(text("""
        INSERT INTO vlan_allocations (pod_id, vlan_id, status)
        VALUES (:pod_id, :vlan_id, :status)
        ON CONFLICT DO NOTHING
    """), rows)
    db.commit()


def seed_wireguard_pool(db, cidr: str = "10.200.0.0/14"):
    """
    Seeds 1024 /24 subnets (10.200.0.0/14) for WireGuard tunnel allocation.
    Safe to re-run.
    """
    import ipaddress
    network = ipaddress.ip_network(cidr, strict=False)
    rows = []
    for sub in network.subnets(new_prefix=24):
        rows.append({
            "cidr":       str(sub),
            "gateway_ip": f"{sub.network_address + 1}",
            "status":     "free",
        })

    db.execute(text("""
        INSERT INTO wireguard_ip_pool (cidr, gateway_ip, status)
        VALUES (:cidr, :gateway_ip, :status)
        ON CONFLICT (cidr) DO NOTHING
    """), rows)
    db.commit()
