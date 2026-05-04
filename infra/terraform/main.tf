terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.28.0"
    }
    proxmox = {
      source = "bpg/proxmox"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
  }
}

# Pick exactly one cluster backend per environment.
#   "aws" → instantiates ./aws (VPC + EC2 + router + Cloudflare DNS)
#   "pve" → instantiates ./pve (Proxmox VMs + Cloudflare DNS)
# Both modules accept the same superset of variables (each module
# stubs out the other's). Switching providers is a `tfvars` change,
# not a code edit — and ``count = 0`` on the inactive module means
# Terraform skips evaluating its resources entirely.
variable "provider_target" {
  type        = string
  description = "Which cluster backend to instantiate: \"aws\" or \"pve\"."
  default     = "aws"
  validation {
    condition     = contains(["aws", "pve"], var.provider_target)
    error_message = "provider_target must be \"aws\" or \"pve\"."
  }
}

variable "aws_region" {
  type        = string
  description = "AWS region (only used when provider_target == \"aws\")."
  default     = "us-west-1"
}

variable "cloudflare_token" {
  type        = string
  description = "API token for Cloudflare"
}
variable "cloudflare_zone_id" {
  type        = string
  description = "Zone ID for cloudflare DNS"
}
variable "deployment_domain_prefix" {
  type        = string
  description = "Deployment domain prefix"
}
variable "customer_name" {
  type        = string
  description = "Customer name"
}
variable "customer_deployment_path" {
  type        = string
  description = "Path to customer deployment folder"
}

variable "pve_password" {
  default = ""
}

variable "pve_host" {
  default = ""
}

variable "pve_http_host" {
  default = ""
}

variable "pve_username" {
  default = ""
}

variable "pve_login_node" {
  default = ""
}

variable "pve_service_ip" {
  default = ""
}

# --- PVE-only knobs (overridable per environment via TF_VAR_*) ----
# Defaults match the original Olympus 10.81.1.0/24 layout. Override
# in env.sh for boxes whose vmbr0 lives on a different network or
# whose datastore/bridge names differ.
variable "pve_node_name" {
  type        = string
  description = "Name of the PVE node hosting all VMs."
  default     = "pve"
}

variable "pve_disk_vol" {
  type        = string
  description = "PVE datastore for VM disks (e.g. local-lvm, nvme1)."
  default     = "local-lvm"
}

variable "pve_bridge" {
  type        = string
  description = "Linux bridge to attach VMs to (e.g. vmbr0)."
  default     = "vmbr0"
}

variable "pve_subnet_cidr" {
  type        = string
  description = "CIDR of the network reachable on pve_bridge."
  default     = "10.81.1.0/24"
}

variable "pve_gateway" {
  type        = string
  description = "Default gateway on pve_bridge."
  default     = "10.81.1.1"
}

variable "pve_master_ip" {
  type        = string
  description = "IP for the k8s master VM on pve_bridge."
  default     = "10.81.1.10"
}

variable "pve_worker_ips" {
  type        = list(string)
  description = "IPs for the k8s worker VMs on pve_bridge (one per worker)."
  default     = ["10.81.1.11", "10.81.1.12", "10.81.1.13"]
}

# Provider blocks live at the root so the cluster_aws / cluster_pve
# modules can be instantiated with ``count``. Terraform does not
# actually open connections until a resource needs them; with
# ``count = 0`` on the unused module nothing is fetched.
provider "aws" {
  region = var.aws_region
}

provider "proxmox" {
  endpoint = var.pve_http_host != "" ? "https://${var.pve_http_host}:8006/" : ""
  username = var.pve_username
  password = var.pve_password
  insecure = true
  ssh {
    agent    = true
    username = var.pve_username != "" ? split("@", var.pve_username)[0] : ""
    node {
      name    = var.pve_login_node
      address = var.pve_host
    }
  }
}

provider "cloudflare" {
  # The cloudflare provider validates api_token's charset even when no
  # cloudflare_record is created — pass a placeholder when the user
  # hasn't set TF_VAR_cloudflare_token. Real records are gated on the
  # original var.cloudflare_token in pve/dns.tf and aws/dns.tf.
  api_token = var.cloudflare_token != "" ? var.cloudflare_token : "0000000000000000000000000000000000000000"
}

