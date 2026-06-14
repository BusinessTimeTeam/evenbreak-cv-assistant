output "app_url" {
  value       = digitalocean_app.chat_ui.live_url
  description = "URL of the chat UI application."
}

output "agent_uuid" {
  value       = digitalocean_gradientai_agent.rag_agent.id
  description = "UUID of the managed RAG agent."
}

output "agent_endpoint" {
  value       = "${digitalocean_gradientai_agent.rag_agent.deployment[0].url}/api/v1/chat/completions"
  description = "OpenAI-compatible chat endpoint of the agent (for local dev AGENT_ENDPOINT)."
}

output "knowledge_base_uuid" {
  value       = digitalocean_gradientai_knowledge_base.kb.id
  description = "UUID of the knowledge base. Upload documents via the DO console."
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

output "agent_id" {
  value       = digitalocean_gradientai_agent.rag_agent.id
  description = "GenAI agent resource ID."
}

output "knowledge_base_id" {
  value       = digitalocean_gradientai_knowledge_base.kb.id
  description = "Knowledge base resource ID."
}
