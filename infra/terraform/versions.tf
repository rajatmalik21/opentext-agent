# UNVALIDATED: terraform/tflint/trivy were not available when this was written.
# First real check: CI job `terraform` (fmt, init -backend=false, validate).
terraform {
  required_version = ">= 1.6"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 5.14"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
  # Remote state: copy backend.example.hcl to backend.hcl (git-ignored) and run
  #   terraform init -backend-config=backend.hcl
  backend "azurerm" {}
}

provider "azurerm" {
  features {}
  # Auth: `az login` locally, or OIDC in CI (ARM_USE_OIDC=true). No keys in code.
  subscription_id = var.subscription_id
}
