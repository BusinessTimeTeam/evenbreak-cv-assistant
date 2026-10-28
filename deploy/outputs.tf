output "app_url" {
  value       = digitalocean_app.chat_ui.live_url
  description = "URL of the chat UI application."
}

output "project_id" {
  value       = local.active_project_id
  description = "Project ID containing all resources."
}

# Resource ID outputs for stack_resources tracking.
output "app_platform_id" {
  value       = digitalocean_app.chat_ui.id
  description = "App Platform resource ID."
}
