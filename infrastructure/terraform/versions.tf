terraform {
  # 1.10+ for S3 backend state locking without DynamoDB (use_lockfile).
  required_version = ">= 1.10.0, < 2.0.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0.0, < 6.0.0"
    }
  }

  # D-TFSTATE: shared state in S3 with a lock file. Partial configuration:
  # the bucket, key and region come from a local backend.hcl (see
  # backend.hcl.example and infrastructure/aws/RUNBOOK.md). The state bucket
  # itself is created once by infrastructure/terraform/state-bucket/.
  backend "s3" {}
}

provider "aws" {
  region = var.aws_region
}
