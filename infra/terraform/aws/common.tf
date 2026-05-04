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
  inventory_master  = "master ansible_host=${var.master_ip} ansible_user=k8s ansible_ssh_private_key_file=./k8s.pem\nrouter ansible_host=${aws_instance.router_instance.public_ip} ansible_user=k8s ansible_ssh_private_key_file=./k8s.pem"
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

data "aws_availability_zones" "available" {
  state = "available"
}


resource "aws_key_pair" "k8s" {
  key_name   = "k8s-${var.customer_name}-key"
  public_key = tls_private_key.k8s_key.public_key_openssh
}

resource "aws_vpc" "main_vpc" {
  cidr_block = var.vpc_cidr
  tags = {
    Name = "olympus-${var.customer_name}-k8s_main_vpc"
  }
  enable_dns_support   = true
  enable_dns_hostnames = false
}


resource "aws_subnet" "k8s_main_subnet" {
  availability_zone = data.aws_availability_zones.available.names[0]
  vpc_id            = aws_vpc.main_vpc.id
  cidr_block        = var.subnet_cidr
  tags = {
    Name = "olympus-${var.customer_name}-k8s-subnet1"
  }
}

resource "aws_security_group" "k8s" {
  name        = "k8s-${var.customer_name}-sg"
  description = "k8s access"
  vpc_id      = aws_vpc.main_vpc.id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] # tighten later
  }

  ingress {
    description = "Kubernetes / Registry"
    from_port   = 5000
    to_port     = 5000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Allow ingress from private subnet"
    from_port   = 0
    to_port     = 0
    protocol    = "-1" # "-1" means all protocols
    cidr_blocks = ["10.0.0.0/8"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}
