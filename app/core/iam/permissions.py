VM_PERMISSIONS = [
    "vm:create",
    "vm:read",
    "vm:update",
    "vm:delete",
    "vm:start",
    "vm:stop",
    "vm:restart",
    "vm:console",
    "vm:snapshot:create",
    "vm:snapshot:delete",
]

NETWORK_PERMISSIONS = [
    "network:create",
    "network:read",
    "network:update",
    "network:delete",
]

FIREWALL_PERMISSIONS = [
    "firewall:create",
    "firewall:read",
    "firewall:update",
    "firewall:delete",
]

WIREGUARD_PERMISSIONS = [
    "wireguard:create",
    "wireguard:read",
    "wireguard:update",
    "wireguard:delete",
]

IP_PERMISSIONS = [
    "ip:reserve",
    "ip:release",
    "ip:read",
]

AUDIT_PERMISSIONS = [
    "audit:read",
]

TENANT_PERMISSIONS = [
    "tenant:read",
    "tenant:update",
    "tenant:delete",
    "tenant:manage_users",
    "tenant:manage_roles",
    "tenant:settings",
]

USER_PERMISSIONS = [
    "user:invite",
    "user:remove",
    "user:update_roles",
    "user:read",
]

ALL_PERMISSIONS = (
    VM_PERMISSIONS +
    NETWORK_PERMISSIONS +
    FIREWALL_PERMISSIONS +
    WIREGUARD_PERMISSIONS +
    IP_PERMISSIONS +
    AUDIT_PERMISSIONS +
    TENANT_PERMISSIONS +
    USER_PERMISSIONS
)


ROLE_PERMISSION_MAPPING = {
    "tenant_admin": ALL_PERMISSIONS,
    "vm_admin": VM_PERMISSIONS + ["network:read", "firewall:create", "firewall:read", "firewall:update", "firewall:delete", "ip:reserve", "ip:release", "ip:read", "vm:snapshot:create", "vm:snapshot:delete"],
    "vm_operator": [
        "vm:create", "vm:read", "vm:update", "vm:delete",
        "vm:start", "vm:stop", "vm:restart", "vm:console",
        "vm:snapshot:create", "vm:snapshot:delete",
        "network:read", "firewall:read", "ip:reserve", "ip:release", "ip:read"
    ],
    "network_admin": NETWORK_PERMISSIONS + FIREWALL_PERMISSIONS + WIREGUARD_PERMISSIONS + ["vm:read", "network:create", "ip:reserve", "ip:release", "ip:read"],
    "viewer": [
        "vm:read", "vm:console", "network:read", "firewall:read", "ip:read",
        "wireguard:read",
        "tenant:read", "user:read"
    ],
}

SYSTEM_ROLES = ["super_admin"]
