# Chat UI deployed on App Platform.
# Serves a simple web interface that calls the managed agent's chat API.
# The app self-discovers the agent's deployment URL and API key at startup.
resource "digitalocean_app" "chat_ui" {
  depends_on = [digitalocean_gradientai_agent.rag_agent]

  # The DO_API_TOKEN env var is a SECRET that the provider cannot read back, so
  # the env set always shows as drift and every apply would redeploy the app.
  # Env values are set correctly at create time; freeze them afterward so routine
  # applies stay clean (AGENT_UUID/AGENT_NAME do not legitimately change).
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

      git {
        repo_clone_url = "https://github.com/${var._app_source_repo}.git"
        branch         = var._app_source_branch
      }

      source_dir      = "blueprints/rag-assistant/chat-ui"
      dockerfile_path = "blueprints/rag-assistant/chat-ui/Dockerfile"

      env {
        key   = "AGENT_UUID"
        value = digitalocean_gradientai_agent.rag_agent.id
        scope = "RUN_TIME"
      }

      env {
        key   = "DO_API_TOKEN"
        value = var.do_token
        scope = "RUN_TIME"
        type  = "SECRET"
      }

      env {
        key   = "AGENT_NAME"
        value = digitalocean_gradientai_agent.rag_agent.name
        scope = "RUN_TIME"
      }
    }
  }
}
