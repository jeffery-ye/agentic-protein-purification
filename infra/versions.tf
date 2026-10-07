terraform {
  required_version = ">= 1.11"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }

  # The bucket name includes the account ID, so it is passed at init:
  #   terraform init -backend-config="bucket=ppr-tfstate-<account-id>"
  backend "s3" {
    key          = "ppr/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
    profile      = "ppr-deploy"
  }
}

provider "aws" {
  region  = var.region
  profile = "ppr-deploy"

  # The ppr-deploy policy only lets Terraform change or delete EC2 resources
  # tagged Project=ppr, so every resource carries it.
  default_tags {
    tags = {
      Project = "ppr"
    }
  }
}
