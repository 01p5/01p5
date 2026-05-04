terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "6.28.0"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4.0"
    }
  }
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

provider "aws" {
  region = "us-west-1"
}


data "aws_availability_zones" "available" {
  state = "available"
}


module "cluster" {
  source                   = "./aws"
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

  // Proxmox config
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

  // Shared config
  master_disk = 40
  master_ip   = "10.81.2.10"
  master_cidr = "10.81.2.0/24"


  workers = {
    worker1 = {
      // AWS config
      ami           = "ami-04f34746e5e1ec0fe"
      instance_type = "t3.medium"
      // Proxmox config
      node     = "pve"
      disk_vol = "nvme1"
      nic      = "k8s"
      cpu      = 8
      memory   = 8192
      vmid     = 151

      // Shared config
      disk    = 20
      ip      = "10.81.1.11"
      gateway = "10.81.1.1"
    }
    worker2 = {
      // AWS config
      ami           = "ami-04f34746e5e1ec0fe"
      instance_type = "t3.medium"
      // Proxmox config
      node     = "pve"
      disk_vol = "nvme1"
      nic      = "k8s"
      cpu      = 8
      memory   = 8192
      vmid     = 152

      // Shared config
      disk    = 20
      ip      = "10.81.1.12"
      gateway = "10.81.1.1"
    }
    worker3 = {
      // AWS config
      ami           = "ami-04f34746e5e1ec0fe"
      instance_type = "t3.medium"
      // Proxmox config
      node     = "pve"
      disk_vol = "nvme1"
      nic      = "k8s"
      cpu      = 8
      memory   = 8192
      vmid     = 153

      // Shared config
      disk    = 20
      ip      = "10.81.1.13"
      gateway = "10.81.1.1"
    }
  }
}
