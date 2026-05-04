resource "aws_internet_gateway" "internet_gateway" {
  vpc_id = aws_vpc.main_vpc.id
  tags = {
    Name = "artemis-${var.customer_name}-k8s-internet-gateway"
  }
}

resource "aws_eip" "router_wan_eip" {
  tags = {
    Name = "artemis-${var.customer_name}-router-wan-eip"
  }
}

resource "aws_eip_association" "router_wan_assoc" {
  allocation_id        = aws_eip.router_wan_eip.id
  network_interface_id = aws_network_interface.router_wan.id
}

resource "aws_subnet" "router_vpn_subnet" {
  availability_zone = data.aws_availability_zones.available.names[0]
  vpc_id            = aws_vpc.main_vpc.id
  cidr_block        = "10.81.81.0/24"
  tags = {
    Name = "artemis-${var.customer_name}-k8s-router-vpn-subnet"
  }
}

resource "aws_subnet" "router_wan_subnet" {
  availability_zone = data.aws_availability_zones.available.names[0]
  vpc_id            = aws_vpc.main_vpc.id
  cidr_block        = var.router_wan_cidr
  tags = {
    Name = "artemis-${var.customer_name}-k8s-router-wan-subnet"
  }
}

resource "aws_route_table" "router-wan-subnet-rt" {
  vpc_id = aws_vpc.main_vpc.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.internet_gateway.id
  }
  tags = {
    Name = "artemis-${var.customer_name}-k8s-router-wan-subnet-rt"
  }
}

resource "aws_route_table_association" "router-subnet-rt-association" {
  route_table_id = aws_route_table.router-wan-subnet-rt.id
  subnet_id      = aws_subnet.router_wan_subnet.id
}

resource "aws_route_table" "router-lan-subnet-rt" {
  vpc_id = aws_vpc.main_vpc.id
  route {
    cidr_block           = "0.0.0.0/0"
    network_interface_id = aws_network_interface.router_lan.id
  }
  route {
    // Setup VPN route
    cidr_block           = "10.81.81.0/24"
    network_interface_id = aws_network_interface.router_lan.id
  }
  tags = {
    Name = "artemis-${var.customer_name}-k8s-router-lan-subnet-rt"
  }
}

resource "aws_route_table_association" "router-lan-subnet-rt-association" {
  route_table_id = aws_route_table.router-lan-subnet-rt.id
  subnet_id      = aws_subnet.k8s_main_subnet.id
}

resource "aws_security_group" "router_wan_sg" {
  vpc_id = aws_vpc.main_vpc.id
  ingress {
    description = "Allow ingress from private subnet"
    from_port   = 0
    to_port     = 0
    protocol    = "-1" # "-1" means all protocols
    cidr_blocks = ["10.0.0.0/8"]
  }

  ingress {
    description = "Allow ingress for ssh"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Allow ingress for wg"
    from_port   = 51820
    to_port     = 51820
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Allow ingress for guacamole"
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  # Outward Network Traffic for the instance
  egress {
    description = "Allow all egress traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = {
    Name = "artemis-${var.customer_name}-k8s-router-sg"
  }
}

resource "aws_security_group" "router_lan_sg" {
  vpc_id = aws_vpc.main_vpc.id
  ingress {
    description = "Allow all ingress traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Allow all egress traffic"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_network_interface" "router_wan" {
  subnet_id         = aws_subnet.router_wan_subnet.id
  private_ips       = [var.router_wan_ip]
  security_groups   = [aws_security_group.router_wan_sg.id]
  source_dest_check = false
}

resource "aws_network_interface" "router_lan" {
  subnet_id         = aws_subnet.k8s_main_subnet.id
  private_ips       = [var.router_ip]
  security_groups   = [aws_security_group.router_lan_sg.id]
  source_dest_check = false
}

resource "aws_instance" "router_instance" {
  availability_zone = data.aws_availability_zones.available.names[0]
  ami               = "ami-04f34746e5e1ec0fe" # pinned ubuntu version 22.04
  # ami                         = "ami-0da657e96a9bfab37" # esperanza router AMI (built with Packer)
  instance_type = "t3.small"
  key_name      = aws_key_pair.k8s.key_name

  root_block_device {
    volume_size = 20 # changed from 8GB to 20GB for docker images
  }

  network_interface {
    network_interface_id = aws_network_interface.router_wan.id
    device_index         = 0
  }

  network_interface {
    network_interface_id = aws_network_interface.router_lan.id
    device_index         = 1
  }

  tags = {
    Name = "artemis-${var.customer_name}-k8s-router"
  }
  user_data = <<-EOF
#cloud-config

users:
  - name: k8s
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    ssh-authorized-keys:
      - ${tls_private_key.k8s_key.public_key_openssh}

package_update: true
package_upgrade: true
packages:
  - iptables-persistent

write_files:
  # Enable IPv4 forwarding permanently
  - path: /etc/sysctl.d/99-router.conf
    permissions: '0644'
    content: |
      net.ipv4.ip_forward=1

runcmd:
  # Apply sysctl immediately
  - sysctl --system

  # Define interfaces (adjust if names differ)
  - WAN_IF=ens5
  - LAN_IF=ens6

  # Flush existing rules (idempotent)
  - iptables -F
  - iptables -t nat -F

  # NAT: LAN -> WAN
  - iptables -t nat -A POSTROUTING -o $WAN_IF -j MASQUERADE

  # Forwarding rules
  - iptables -A FORWARD -i $LAN_IF -o $WAN_IF -j ACCEPT
  - iptables -A FORWARD -i $WAN_IF -o $LAN_IF -m state --state RELATED,ESTABLISHED -j ACCEPT

  # Save rules so they persist across reboot
  - netfilter-persistent save
  - netfilter-persistent reload
EOF
}
