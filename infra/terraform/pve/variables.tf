variable "master_node" {
  type        = string
  description = "Node to deploy"
}

variable "master_disk_vol" {
  type        = string
  description = "Disk to deploy"
}

variable "master_nic" {
  type        = string
  description = "Bridge to use for the network interface"
}

variable "master_ip" {
  type        = string
  description = "IP address for the master node (cidr block)"
}

variable "master_gateway" {
  type        = string
  description = "Gateway for the master node"
}

variable "master_cpu" {
  type        = number
  description = "CPU for the master node"
}

variable "master_memory" {
  type        = number
  description = "Memory for the master node"
}

variable "master_vmid" {
  type        = number
  description = "VM id"
}

variable "master_disk" {
  type        = number
  description = "Disk size for the master node"
}

variable "subnet_cidr" {
  type = string
}


// AWS stub
variable "master_cidr" {
  default = ""
}

variable "router_ip" {
  default = ""
}

variable "master_ami" {
  default = ""
}

variable "master_instance_type" {
  default = ""
}

variable "vpc_cidr" {
  default = ""
}

variable "router_wan_cidr" {
  default = ""
}

variable "router_wan_ip" {
  default = ""
}
// End AWS stub

variable "pve_password" {
}

variable "pve_host" {
}

variable "pve_http_host" {
}

variable "pve_username" {
}

variable "pve_login_node" {
}

variable "pve_service_ip" {
}

variable "customer_name" {
  type        = string
  description = "Customer name"
}
variable "customer_deployment_path" {
  type        = string
  description = "Path to customer deployment folder"
}

variable "workers" {
  default = {}
  type = map(object({
    ip       = string
    node     = string
    disk_vol = string
    nic      = string
    cpu      = number
    memory   = number
    vmid     = number
    gateway  = string
    disk     = number
    // AWS stub
    ami           = string
    instance_type = string
  }))
}
