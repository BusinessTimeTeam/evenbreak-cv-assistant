# Knowledge base for RAG document retrieval. Ingests two sources: the Evenbreak
# homepage (inline web crawler, below) and the CV documents in the Spaces bucket
# (spaces_docs data source, below). The bucket itself is defined in spaces.tf.
# NOTE: Knowledge bases currently only support the tor1 region.
resource "digitalocean_gradientai_knowledge_base" "kb" {
  name                 = "${local.resource_name}-kb"
  project_id           = local.active_project_id
  region               = "tor1"
  embedding_model_uuid = local.embedding_model_uuid
  tags                 = [digitalocean_tag.tag.name]

  # Required inline datasource (the KB needs at least one). Crawls only the
  # Evenbreak homepage for now (SCOPED = the given URL only, not the whole site).
  # Bucket-based CV documents are attached separately (see spaces.tf), so editing
  # those files never touches this resource.
  datasources {
    web_crawler_data_source {
      base_url        = "https://www.evenbreak.com/"
      crawling_option = "SCOPED"
    }
  }

  # Both of these are ignored because the live KB diverges from this config in
  # ways Terraform would otherwise "fix" destructively. See README.md "Known
  # issues" for the full explanation.
  #
  #   datasources - the Spaces bucket is attached as a separate resource
  #     (spaces_docs, below), so the API reports it as an extra datasource on the
  #     KB. datasources is ForceNew, so without this ignore every apply would
  #     RECREATE the KB. Trade-off: the inline web crawler can't be changed via
  #     this block either - see README for how to change/remove it.
  #   database_id - the GenAI platform auto-provisions its own managed OpenSearch
  #     cluster and assigns it here; a supplied database_id is ignored, so this is
  #     a server-managed value.
  lifecycle {
    ignore_changes = [datasources, database_id]
  }
}

# Attach the Spaces documents bucket to the knowledge base as a data source. Kept
# as a standalone resource (rather than inline on the KB) so it can be managed
# without forcing the knowledge base to be recreated. The bucket and its objects
# are defined in spaces.tf.
resource "digitalocean_gradientai_knowledge_base_data_source" "spaces_docs" {
  knowledge_base_uuid = digitalocean_gradientai_knowledge_base.kb.id

  spaces_data_source {
    bucket_name = digitalocean_spaces_bucket.kb_docs.name
    region      = digitalocean_spaces_bucket.kb_docs.region
  }

  depends_on = [digitalocean_spaces_bucket_object.kb_docs]
}

# Terraform syncs files to Spaces but does not trigger knowledge base indexing.
# Fire an indexing job for the Spaces data source whenever the document content
# changes (the trigger hashes every object's etag), so the full flow is:
# edit a file in knowledge-base/data-sources/ -> apply -> uploaded AND re-indexed.
resource "null_resource" "kb_reindex" {
  triggers = {
    docs_hash = sha256(join(",", [for k, o in digitalocean_spaces_bucket_object.kb_docs : o.etag]))
    ds_uuid   = digitalocean_gradientai_knowledge_base_data_source.spaces_docs.id
  }

  provisioner "local-exec" {
    command = <<-EOT
      curl -sf -X POST \
        -H "Authorization: Bearer ${var.do_token}" \
        -H "Content-Type: application/json" \
        -d '{"knowledge_base_uuid":"${digitalocean_gradientai_knowledge_base.kb.id}","data_source_uuids":["${digitalocean_gradientai_knowledge_base_data_source.spaces_docs.id}"]}' \
        "https://api.digitalocean.com/v2/gen-ai/indexing_jobs"
    EOT
  }
}
