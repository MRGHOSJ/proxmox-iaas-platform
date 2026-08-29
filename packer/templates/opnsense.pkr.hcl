packer {
  required_plugins {
    proxmox = {
      version = ">= 1.1.3"
      source  = "github.com/hashicorp/proxmox"
    }
  }
}

variable "proxmox_url"      { type = string }
variable "proxmox_username" { type = string }
variable "proxmox_node"     { type = string }

variable "proxmox_token"    { 
  type = string 
  sensitive = true 
}

variable "root_password"    { 
  type = string 
  sensitive = true 
  default = "opnsense" 
}
variable "opnsense_version" { 
  type = string
  default = "26.1.2" 
}

source "proxmox-iso" "opnsense" {
  proxmox_url              = var.proxmox_url
  username                 = var.proxmox_username
  token                    = var.proxmox_token
  node                     = var.proxmox_node
  insecure_skip_tls_verify = true

  vm_id                = 9000
  vm_name              = "opnsense-build"
  template_name        = "opnsense"
  template_description = "OPNsense ${var.opnsense_version} — Packer golden image"

  cpu_type   = "kvm64"
  cores      = 2
  memory     = 1024
  os         = "other"
  qemu_agent = false

  disks {
    disk_size    = "8G"
    storage_pool = "local-lvm"
    type         = "scsi"
    format       = "raw"
    io_thread    = false
  }

  network_adapters {
    bridge = "vmbr0"
    model  = "virtio"
  }

  network_adapters {
    bridge = "vmbr0"
    model  = "virtio"
  }

  # Main installer DVD
  boot_iso {
    iso_file         = "local:iso/OPNsense-${var.opnsense_version}-dvd-amd64.iso"
    iso_storage_pool = "local"
    unmount          = true
  }

  # Seed ISO — OPNsense auto-imports this on boot, skips interface wizard
  additional_iso_files {
    iso_file         = "local:iso/seed.iso"
    iso_storage_pool = "local"
    unmount          = true
    device           = "ide3"
  }

  communicator = "none"
  boot_wait    = "10s"

  boot_command = [
    # GRUB: boot installer DVD
    "<enter><wait5>",

    # Config importer finds seed.iso automatically — just wait for it
    "<wait20>",

    # Blank enter to exit device selection prompt
    "<enter><wait10>",

    # Interface wizard is now SKIPPED (config.xml handled it)
    # Login prompt appears directly
    "installer<enter><wait3>",
    "opnsense<enter><wait20>",

    # Step 1: Keymap
    "<enter><wait3>",

    # Step 2: Install UFS
    "<enter><wait3>",

    # Step 3: Disk (da0)
    "<enter><wait3>",

    # Step 4: Partition (GPT)
    "<enter><wait3>",

    # Step 5: Last Chance
    "<enter><wait3>",

    # Step 6: Swap
    "<enter><wait5>",

    # Wait for disk write
    "<wait300>",

    # Step 7: Root password
    "${var.root_password}<enter><wait3>",
    "${var.root_password}<enter><wait5>",

    # Step 8: Complete Install — reboots into finished system
    "<enter><wait120>",

    "<wait30>"
  ]
}

build {
  sources = ["source.proxmox-iso.opnsense"]
}