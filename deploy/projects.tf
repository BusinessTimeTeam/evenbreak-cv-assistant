# Fresh deployments generate a random suffix. An imported existing deployment
# pins it via var.name_suffix so generated names keep matching the live resources.
resource "random_string" "suffix" {
  count   = var.name_suffix == "" ? 1 : 0
  length  = 4
  special = false
  upper   = false
}

locals {
  name_suffix          = var.name_suffix != "" ? var.name_suffix : one(random_string.suffix[*].result)
  resource_name        = "${var.basename}-${local.name_suffix}"
  project_display_name = var.project_name != "" ? var.project_name : var.basename
}

# Create a new project if project_uuid is not provided.
resource "digitalocean_project" "rag_assistant" {
  count       = var.project_uuid == "" ? 1 : 0
  name        = local.project_display_name
  purpose     = "RAG Assistant"
  environment = "Development"
}

# Use existing project if project_uuid is provided.
data "digitalocean_project" "existing" {
  count = var.project_uuid != "" ? 1 : 0
  id    = var.project_uuid
}

locals {
  active_project_id = var.project_uuid == "" ? digitalocean_project.rag_assistant[0].id : data.digitalocean_project.existing[0].id
}

resource "digitalocean_project_resources" "project_resources" {
  project = local.active_project_id
  resources = [
    digitalocean_app.chat_ui.urn,
    digitalocean_spaces_bucket.kb_docs.urn,
  ]
}
