#!/usr/bin/env bash
# Started by Terraform (terraform_data.deploy_new_droplet in droplet.tf) when
# the droplet is created or replaced: runs the Deploy workflow on main, which
# waits for first-boot setup to finish and then deploys the app and enables
# HTTPS. A new droplet has Dokku set up but no app until something deploys it.
#
# Uses the gh CLI, which works out the repository from this checkout's git
# remote. Locally it needs `gh auth login`; in CI the Terraform Apply workflow
# passes its token in GH_TOKEN.

set -euo pipefail

if ! command -v gh > /dev/null; then
  echo "error: the gh CLI isn't installed, so the new droplet wasn't deployed. Install gh and run \`terraform apply\` again, or deploy by hand with deploy/push.sh main" >&2
  exit 1
fi

# GitHub can only start a workflow that exists on the default branch, which
# deploy.yml doesn't until the Dokku branch is merged.
if ! response="$(gh api "repos/{owner}/{repo}/contents/.github/workflows/deploy.yml?ref=main" --silent 2>&1)"; then
  if [[ "$response" == *"Not Found"* || "$response" == *"404"* ]]; then
    echo "warning: deploy.yml isn't on main yet, so no deploy was started. Deploy the new droplet by hand: deploy/push.sh" >&2
    exit 0
  fi
  echo "error: couldn't check for deploy.yml on main: $response" >&2
  exit 1
fi

gh workflow run deploy.yml --ref main
echo "Started the Deploy workflow on main for the new droplet. Follow it with: gh run watch \$(gh run list --workflow deploy.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
