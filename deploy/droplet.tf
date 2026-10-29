# Chat UI hosted on a Dokku droplet in London.
# Replaces App Platform (app.tf), whose 100s request limit and response
# buffering break streamed replies. Both run side by side until the droplet is
# confirmed working on var.domain; then app.tf is removed.
#
# The droplet is disposable: cloud-init (cloud-init.yaml.tftpl) installs Dokku
# and sets up the app on first boot, so any change to the setup - including a
# rotated secret - replaces the droplet. The reserved IP stays put, so DNS
# never changes, and the host key comes from Terraform, so known_hosts never
# changes either. The deploy workflow pushes the app once setup has finished.
#
# Secrets (MODEL_ACCESS_KEY, HISTORY_SIGNING_KEY, the host private key) end up
# in the droplet's user_data and in Terraform state, as they already are in
# state for App Platform.

// =============================================================================
// SSH KEYS
// =============================================================================

# The deploy workflow's key for `git push dokku@...`. The private half is the
# sensitive output dokku_deploy_private_key, stored once as a GitHub secret.
resource "tls_private_key" "deploy" {
  algorithm = "ED25519"
}

# The droplet's SSH host key, the same across rebuilds. Its public half is in
# the dokku_ssh_known_hosts output.
resource "tls_private_key" "host" {
  algorithm = "ED25519"
}

# The admin keys for logging in as root, registered with DigitalOcean so the
# droplet is created with them. A droplet created without registered keys gets
# an emailed root password that must be changed at first login, and until it
# is, PAM refuses root for sudo and chfn, which breaks the Dokku install.
# DigitalOcean rejects a public key that's already registered in the account,
# so a key added by hand in the control panel can't also be listed here.
resource "digitalocean_ssh_key" "admin" {
  for_each = toset([for key in var.admin_ssh_public_keys : trimspace(key)])

  name       = "${local.resource_name}-admin-${substr(sha256(each.value), 0, 8)}"
  public_key = each.value
}

// =============================================================================
// DROPLET
// =============================================================================

resource "digitalocean_droplet" "chat" {
  name       = "${local.resource_name}-dokku"
  region     = var.droplet_region
  size       = var.droplet_size
  image      = var.droplet_image
  monitoring = true
  # Root's authorized keys. Changing them replaces the droplet.
  ssh_keys = [for key in digitalocean_ssh_key.admin : key.fingerprint]

  # Changing the rendered template replaces the droplet (user_data forces a
  # new resource).
  user_data = templatefile("${path.module}/cloud-init.yaml.tftpl", {
    domain                    = var.domain
    letsencrypt_email         = var.letsencrypt_email
    dokku_version             = var.dokku_version
    dokku_letsencrypt_version = var.dokku_letsencrypt_version
    host_private_key          = tls_private_key.host.private_key_openssh
    host_public_key           = trimspace(tls_private_key.host.public_key_openssh)
    deploy_public_key         = trimspace(tls_private_key.deploy.public_key_openssh)
    model_access_key          = var.model_access_key
    history_signing_key       = random_password.history_signing_key.result
  })
}

// =============================================================================
// RESERVED IP
// =============================================================================

# The address the domain's A record points at (set by hand, once, at the DNS
# host). It outlives droplet rebuilds.
resource "digitalocean_reserved_ip" "chat" {
  region = var.droplet_region
}

resource "digitalocean_reserved_ip_assignment" "chat" {
  ip_address = digitalocean_reserved_ip.chat.ip_address
  droplet_id = digitalocean_droplet.chat.id
}

// =============================================================================
// FIREWALL
// =============================================================================

# Inbound: SSH (admin and git push deploys), HTTP (Let's Encrypt challenges and
# the redirect to HTTPS) and HTTPS only.
resource "digitalocean_firewall" "chat" {
  name        = "${local.resource_name}-dokku"
  droplet_ids = [digitalocean_droplet.chat.id]

  inbound_rule {
    protocol         = "tcp"
    port_range       = "22"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  inbound_rule {
    protocol         = "tcp"
    port_range       = "80"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  inbound_rule {
    protocol         = "tcp"
    port_range       = "443"
    source_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "tcp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "udp"
    port_range            = "1-65535"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }

  outbound_rule {
    protocol              = "icmp"
    destination_addresses = ["0.0.0.0/0", "::/0"]
  }
}

// =============================================================================
// FIRST DEPLOY
// =============================================================================

# A new droplet comes up with Dokku set up but no app. When the droplet is
# created or replaced, start the Deploy workflow on main (trigger-deploy.sh),
# which waits for first-boot setup and then deploys and enables HTTPS, so a
# rebuild needs no manual steps, whether the apply runs locally or in CI. It
# runs after the reserved IP points at the new droplet, which is where the
# workflow deploys to.
resource "terraform_data" "deploy_new_droplet" {
  triggers_replace = digitalocean_droplet.chat.id

  depends_on = [
    digitalocean_reserved_ip_assignment.chat,
    digitalocean_firewall.chat,
  ]

  provisioner "local-exec" {
    command = "${path.module}/trigger-deploy.sh"
  }
}
