"""
SSH key generation utilities.

Generates ED25519 key pairs for VM access. The private key is stored
encrypted in the database; the public key is injected into the VM
via cloud-init.
"""
import logging
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

logger = logging.getLogger(__name__)


def generate_ssh_keypair(comment: str = "") -> tuple[str, str]:
    """
    Generate an ED25519 SSH key pair.

    Returns:
        (public_key_openssh, private_key_pem) as strings.
        The private key is unencrypted (no passphrase).
    """
    private_key = Ed25519PrivateKey.generate()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")

    public_key = private_key.public_key()
    public_openssh = public_key.public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("ascii")

    if comment:
        public_openssh = f"{public_openssh} {comment}"

    return public_openssh, private_pem
