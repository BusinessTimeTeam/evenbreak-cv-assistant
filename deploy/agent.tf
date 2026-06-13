# Managed agent for RAG interactions.
resource "digitalocean_gradientai_agent" "rag_agent" {
  name        = "${local.resource_name}-agent"
  description = "RAG Assistant powered by serverless inference with knowledge base retrieval and guardrails."
  # NOTE: GenAI platform is currently only available in tor1.
  region     = "tor1"
  project_id = local.active_project_id

  model_uuid  = local.model_uuid
  instruction = var.agent_instruction
  temperature = var.agent_temperature
  max_tokens  = var.agent_max_tokens
  k           = var.agent_k
  top_p       = var.agent_top_p

  provide_citations = var.agent_provide_citations
  retrieval_method  = var.agent_retrieval_method

  # Guardrails are attached out-of-band (see null_resource.agent_guardrails) and
  # the deployment block reflects live runtime state, so leave both to the API
  # rather than letting Terraform detach guardrails or reset the deployment.
  #
  # The remaining attributes are optional+writable in the provider but are
  # actually server-generated (the schema should mark them computed). Without
  # ignore_changes Terraform plans to null them out on every apply — which would
  # wipe the agent's api_keys (used by the chat-ui), created_at, etc.
  lifecycle {
    ignore_changes = [
      agent_guardrail,
      deployment,
      api_keys,
      api_key_infos,
      chatbot_identifiers,
      created_at,
      route_uuid,
      user_id,
    ]
  }
}

# Attach the knowledge base to the agent. Native provider resource (no curl):
# the KB attachment is now first-class Terraform state. Depends implicitly on the
# agent and KB via their ids. NOTE: on a brand-new KB that is still indexing, this
# attach can fail; if so, just re-run `terraform apply` once indexing finishes.
resource "digitalocean_gradientai_agent_knowledge_base_attachment" "kb" {
  agent_uuid          = digitalocean_gradientai_agent.rag_agent.id
  knowledge_base_uuid = digitalocean_gradientai_knowledge_base.kb.id
}

# Attach guardrails to the agent. There is no provider resource for guardrail
# attachment, so this stays an out-of-band API call. Runs only when at least one
# guardrail UUID is supplied; re-runs if the set of UUIDs changes.
resource "null_resource" "agent_guardrails" {
  count = anytrue([
    var.guardrail_jailbreak_uuid != "",
    var.guardrail_content_mod_uuid != "",
    var.guardrail_sensitive_data_uuid != "",
  ]) ? 1 : 0

  depends_on = [digitalocean_gradientai_agent.rag_agent]

  triggers = {
    agent_id   = digitalocean_gradientai_agent.rag_agent.id
    guardrails = "${var.guardrail_jailbreak_uuid},${var.guardrail_content_mod_uuid},${var.guardrail_sensitive_data_uuid}"
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      AGENT_ID="${digitalocean_gradientai_agent.rag_agent.id}"
      TOKEN="${var.do_token}"
      API="https://api.digitalocean.com/v2/gen-ai"

      GUARDRAILS=""
      %{if var.guardrail_jailbreak_uuid != ""~}
      GUARDRAILS="$GUARDRAILS{\"guardrail_uuid\":\"${var.guardrail_jailbreak_uuid}\",\"priority\":1},"
      %{endif~}
      %{if var.guardrail_content_mod_uuid != ""~}
      GUARDRAILS="$GUARDRAILS{\"guardrail_uuid\":\"${var.guardrail_content_mod_uuid}\",\"priority\":2},"
      %{endif~}
      %{if var.guardrail_sensitive_data_uuid != ""~}
      GUARDRAILS="$GUARDRAILS{\"guardrail_uuid\":\"${var.guardrail_sensitive_data_uuid}\",\"priority\":3},"
      %{endif~}

      GUARDRAILS=$(echo "$GUARDRAILS" | sed 's/,$//')
      echo "Attaching guardrails..."
      curl -sf -X POST \
        -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -d "{\"guardrails\":[$GUARDRAILS]}" \
        "$API/agents/$AGENT_ID/guardrails"
      echo "Guardrails attached"
    EOT
  }
}
