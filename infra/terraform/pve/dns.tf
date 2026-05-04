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

provider "cloudflare" {
  api_token = var.cloudflare_token
}

resource "cloudflare_record" "staging_master_public" {
  zone_id = var.cloudflare_zone_id
  name    = var.deployment_domain_prefix
  content = var.pve_service_ip
  type    = "A"
  proxied = false
}

resource "cloudflare_record" "staging_master_telemetry" {
  zone_id = var.cloudflare_zone_id
  name    = "telemetry.internal.${var.deployment_domain_prefix}"
  content = var.pve_service_ip
  type    = "A"
  proxied = false
}
