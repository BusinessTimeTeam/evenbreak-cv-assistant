output "app_url" {
  value       = "https://${var.domain}"
  description = "URL of the chat UI application."
}

output "project_id" {
  value       = local.active_project_id
  description = "Project ID containing all resources."
}

// =============================================================================
// DOKKU DROPLET
// =============================================================================

output "reserved_ip" {
  value       = digitalocean_reserved_ip.chat.ip_address
  description = "The droplet's reserved IP. Point the domain's A record at it (once) and deploy to ssh://dokku@<reserved_ip>:22/chat-ui."
}

output "dokku_ssh_known_hosts" {
  value       = "${var.domain},${digitalocean_reserved_ip.chat.ip_address} ${trimspace(tls_private_key.host.public_key_openssh)}"
  description = "known_hosts line for the droplet's stable SSH host key, for the deploy workflow."
}

output "dokku_deploy_private_key" {
  value       = tls_private_key.deploy.private_key_openssh
  sensitive   = true
  description = "OpenSSH private key the deploy workflow pushes to Dokku with. Store it as a GitHub Actions secret once."
}
