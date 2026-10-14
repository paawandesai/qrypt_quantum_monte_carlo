terraform {
  required_version = ">= 1.10.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.69"
    }
  }

  # GitLab-managed Terraform state. Address and credentials come from
  # TF_HTTP_* environment variables (see scripts/tf-init.sh); nothing here is secret.
  backend "http" {}
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "qrypt-mc"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
