#!/usr/bin/env bash
# SSH to the Dokku droplet, using Terraform state for its address and host key.
#
#   deploy/ssh.sh                      # a root shell (your own admin key)
#   deploy/ssh.sh cloud-init status    # run one command as root
#   deploy/ssh.sh --dokku apps:list    # a Dokku command, with the deploy key
#
# As root it logs in with your own key (one of admin_ssh_public_keys, from your
# ssh-agent or ~/.ssh). With --dokku it logs in as the dokku user with the
# Terraform-generated deploy key, which can only run Dokku commands; that key
# is added at the very end of first-boot setup, so --dokku is refused until
# setup has finished (`deploy/ssh.sh cloud-init status --wait`).
#
# The host key is pinned from the dokku_ssh_known_hosts output, so it never
# prompts or trusts a changed key (see droplet-ssh.sh).

set -euo pipefail

# shellcheck source-path=SCRIPTDIR source=droplet-ssh.sh
source "$(dirname "${BASH_SOURCE[0]}")/droplet-ssh.sh"

user=root
if [[ "${1:-}" == "--dokku" ]]; then
  user=dokku
  shift
fi

droplet_ssh_setup "$user"

# Not exec: the trap must run afterwards to delete the key and known_hosts.
# The arguments are the remote command, so they're meant to reach the server.
# shellcheck disable=SC2029
ssh "${ssh_args[@]}" "$user@$droplet_ip" "$@"
