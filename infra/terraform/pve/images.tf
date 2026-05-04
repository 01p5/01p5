locals {
  nodes     = toset(concat([var.master_node], [for wk in var.workers : wk.node]))
  nodes_map = { for n in local.nodes : n => n }
  # Tie the cloud-image to the download resource so Terraform manages
  # the file on each node. Idempotent — Proxmox skips the download
  # once the file is present, so this does not re-fetch on every plan.
  image_id = { for n in local.nodes : n => proxmox_virtual_environment_download_file.ubuntu_22[n].id }
}

resource "proxmox_virtual_environment_file" "cloud_init_master" {
  content_type = "snippets"
  datastore_id = "local"
  node_name    = var.master_node

  source_raw {
    data      = <<-EOF
      #cloud-config
      hostname: k8s-master
      fqdn: k8s-master.local
      users:
        - name: k8s
          shell: /bin/bash
          sudo: ALL=(ALL) NOPASSWD:ALL
          ssh_authorized_keys:
            - ${tls_private_key.k8s_key.public_key_openssh}
    EOF
    file_name = "ci-k8s-${var.customer_name}-master.yml"
  }
}

resource "proxmox_virtual_environment_file" "cloud_init_worker" {
  for_each     = var.workers
  content_type = "snippets"
  datastore_id = "local"
  node_name    = each.value.node

  source_raw {
    data      = <<-EOF
      #cloud-config
      hostname: k8s-worker-${each.key}
      fqdn: k8s-worker-${each.key}.local
      users:
        - name: k8s
          shell: /bin/bash
          sudo: ALL=(ALL) NOPASSWD:ALL
          ssh_authorized_keys:
            - ${tls_private_key.k8s_key.public_key_openssh}
    EOF
    file_name = "ci-k8s-${var.customer_name}-worker-${each.key}.yml"
  }
}

resource "proxmox_virtual_environment_download_file" "ubuntu_22" {
  for_each     = local.nodes_map
  content_type = "iso"
  datastore_id = "local"
  node_name    = each.key
  file_name    = "ubuntu-22.04-k8s.img"
  url          = "https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img"
}

