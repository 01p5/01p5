resource "proxmox_virtual_environment_vm" "k8s_workers" {
  for_each  = var.workers
  name      = "k8s-${var.customer_name}-worker-${each.key}"
  node_name = each.value.node
  vm_id     = each.value.vmid

  lifecycle {
    ignore_changes = [
      initialization
    ]
  }

  initialization {
    user_data_file_id = proxmox_virtual_environment_file.cloud_init_worker[each.key].id
    ip_config {
      ipv4 {
        address = "${each.value.ip}/${split("/", var.subnet_cidr)[1]}"
        gateway = each.value.gateway
      }
    }
  }

  disk {
    datastore_id = each.value.disk_vol
    file_id      = local.image_id[each.value.node]
    interface    = "virtio0"
    iothread     = true
    discard      = "on"
    file_format  = "raw"
    size         = each.value.disk
  }
  cpu {
    cores   = each.value.cpu
    sockets = 1
    type    = "host"
  }
  memory {
    dedicated = each.value.memory
  }

  network_device {
    bridge = each.value.nic
  }
}
