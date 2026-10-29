# shellcheck shell=bash
# Shared by deploy/ssh.sh and deploy/push.sh: SSH settings for the Dokku
# droplet, taken from Terraform state. Source it, then call droplet_ssh_setup
# with the user to log in as (root or dokku). It sets:
#
#   droplet_ip  the reserved IP
#   ssh_args    ssh options: the host key pinned from the dokku_ssh_known_hosts
#               output, plus the deploy key when the user is dokku
#
# Temporary files (the known_hosts line, the deploy key) live in a private
# directory that's deleted when the calling script exits. Needs Terraform's
# backend credentials: it sources deploy/env.sh if they aren't already set.

deploy_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

_tf_output() {
  terraform -chdir="$deploy_dir" output -raw "$1"
}

droplet_ssh_setup() {
  local user="$1"

  if [[ -z "${AWS_ACCESS_KEY_ID:-}" ]]; then
    if [[ ! -f "$deploy_dir/env.sh" ]]; then
      echo "error: Terraform's backend credentials aren't set and $deploy_dir/env.sh doesn't exist (see env.sh.example)" >&2
      exit 1
    fi
    # shellcheck source=/dev/null
    source "$deploy_dir/env.sh"
  fi

  droplet_tmp="$(mktemp -d)"
  trap 'rm -rf "$droplet_tmp"' EXIT
  chmod 700 "$droplet_tmp"

  # shellcheck disable=SC2034 # used by the scripts that source this file
  droplet_ip="$(_tf_output reserved_ip)"
  _tf_output dokku_ssh_known_hosts > "$droplet_tmp/known_hosts"

  ssh_args=(
    -o "UserKnownHostsFile=$droplet_tmp/known_hosts"
    -o StrictHostKeyChecking=yes
  )
  if [[ "$user" == dokku ]]; then
    (umask 077 && _tf_output dokku_deploy_private_key > "$droplet_tmp/deploy_key")
    ssh_args+=(-i "$droplet_tmp/deploy_key" -o IdentitiesOnly=yes)
  fi
}
