# Spaces bucket holding the knowledge base source documents.
#
# Documents live in knowledge-base/data-sources/ in this repo and are synced to
# the bucket by Terraform: add/edit/remove a file there and `terraform apply`
# creates/updates/deletes the matching object. The knowledge base then indexes
# the bucket via the spaces data source below.
#
# Co-located with the knowledge base in tor1 by default (var.spaces_region).
resource "digitalocean_spaces_bucket" "kb_docs" {
  name          = "${local.resource_name}-kb-docs"
  region        = var.spaces_region
  acl           = "private"
  force_destroy = true
}

locals {
  kb_docs_dir = "${path.module}/../knowledge-base/data-sources"

  kb_doc_content_types = {
    txt  = "text/plain"
    md   = "text/markdown"
    pdf  = "application/pdf"
    html = "text/html"
    csv  = "text/csv"
    json = "application/json"
  }
}

# One object per file under knowledge-base/data-sources/ (recursive). The etag
# tracks content so edits re-upload; removing a file deletes its object.
resource "digitalocean_spaces_bucket_object" "kb_docs" {
  for_each = fileset(local.kb_docs_dir, "**")

  region       = digitalocean_spaces_bucket.kb_docs.region
  bucket       = digitalocean_spaces_bucket.kb_docs.name
  key          = each.value
  source       = "${local.kb_docs_dir}/${each.value}"
  etag         = filemd5("${local.kb_docs_dir}/${each.value}")
  content_type = lookup(local.kb_doc_content_types, lower(element(reverse(split(".", each.value)), 0)), "text/plain")
  acl          = "private"
}

# Attach the bucket to the existing knowledge base as a data source. This is a
# standalone resource (not inline on the KB) so it can be added without forcing
# the knowledge base to be recreated.
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
