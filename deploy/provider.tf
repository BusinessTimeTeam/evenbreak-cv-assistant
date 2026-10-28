terraform {
  required_version = ">= 1.9"

  required_providers {
    digitalocean = {
      source  = "digitalocean/digitalocean"
      version = "~> 2.106.0"
    }
    # Used by random_string.suffix (fresh-deployment resource names) and
    # random_password.history_signing_key.
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9"
    }
  }
}

provider "digitalocean" {
  token        = var.do_token
  api_endpoint = var._api_host
}
