terraform {
  required_version = ">= 1.9"

  required_providers {
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "~> 2.85.0"
    }
    # Used by null_resource.agent_post_setup / kb_reindex glue.
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
