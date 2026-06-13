# Knowledge base for RAG document retrieval.
# Seeded with a placeholder DO docs page. Customer adds their own documents post-deploy.
# NOTE: Knowledge bases currently only support the tor1 region.
resource "digitalocean_gradientai_knowledge_base" "kb" {
  name                 = "${local.resource_name}-kb"
  project_id           = local.active_project_id
  region               = "tor1"
  embedding_model_uuid = var.embedding_model_uuid
  tags                 = [digitalocean_tag.tag.name]

  datasources {
    web_crawler_data_source {
      base_url        = "https://docs.digitalocean.com/products/genai-platform/getting-started/quickstart/"
      crawling_option = "PATH"
    }
  }

  # Datasources (especially file uploads) are managed out-of-band: the provider
  # cannot import a KB that has a file-upload datasource, and file uploads are
  # inherently imperative. Source documents live in knowledge-base/data-sources/
  # and are loaded via scripts. Ignore datasource drift so adding documents does
  # not force the KB to be recreated.
  lifecycle {
    ignore_changes = [datasources, database_id]
  }
}
