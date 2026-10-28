# Chat UI deployed on App Platform.
# Serves a simple web interface that answers from a knowledge-base index built
# into its image, using DO serverless inference (MODEL_ACCESS_KEY).
#
# MODEL_ACCESS_KEY is a SECRET the provider cannot read back, so the env always
# shows as drift and every apply redeploys the app. That is accepted: pushes
# that change documents redeploy it anyway to rebuild the index.
resource "digitalocean_app" "chat_ui" {
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

      # Build from the repo root so the image's index stage can read
      # knowledge-base/data-sources/.
      source_dir      = "/"
      dockerfile_path = "chat-ui/Dockerfile"

      # Needed at build time (embedding the knowledge base; App Platform passes
      # it to the Dockerfile as a build arg) and at run time (every chat turn).
      env {
        key   = "MODEL_ACCESS_KEY"
        value = var.model_access_key
        scope = "RUN_AND_BUILD_TIME"
        type  = "SECRET"
      }
    }
  }
}
