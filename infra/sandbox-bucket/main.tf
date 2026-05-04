# Smallest meaningful Terraform target for the W3-4 "minimal AWS deploy
# path" plan item. The point is not the bucket — it's to surface
# IAM/state/secrets pain by having the Terraform agent apply *something*
# against real AWS, end-to-end.
#
# Resources here are deliberately cheap, deletable, and have no
# blast radius beyond a single sandbox account.

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Remote state in S3 + DynamoDB locking. Bootstrapped by
  # ../aws-bootstrap. Override via env vars or -backend-config when
  # running in CI.
  backend "s3" {
    key          = "olympus/sandbox-bucket/terraform.tfstate"
    region       = "us-east-1"
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type        = string
  default     = "us-east-1"
  description = "AWS region for the sandbox bucket."
}

variable "name_suffix" {
  type        = string
  description = "Random suffix appended to the bucket name to keep it globally unique."
}

resource "aws_s3_bucket" "sandbox" {
  bucket = "olympus-sandbox-${var.name_suffix}"

  tags = {
    Project = "olympus"
    Owner   = "olympus-terraform-agent"
    Purpose = "W3-4 minimal AWS deploy smoke target"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "expire" {
  bucket = aws_s3_bucket.sandbox.id

  rule {
    id     = "expire-soon"
    status = "Enabled"

    filter {}

    expiration {
      days = 7
    }
  }
}

output "bucket_name" {
  value = aws_s3_bucket.sandbox.bucket
}

output "bucket_arn" {
  value = aws_s3_bucket.sandbox.arn
}