# AWS-side worker map. IPs/gateway here only exist inside the AWS VPC.
locals {
  workers_aws = {
    worker1 = {
      ami           = "ami-04f34746e5e1ec0fe"
      instance_type = "t3.medium"
      # Proxmox stub fields (the AWS module accepts them but ignores).
      node     = "pve"
      disk_vol = "local-lvm"
      nic      = "vmbr0"
      cpu      = 8
      memory   = 8192
      vmid     = 151
      # Shared
      disk    = 20
      ip      = "10.81.1.11"
      gateway = "10.81.1.1"
    }
    worker2 = {
      ami           = "ami-04f34746e5e1ec0fe"
      instance_type = "t3.medium"
      node          = "pve"
      disk_vol      = "local-lvm"
      nic           = "vmbr0"
      cpu           = 8
      memory        = 8192
      vmid          = 152
      disk          = 20
      ip            = "10.81.1.12"
      gateway       = "10.81.1.1"
    }
    worker3 = {
      ami           = "ami-04f34746e5e1ec0fe"
      instance_type = "t3.medium"
      node          = "pve"
      disk_vol      = "local-lvm"
      nic           = "vmbr0"
      cpu           = 8
      memory        = 8192
      vmid          = 153
      disk          = 20
      ip            = "10.81.1.13"
      gateway       = "10.81.1.1"
    }
  }
  # PVE worker map — derived from the env-overridable PVE knobs so
  # box-specific values stay in env.sh, not committed code.
  workers_pve = {
    for i, ip in var.pve_worker_ips :
    "worker${i + 1}" => {
      # AWS stub fields (the PVE module accepts them but ignores).
      ami           = ""
      instance_type = ""
      # Proxmox
      node     = var.pve_node_name
      disk_vol = var.pve_disk_vol
      nic      = var.pve_bridge
      cpu      = 8
      memory   = 8192
      vmid     = 151 + i
      # Shared
      disk    = 20
      ip      = ip
      gateway = var.pve_gateway
    }
  }
}

module "cluster_aws" {
  source = "./aws"
  count  = var.provider_target == "aws" ? 1 : 0

  cloudflare_token         = var.cloudflare_token
  cloudflare_zone_id       = var.cloudflare_zone_id
  deployment_domain_prefix = var.deployment_domain_prefix
  customer_name            = var.customer_name
  customer_deployment_path = var.customer_deployment_path

  // AWS config
  router_ip            = "10.81.1.5"
  master_ami           = "ami-04f34746e5e1ec0fe"
  master_instance_type = "c6a.xlarge"
  vpc_cidr             = "10.81.0.0/16"
  subnet_cidr          = "10.81.1.0/24"
  router_wan_cidr      = "10.81.99.0/24"
  router_wan_ip        = "10.81.99.5"

  // Proxmox stub (unused on AWS path; the module's variables.tf has
  // defaults so we still pass them for symmetry).
  master_node     = "pve"
  master_disk_vol = "nvme1"
  master_nic      = "k8s"
  master_gateway  = "10.81.2.1"
  master_cpu      = 16
  master_memory   = 8192
  master_vmid     = 150
  pve_password    = var.pve_password
  pve_host        = var.pve_host
  pve_http_host   = var.pve_http_host
  pve_username    = var.pve_username
  pve_login_node  = var.pve_login_node
  pve_service_ip  = var.pve_service_ip

  // Shared
  master_disk = 40
  master_ip   = "10.81.2.10"
  master_cidr = "10.81.2.0/24"

  workers = local.workers_aws
}

module "cluster_pve" {
  source = "./pve"
  count  = var.provider_target == "pve" ? 1 : 0

  cloudflare_token         = var.cloudflare_token
  cloudflare_zone_id       = var.cloudflare_zone_id
  deployment_domain_prefix = var.deployment_domain_prefix
  customer_name            = var.customer_name
  customer_deployment_path = var.customer_deployment_path

  // Proxmox config (env-overridable — see TF_VAR_pve_* in env.sh).
  master_node     = var.pve_node_name
  master_disk_vol = var.pve_disk_vol
  master_nic      = var.pve_bridge
  master_gateway  = var.pve_gateway
  master_cpu      = 16
  master_memory   = 8192
  master_vmid     = 150
  pve_password    = var.pve_password
  pve_host        = var.pve_host
  pve_http_host   = var.pve_http_host
  pve_username    = var.pve_username
  pve_login_node  = var.pve_login_node
  pve_service_ip  = var.pve_service_ip

  // Shared
  master_disk = 40
  master_ip   = var.pve_master_ip
  subnet_cidr = var.pve_subnet_cidr

  workers = local.workers_pve
}
