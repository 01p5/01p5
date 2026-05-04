resource "aws_instance" "k8s_worker_host" {
  availability_zone = data.aws_availability_zones.available.names[0]
  for_each          = var.workers
  ami               = each.value.ami
  instance_type     = each.value.instance_type
  subnet_id         = aws_subnet.k8s_main_subnet.id

  key_name               = aws_key_pair.k8s.key_name
  vpc_security_group_ids = [aws_security_group.k8s.id]

  associate_public_ip_address = false

  private_ip = each.value.ip

  root_block_device {
    volume_size           = each.value.disk
    volume_type           = "gp3"
    delete_on_termination = true
  }

  user_data = <<-EOF
    #cloud-config
    hostname: k8s-${each.key}
    fqdn: k8s-${each.key}.local
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
    Name = "k8s-${var.customer_name}-worker-${each.key}"
  }
}
