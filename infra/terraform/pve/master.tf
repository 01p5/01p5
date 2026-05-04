resource "proxmox_virtual_environment_vm" "k8s_master_host" {
  name      = "k8s-${var.customer_name}-master"
  node_name = var.master_node
  vm_id = var.master_vmid

  lifecycle {
    ignore_changes = [
      initialization
    ]
  }

  initialization {
    user_data_file_id = proxmox_virtual_environment_file.cloud_init_master.id
    ip_config {
      ipv4 {
        address = "${var.master_ip}/${split("/", var.subnet_cidr)[1]}"
        gateway = var.master_gateway
      }
    }
  }

  disk {
    datastore_id = var.master_disk_vol
    file_id      =  local.image_id[var.master_node]
    interface    = "virtio0"
    iothread     = true
    discard      = "on"
    file_format  = "raw"
    size         = var.master_disk
  }
  cpu {
    cores   = var.master_cpu
    sockets = 1
    type = "host"
  }
  memory {
    dedicated = var.master_memory
  }

  network_device {
    bridge = var.master_nic
  }
}
