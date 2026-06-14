# Chat UI deployed on App Platform.
# Serves a simple web interface that calls the managed agent's chat API.
# The agent endpoint and API key are passed in directly via env; the app does
# no DO API discovery or key generation.
resource "digitalocean_app" "chat_ui" {
  depends_on = [digitalocean_gradientai_agent.rag_agent]

  # The AGENT_API_KEY env var is a SECRET that the provider cannot read back, so
  # the env set always shows as drift and every apply would redeploy the app.
  # Env values are set correctly at create time; freeze them afterward so routine
  # applies stay clean (AGENT_ENDPOINT do not legitimately change).
  lifecycle {
    ignore_changes = [spec[0].service[0].env]
  }

  spec {
    name   = "${local.resource_name}-chat"
    region = var.region

    ingress {
      rule {
        component {
          name = "chat-ui"
        }
        match {
          path {
            prefix = "/"
          }
        }
      }
    }

    service {
      name               = "chat-ui"
      instance_count     = 1
      instance_size_slug = var.app_instance_size
      http_port          = 8080

      # Private org repo, so use the GitHub integration (not a public clone URL).
      # Requires the DigitalOcean GitHub app to be authorized on this repo.
      github {
        repo           = var._app_source_repo
        branch         = var._app_source_branch
        deploy_on_push = true
      }

      source_dir      = "chat-ui"
      dockerfile_path = "chat-ui/Dockerfile"

      env {
        key   = "AGENT_ENDPOINT"
        value = "${digitalocean_gradientai_agent.rag_agent.deployment[0].url}/api/v1/chat/completions"
        scope = "RUN_TIME"
      }

      env {
        key   = "AGENT_API_KEY"
        value = var.agent_api_key
        scope = "RUN_TIME"
        type  = "SECRET"
      }
    }
  }
}
