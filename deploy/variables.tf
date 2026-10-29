// =============================================================================
// API CONFIGURATION
// =============================================================================

variable "do_token" {
  type        = string
  description = "DigitalOcean API token"
  sensitive   = true
}

variable "_api_host" {
  type        = string
  default     = "https://api.digitalocean.com"
  description = "DigitalOcean API endpoint (internal use)"
}


// =============================================================================
// PROJECT CONFIGURATION
// =============================================================================

variable "project_uuid" {
  type        = string
  default     = ""
  description = "Existing project UUID (leave empty to create new project)"
}

variable "basename" {
  type        = string
  default     = "cv-assistant"
  description = "The base name used to auto-generate resource names."
}

variable "name_suffix" {
  type        = string
  default     = ""
  description = "Fixed suffix for generated resource names. Leave empty for a fresh deployment (a random suffix is generated); set to the existing suffix when importing a deployment so names keep matching."
}

variable "project_name" {
  type        = string
  default     = ""
  description = "Display name for the DO project. Defaults to basename if empty."
}

variable "region" {
  type        = string
  default     = "nyc3"
  description = "DigitalOcean region for all resources."
}

// =============================================================================
// APP PLATFORM CONFIGURATION
// =============================================================================

variable "app_instance_size" {
  type        = string
  default     = "apps-s-1vcpu-1gb"
  description = "App Platform instance size slug for the chat UI."
}

variable "_app_source_repo" {
  type        = string
  default     = "BusinessTimeTeam/evenbreak-cv-assistant"
  description = "GitHub repo (owner/name) hosting the chat-ui app source. Deployed via the DigitalOcean GitHub integration, which must be authorized on this repo."
}

variable "_app_source_branch" {
  type        = string
  default     = "main"
  description = "Git branch for the app source code."
}

variable "model_access_key" {
  type        = string
  sensitive   = true
  description = "DO model access key the chat UI uses for serverless inference (embedding the knowledge base at build time, and chat at run time), on App Platform and the Dokku droplet. Create one in the DO console (Inference > Model Access Keys) scoped to Qwen3 Embedding 0.6B and the model-trial chat models (gpt-oss-20b, DeepSeek V4 Flash, Qwen 3.5 397B, DeepSeek V4 Pro, GLM-5.3, Kimi K3; see chat-ui/trial.py), and supply via TF_VAR_model_access_key."
}

// =============================================================================
// DOKKU DROPLET CONFIGURATION
// =============================================================================

variable "domain" {
  type        = string
  default     = "evenbreak-cv.businesstime.team"
  description = "Public domain the chat UI is served on from the Dokku droplet. Its DNS is not on DigitalOcean: add an A record pointing at the reserved_ip output by hand, once."
}

variable "letsencrypt_email" {
  type        = string
  description = "Contact email Let's Encrypt uses for certificate expiry and account notices. No default: set it in terraform.tfvars before the first apply."
}

variable "admin_ssh_public_keys" {
  type        = list(string)
  description = "OpenSSH public keys (one per entry) allowed to log in to the droplet as root, for administration. Terraform registers them as DigitalOcean SSH keys, so none may already be registered in the account. No default: set them in terraform.tfvars before the first apply."
}

variable "droplet_region" {
  type        = string
  default     = "lon1"
  description = "DigitalOcean region slug for the Dokku droplet and its reserved IP."
}

variable "droplet_size" {
  type        = string
  default     = "s-1vcpu-2gb"
  description = "Droplet size slug. 2GB RAM, because Dokku builds the image (including the knowledge-base index) on the droplet."
}

variable "droplet_image" {
  type        = string
  default     = "ubuntu-24-04-x64"
  description = "Droplet image slug. Ubuntu 24.04 LTS: Dokku supports 22.04/24.04/26.04, but its v0.38.32 bootstrap.sh only has packages for jammy and noble (checked 2026-10-28)."
}

variable "dokku_version" {
  type        = string
  default     = "v0.38.32"
  description = "Dokku release tag installed by cloud-init. Latest stable as of 2026-10-28 (released 2026-10-25)."
}

variable "dokku_letsencrypt_version" {
  type        = string
  default     = "0.25.2"
  description = "dokku-letsencrypt plugin git tag installed by cloud-init. Latest release as of 2026-10-28 (released 2026-08-28)."
}
