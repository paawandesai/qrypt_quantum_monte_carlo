# One-time bootstrap, applied by an administrator: the GitLab OIDC identity
# provider and the role GitLab CI assumes for plan/apply. No long-lived AWS keys
# are stored in GitLab. Kept separate from the app stack so CI cannot modify
# its own trust policy.

terraform {
  required_version = ">= 1.10.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.69"
    }
  }
  backend "http" {}
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "qrypt-mc", ManagedBy = "terraform", Stack = "bootstrap" }
  }
}
