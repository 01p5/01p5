resource "aws_subnet" "k8s_master_subnet" {
  availability_zone = data.aws_availability_zones.available.names[0]
  vpc_id            = aws_vpc.main_vpc.id
  cidr_block        = var.master_cidr
  tags = {
    Name = "olympus-${var.customer_name}-k8s-master-subnet"
  }
}

resource "aws_security_group" "k8s_master_sg" {
  name        = "k8s-${var.customer_name}-master-sg"
  description = "k8s master access"
  vpc_id      = aws_vpc.main_vpc.id

  ingress {
    description = "Allow ingress from private subnet"
    from_port   = 0
    to_port     = 0
    protocol    = "-1" # "-1" means all protocols
    cidr_blocks = ["10.0.0.0/8"]
  }

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] # tighten later
  }

  ingress {
    description = "allow port 80 and 443 traffic"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "allow port 80 and 443 traffic"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_route_table" "master-subnet-rt" {
  vpc_id = aws_vpc.main_vpc.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.internet_gateway.id
  }
  route {
    // Setup VPN route
    cidr_block           = "10.81.81.0/24"
    network_interface_id = aws_network_interface.router_lan.id
  }
  tags = {
    Name = "olympus-${var.customer_name}-k8s-master-subnet-rt"
  }
}

resource "aws_route_table_association" "master-subnet-rt-association" {
  route_table_id = aws_route_table.master-subnet-rt.id
  subnet_id      = aws_subnet.k8s_master_subnet.id
}

resource "aws_instance" "k8s_master_host" {
  availability_zone = data.aws_availability_zones.available.names[0]
  ami               = var.master_ami
  instance_type     = var.master_instance_type
  subnet_id         = aws_subnet.k8s_master_subnet.id

  key_name               = aws_key_pair.k8s.key_name
  vpc_security_group_ids = [aws_security_group.k8s_master_sg.id]

  associate_public_ip_address = true
  private_ip                  = var.master_ip

  root_block_device {
    volume_size           = var.master_disk
    volume_type           = "gp3"
    delete_on_termination = true
  }

  user_data = <<-EOF
    #cloud-config
    hostname: k8s-master
    fqdn: k8s-master.local
    users:
      - name: k8s
        sudo: ALL=(ALL) NOPASSWD:ALL
        shell: /bin/bash
        ssh-authorized-keys:
          - ${tls_private_key.k8s_key.public_key_openssh}

    package_update: true
    package_upgrade: true
  EOF

  tags = {
    Name = "k8s-${var.customer_name}-master"
  }
}
