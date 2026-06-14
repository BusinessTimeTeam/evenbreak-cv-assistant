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

variable "spaces_access_id" {
  type        = string
  default     = ""
  description = "DigitalOcean Spaces access key ID (S3-compatible). Required to manage the knowledge base documents bucket."
  sensitive   = true
}

variable "spaces_secret_key" {
  type        = string
  default     = ""
  description = "DigitalOcean Spaces secret key (S3-compatible). Required to manage the knowledge base documents bucket."
  sensitive   = true
}

variable "spaces_region" {
  type        = string
  default     = "tor1"
  description = "Region for the Spaces bucket holding knowledge base documents. Co-located with the knowledge base (tor1) by default."
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
// MODEL CONFIGURATION
// =============================================================================

variable "model_name" {
  type        = string
  default     = "OpenAI GPT-oss-20b"
  description = "Display name of the serverless inference model. Resolved to a UUID via the digitalocean_gradientai_models data source (see models.tf)."
}

variable "embedding_model_name" {
  type        = string
  default     = "Qwen3 Embedding 0.6B"
  description = "Display name of the embedding model. Resolved to a UUID via the digitalocean_gradientai_models data source (see models.tf)."
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

variable "agent_api_key" {
  type        = string
  sensitive   = true
  description = "Secret API key the chat UI uses to call the managed agent. Mint one out-of-band (DO console > GenAI > Agents > API Keys, or the API) and supply via TF_VAR_agent_api_key. The provider has no resource to create agent API keys, so this is operator-supplied like do_token."
}

// =============================================================================
// AGENT CONFIGURATION
// =============================================================================

variable "agent_instruction" {
  type        = string
  default     = "You are a helpful RAG assistant. Answer questions using the knowledge base context provided. If you don't know the answer, say so honestly."
  description = "System instruction for the managed agent."
}

variable "agent_temperature" {
  type        = number
  default     = 0
  description = "Temperature for inference (0.0 = deterministic, 1.0 = creative). Supplied by model preset."
}

variable "agent_max_tokens" {
  type        = number
  default     = 4096
  description = "Maximum tokens in the agent's response. Supplied by model preset."
}

variable "agent_k" {
  type        = number
  default     = 5
  description = "Number of knowledge base documents to retrieve per query."
}

variable "agent_top_p" {
  type        = number
  default     = 1
  description = "Nucleus sampling cutoff for inference (0.0-1.0)."
}

variable "agent_retrieval_method" {
  type        = string
  default     = "RETRIEVAL_METHOD_SUB_QUERIES"
  description = "Knowledge base retrieval method for the agent."
}

variable "agent_provide_citations" {
  type        = bool
  default     = true
  description = "Whether the agent returns citations for retrieved knowledge base content."
}

// =============================================================================
// GUARDRAIL CONFIGURATION
// =============================================================================

variable "guardrail_jailbreak_uuid" {
  type        = string
  default     = ""
  description = "UUID of the jailbreak detection guardrail. Resolved by do-terraform."
}

variable "guardrail_content_mod_uuid" {
  type        = string
  default     = ""
  description = "UUID of the content moderation guardrail. Resolved by do-terraform."
}

variable "guardrail_sensitive_data_uuid" {
  type        = string
  default     = ""
  description = "UUID of the sensitive data detection guardrail. Resolved by do-terraform."
}
