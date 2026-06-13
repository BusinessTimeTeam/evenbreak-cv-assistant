# Resolve the serverless inference and embedding model UUIDs from their
# human-readable names. This keeps the model *choice* in version-controlled
# config (and the resolved UUID in Terraform state) instead of as opaque UUIDs
# pasted into terraform.tfvars by an external resolver. Recreating the infra no
# longer requires anyone to look the UUIDs up by hand.
#
# Model names are unique within the GenAI platform, so each filter resolves to
# exactly one model.
data "digitalocean_gradientai_models" "inference" {
  filter {
    key    = "name"
    values = [var.model_name]
  }
}

data "digitalocean_gradientai_models" "embedding" {
  filter {
    key    = "name"
    values = [var.embedding_model_name]
  }
}

locals {
  model_uuid           = data.digitalocean_gradientai_models.inference.models[0].uuid
  embedding_model_uuid = data.digitalocean_gradientai_models.embedding.models[0].uuid
}
