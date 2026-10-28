terraform {
  required_version = ">= 1.9"

  required_providers {
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "~> 2.106.0"
    }
    # No longer used by any resource; kept for one apply so Terraform can
    # destroy the old null_resource.agent_guardrails / kb_reindex in state.
    null = {
      source  = "hashicorp/null"
      version = "~> 3.3"
    }
    # Used by random_string.suffix for fresh-deployment resource names.
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
  }
}

provider "digitalocean" {
  token        = var.do_token
  api_endpoint = var._api_host

  # S3-compatible credentials for managing the knowledge base documents bucket.
  spaces_access_id  = var.spaces_access_id
  spaces_secret_key = var.spaces_secret_key
}
