
terraform {
  required_providers {

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

# Provider config hoisted to root main.tf — see comment in common.tf.

# DNS records gated on cloudflare_token presence (parallel to the
# pve/dns.tf gating). Intranet/no-DNS deployments skip these.
locals {
  cloudflare_enabled = var.cloudflare_token != "" ? 1 : 0
}

resource "cloudflare_record" "staging_master_public" {
  count   = local.cloudflare_enabled
  zone_id = var.cloudflare_zone_id
  name    = var.deployment_domain_prefix
  content = aws_instance.k8s_master_host.public_ip
  type    = "A"
  proxied = false
}

resource "cloudflare_record" "staging_master_telemetry" {
  count   = local.cloudflare_enabled
  zone_id = var.cloudflare_zone_id
  name    = "telemetry.internal.${var.deployment_domain_prefix}"
  content = aws_instance.k8s_master_host.public_ip
  type    = "A"
  proxied = false
}

resource "cloudflare_record" "staging_master_private" {
  count   = local.cloudflare_enabled
  zone_id = var.cloudflare_zone_id
  name    = "master.k8s.${var.deployment_domain_prefix}"
  content = aws_instance.k8s_master_host.private_ip
  type    = "A"
  proxied = false
}
