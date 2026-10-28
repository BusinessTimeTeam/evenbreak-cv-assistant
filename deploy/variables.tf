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
  default     = "rag-assistant"
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
  description = "DO model access key the chat UI uses for serverless inference (embedding the knowledge base at build time, and chat at run time). Create one in the DO console (Inference > Model Access Keys) scoped to gpt-oss-20b and Qwen3 Embedding 0.6B, and supply via TF_VAR_model_access_key."
}
