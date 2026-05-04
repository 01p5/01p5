# One-time bootstrap for the Olympus AWS sandbox. Creates:
#   - S3 bucket for Terraform remote state
#   - DynamoDB table for state locking (S3-native locking is preferred
#     in TF 1.10+, but DynamoDB still works for older state backends)
#   - IAM role + policy the Terraform agent assumes when it runs
#
# Apply this manually with a privileged identity once per AWS account.
# After that, the Olympus Terraform agent assumes ``olympus_terraform``
# and operates with the scoped policy below.

terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.region
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "state_bucket_name" {
  type        = string
  description = "Globally unique name for the Terraform state bucket."
}

resource "aws_s3_bucket" "state" {
  bucket = var.state_bucket_name
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_dynamodb_table" "lock" {
  name         = "olympus-tf-locks"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"
  attribute {
    name = "LockID"
    type = "S"
  }
}

# IAM identity for the Terraform agent.
resource "aws_iam_role" "terraform_agent" {
  name = "olympus_terraform"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Scoped policy: state access + the resources the W3-4 sandbox-bucket
# stack actually creates. Expand only when a new stack genuinely needs
# more — drift toward least privilege, not the other way.
resource "aws_iam_role_policy" "terraform_agent" {
  role = aws_iam_role.terraform_agent.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "TerraformState"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.state.arn,
          "${aws_s3_bucket.state.arn}/*",
        ]
      },
      {
        Sid      = "TerraformLocks"
        Effect   = "Allow"
        Action   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:DeleteItem"]
        Resource = aws_dynamodb_table.lock.arn
      },
      {
        Sid    = "SandboxBucketLifecycle"
        Effect = "Allow"
        Action = [
          "s3:CreateBucket",
          "s3:DeleteBucket",
          "s3:GetBucket*",
          "s3:ListBucket",
          "s3:PutBucket*",
          "s3:GetLifecycleConfiguration",
          "s3:PutLifecycleConfiguration",
        ]
        Resource = "arn:aws:s3:::olympus-sandbox-*"
      },
    ]
  })
}

output "state_bucket" { value = aws_s3_bucket.state.bucket }
output "lock_table" { value = aws_dynamodb_table.lock.name }
output "agent_role" { value = aws_iam_role.terraform_agent.arn }
