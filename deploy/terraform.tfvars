# Secrets (do_token, model_access_key) are supplied via
# TF_VAR_* environment variables, not this file. See env.sh.example.
project_uuid = "5382b7ad-d7d6-4785-b012-05a324d07c83"
basename     = "rag-assistant"
region       = "lon" # App Platform region slug

# Dokku droplet. letsencrypt_email and admin_ssh_public_keys have no default
# and must be set here by the user before the first apply (left out on purpose:
# placeholder values would create a droplet with fake keys and a fake email).
#   letsencrypt_email     = "you@example.com"
#   admin_ssh_public_keys = ["ssh-ed25519 AAAA... you@laptop"]
