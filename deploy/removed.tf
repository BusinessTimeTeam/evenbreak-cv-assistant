# The knowledge-base documents bucket was deleted by hand in the DO console, so
# drop it and its objects from state instead of trying to destroy them. Safe to
# delete this file once an apply has run with it.
removed {
  from = digitalocean_spaces_bucket.kb_docs

  lifecycle {
    destroy = false
  }
}

removed {
  from = digitalocean_spaces_bucket_object.kb_docs

  lifecycle {
    destroy = false
  }
}
