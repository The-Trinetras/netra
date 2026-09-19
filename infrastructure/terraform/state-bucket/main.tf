# One-time bootstrap for D-TFSTATE: the private, versioned, encrypted bucket
# that holds the main configuration's Terraform state (with S3 lock files).
# This small configuration keeps its own local state; it is applied once by an
# operator and rarely changes. See infrastructure/aws/RUNBOOK.md.

terraform {
  required_version = ">= 1.10.0, < 2.0.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0.0, < 6.0.0"
    }
  }
}

variable "aws_region" {
  type = string
}

variable "project" {
  type    = string
  default = "netra"
}

provider "aws" {
  region = var.aws_region
}

resource "aws_s3_bucket" "state" {
  bucket_prefix = "${var.project}-terraform-state-"
  tags          = { Project = var.project, ManagedBy = "terraform", Purpose = "terraform-state" }

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id
  rule {
    object_ownership = "BucketOwnerEnforced"
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

# Versioning keeps every earlier state, so a bad apply can be rolled back.
resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id
  versioning_configuration {
    status = "Enabled"
  }
}

output "state_bucket" { value = aws_s3_bucket.state.bucket }
