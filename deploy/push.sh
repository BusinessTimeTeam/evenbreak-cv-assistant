#!/usr/bin/env bash
# Deploy a branch to the Dokku droplet by hand, with the deploy key from
# Terraform state (Task 9's manual deploy, before the branch is merged).
#
#   deploy/push.sh                  # deploy the current branch
#   deploy/push.sh some-branch      # deploy another branch
#   deploy/push.sh --dry-run        # check the connection; nothing is deployed
#   deploy/push.sh --force          # deploy over history Dokku would reject
#
# Whatever branch you push becomes the droplet's `main`, its deploy branch, and
# Dokku builds and starts it. Only committed work is pushed. Once this branch
# is merged, deploys happen automatically on every push to main (deploy.yml),
# so this is for bringing a server up, or for an emergency redeploy.

set -euo pipefail

# shellcheck source-path=SCRIPTDIR source=droplet-ssh.sh
source "$(dirname "${BASH_SOURCE[0]}")/droplet-ssh.sh"

push_flags=()
branch=""
for arg in "$@"; do
  case "$arg" in
    --dry-run | --force) push_flags+=("$arg") ;;
    -*)
      echo "error: unknown option $arg" >&2
      exit 1
      ;;
    *) branch="$arg" ;;
  esac
done

repo_root="$(git -C "$deploy_dir" rev-parse --show-toplevel)"
if [[ -z "$branch" ]]; then
  branch="$(git -C "$repo_root" symbolic-ref --short HEAD)"
fi
git -C "$repo_root" rev-parse --verify --quiet "refs/heads/$branch" > /dev/null || {
  echo "error: no local branch called $branch" >&2
  exit 1
}

if [[ -n "$(git -C "$repo_root" status --porcelain)" ]]; then
  echo "warning: you have uncommitted changes; only what's committed on $branch is deployed" >&2
fi

droplet_ssh_setup dokku

# git runs this command for its SSH connection, so quote every argument.
git_ssh="ssh"
for opt in "${ssh_args[@]}"; do
  git_ssh+=" $(printf '%q' "$opt")"
done

echo "Deploying $branch ($(git -C "$repo_root" rev-parse --short "$branch")) to dokku@$droplet_ip:chat-ui"
GIT_SSH_COMMAND="$git_ssh" git -C "$repo_root" push ${push_flags[@]+"${push_flags[@]}"} \
  "dokku@$droplet_ip:chat-ui" "refs/heads/$branch:refs/heads/main"
