"""
Worker modules for Proxmox CloudOrchestrator.

Contains provider-specific utilities and configuration modules:
- opnsense_config_invm: OPNsense configuration management via in-VM PHP scripts
"""

from app.workers.modules.opnsense_config_invm import OPNsenseConfigInVM

__all__ = ["OPNsenseConfigInVM"]