variable "master_disk" {
  type        = number
  description = "Disk size for the master node (in GB)"
}

variable "master_cidr" {
  type        = string
  description = "IP address for the master node (cidr block)"
}

variable "master_ip" {
  type        = string
  description = "IP address for the master node (cidr block)"
}

variable "master_ami" {
  type        = string
  description = "AMI for the master node"
}

variable "master_instance_type" {
  type        = string
  description = "Instance type for the master node"
}

variable "vpc_cidr" {
  type        = string
  description = "CIDR block for the VPC"
}

variable "subnet_cidr" {
  type        = string
  description = "CIDR block for the subnet"
}

variable "router_ip" {
  type        = string
  description = "LAN ip of router for connecting the cluster to internet"
}

variable "router_wan_cidr" {
  type        = string
  description = "Router public CIDR"
}

variable "router_wan_ip" {
  type        = string
  description = "WAN port IP of router"
}

variable "customer_name" {
  type        = string
  description = "Customer name"
}
variable "customer_deployment_path" {
  type        = string
  description = "Path to customer deployment folder"
}

// Stub for proxmox
variable "master_node" {
  default = ""
}

variable "master_disk_vol" {
  default = ""
}
variable "master_nic" {
  default = ""
}

variable "master_gateway" {
  default = ""
}
variable "master_cpu" {
  default = ""
}

variable "master_memory" {
  default = ""
}
variable "master_vmid" {
  default = ""
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

variable "workers" {
  default = {}
  type = map(object({
    disk          = number
    ip            = string
    ami           = string
    instance_type = string
    // Proxmox stub
    node     = string
    disk_vol = string
    nic      = string
    cpu      = number
    memory   = number
    vmid     = number
    gateway  = string
  }))
}
