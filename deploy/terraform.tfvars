# Secrets (do_token, model_access_key) are supplied via
# TF_VAR_* environment variables, not this file. See env.sh.example.
project_uuid = "5382b7ad-d7d6-4785-b012-05a324d07c83"
basename     = "rag-assistant"
region       = "lon" # App Platform region slug

# Dokku droplet. letsencrypt_email and admin_ssh_public_keys have no default
# and must be set here by the user before the first apply
letsencrypt_email     = "security@businesstime.team"
admin_ssh_public_keys = ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIPWUZ6YEvgbqAoO0GMZPwxmzrAmr4HZHDOK4+Zwxs9HT wmayor@CheshireCat.local"]
