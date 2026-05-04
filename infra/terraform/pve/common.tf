terraform {
  required_providers {
    proxmox = {
      source = "bpg/proxmox"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
  }
}

provider "proxmox" {
  endpoint = "https://${var.pve_http_host}:8006/"
  username = var.pve_username
  password = var.pve_password
  insecure = true
  ssh {
    agent    = true
    username = split("@", var.pve_username)[0]
    node {
      name    = var.pve_login_node
      address = var.pve_host
    }
  }
}

resource "tls_private_key" "k8s_key" {
  algorithm = "ED25519"
}

resource "local_file" "k8s_key" {
  content         = tls_private_key.k8s_key.private_key_openssh
  filename        = "${var.customer_deployment_path}/deployment/k8s.pem"
  file_permission = 0600
}

# Inventory generation
locals {
  inventory_workers = join("\n", [for key, value in var.workers : "${key} ansible_host=${split("/", value.ip)[0]} ansible_user=k8s ansible_ssh_private_key_file=./k8s.pem"])
  inventory_master  = "master ansible_host=${split("/", var.master_ip)[0]} ansible_user=k8s ansible_ssh_private_key_file=./k8s.pem"
  inventory_content = "[public]\n${local.inventory_master}\n\n[workers]\n${local.inventory_workers}"
}

resource "local_file" "inventory" {
  content  = local.inventory_content
  filename = "${var.customer_deployment_path}/deployment/inventory.ini"
}

# Connection script generation
resource "local_file" "connection_script" {
  content  = <<EOF
  #!/bin/bash
  ssh -i ./k8s.pem k8s@${split("/", var.master_ip)[0]}
  EOF
  filename = "${var.customer_deployment_path}/deployment/connect.sh"
}