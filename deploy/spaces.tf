# Spaces bucket holding the knowledge base source documents.
#
# Documents live in knowledge-base/data-sources/ in this repo and are synced to
# the bucket by Terraform: add/edit/remove a file there and `terraform apply`
# creates/updates/deletes the matching object. Nothing reads the bucket any
# more (the chat UI builds its index from the repo); it is kept until the
# infrastructure is rebuilt.
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
