# Design: Manage the launchpad RAG deployment with Terraform (as-built)

**Date:** 2026-06-09
**Status:** Implemented. This document records the final design as built, including
the deviations discovered during implementation.

## Goal

Take over management of the existing DigitalOcean "CV Assistant" RAG deployment
— created via the DO launchpad from this blueprint — with stock Terraform, using
the `deploy/` directory as the durable source of truth. Keep the existing
resources (import, do not recreate).

## Background

The blueprint was authored to run under DigitalOcean's internal `do-terraform`
wrapper, which resolves model/guardrail *names* into UUIDs and injects them. We
run stock `terraform`, so those UUIDs are supplied via `terraform.tfvars`; every
value was read off the already-deployed resources.

### Live deployment (all in project `CV Assistant`, suffix `alpg`)

| Resource | Name | ID / UUID |
|---|---|---|
| Project (data source) | CV Assistant | `5382b7ad-d7d6-4785-b012-05a324d07c83` |
| Agent (tor1) | rag-assistant-alpg-agent | `0f878718-63cf-11f1-b074-4e013e2ddde4` |
| └ model_uuid | — | `153b8921-73c5-11f0-b074-4e013e2ddde4` |
| Knowledge base (tor1) | rag-assistant-alpg-kb | `1248867b-63cf-11f1-b074-4e013e2ddde4` |
| └ embedding_model_uuid | — | `bb3ab4ee-d9b5-11f0-b074-4e013e2ddde4` |
| App Platform (lon) | rag-assistant-alpg-chat | `21352860-7a34-49bd-bcb1-975bbf771211` |
| Tag | rag-assistant-resource | `rag-assistant-resource` |

Launchpad had already attached the KB and all three guardrails (Content
Moderation, Jailbreak, Sensitive Data) to the agent. The app builds from
`digitalocean/marketplace-blueprints` @ `master`,
`source_dir: blueprints/rag-assistant/chat-ui`.

## Mechanics

- **Working dir:** `deploy/`. **Provider:** `digitalocean ~> 2.85.0` (bumped from
  `2.81.0`; 2.81 could not import GenAI resources cleanly).
- **State:** local `deploy/terraform.tfstate` (gitignored). Remote backend deferred.
- **Secrets/vars:** `deploy/terraform.tfvars` (gitignored) holds the DO token,
  Spaces keys, resolved UUIDs, and the values that pin this deployment to the live
  resources.

### Pinning generated names — `name_suffix` variable

Names are `${var.basename}-${local.name_suffix}`. Live resources use suffix
`alpg`. `random_string.suffix` cannot be imported to a fixed value (import does
not capture its generation args, so it forces replacement → a new random suffix →
renamed resources). Instead the suffix is a variable: `random_string.suffix` is
gated `count = var.name_suffix == "" ? 1 : 0`, and
`local.name_suffix = var.name_suffix != "" ? var.name_suffix : one(random_string.suffix[*].result)`.
This deployment sets `name_suffix = "alpg"`; fresh deployments leave it empty and
get a generated suffix.

### Keep `null_resource.agent_post_setup`, but skip it here — `agent_post_setup_enabled`

That block curl-attaches the KB and guardrails to the agent. It is kept so a fresh
deployment still wires everything up, but `null_resource` import is **not supported**
by the null provider, so the "import + ignore_changes" idea was abandoned. Instead
the block is gated `count = var.agent_post_setup_enabled ? 1 : 0` (default `true`).
This deployment sets `agent_post_setup_enabled = false` (KB + guardrails already
attached), so Terraform simply never creates it.

### Agent — preserve the launchpad's settings

The launchpad created the agent with different tuning than the blueprint defaults.
Per decision, the live settings are preserved (no behavioural change). The
hardcoded `retrieval_method` / `provide_citations` were turned into variables so
fresh deployments keep the blueprint defaults while this deployment overrides them.
`terraform.tfvars` sets: `agent_temperature = 1`, `agent_max_tokens = 2048`,
`agent_k = 10`, `agent_retrieval_method = "RETRIEVAL_METHOD_NONE"`,
`agent_provide_citations = false`.

The agent also carries read-only/attached state the bare config does not declare.
To avoid detaching guardrails or fighting computed fields, the agent has:
```hcl
lifecycle {
  ignore_changes = [
    agent_guardrail, deployment, top_p, api_keys, api_key_infos,
    chatbot_identifiers, created_at, route_uuid, user_id,
  ]
}
```

### App — stop the secret causing perpetual redeploys

`DO_API_TOKEN` is a `SECRET` Terraform cannot read back, so the `env` set always
shows drift and every apply would redeploy the app. The app freezes its env after
creation:
```hcl
lifecycle {
  ignore_changes = [spec[0].service[0].env]
}
```
Env values are set correctly at create time; `AGENT_UUID`/`AGENT_NAME` do not
legitimately change. The app region is `lon` (App Platform's 3-letter slug, not
`lon1`).

### `project_resources` and the tag

`digitalocean_project_resources` does **not** support import, so it is created on
the first apply (a no-op membership re-assertion). It lists the app URN and the
Spaces bucket URN. The tag imports cleanly.

## Knowledge base documents — Spaces-backed (the significant redesign)

The blueprint models the KB with an inline `web_crawler` datasource and expects
documents to be added out-of-band. Reality and constraints forced a different
approach:

1. The live KB contained a **file-upload** datasource (`businesstime.txt`, 836 B).
2. The provider (2.81 **and** 2.85) **cannot import a KB that has a file-upload
   datasource** (`Invalid address to set: datasources.0.file_upload_data_source.0.size`).
3. File uploads are inherently imperative and not declaratively manageable.

To import the KB, the file-upload datasource was deleted via the API (the source
file is preserved in the repo at `knowledge-base/data-sources/businesstime.txt`).
The KB then imported with only its web crawler, and carries:
```hcl
lifecycle {
  ignore_changes = [datasources, database_id]
}
```
so datasource changes never force a KB recreate and the backing OpenSearch DB is
left alone.

Documents are now managed declaratively via Spaces (`deploy/spaces.tf`):

- `digitalocean_spaces_bucket.kb_docs` — `rag-assistant-alpg-kb-docs`, region
  `tor1` (co-located with the KB), private, `force_destroy = true`.
- `digitalocean_spaces_bucket_object.kb_docs` — `for_each` over
  `fileset("knowledge-base/data-sources", "**")`, `etag = filemd5(...)`. Editing /
  adding / removing a file in that folder syncs the bucket on `apply`.
- `digitalocean_gradientai_knowledge_base_data_source.spaces_docs` — attaches the
  bucket to the existing KB without recreating it.
- `null_resource.kb_reindex` — triggers on the hash of all object etags and fires a
  KB indexing job via the API, so doc changes are uploaded **and** re-indexed.

Managing Spaces requires S3-style **Spaces access keys** (`spaces_access_id` /
`spaces_secret_key` on the provider), separate from the DO API token.

## Provider import quirks encountered (reference)

- `random_string` — importable, but loses generation args → forces replacement.
  Solved with the `name_suffix` variable instead.
- `null_resource` — import not implemented. Solved with the
  `agent_post_setup_enabled` count gate.
- `digitalocean_project_resources` — import not supported; created on apply.
- `digitalocean_gradientai_knowledge_base` — not importable while it has a
  file-upload datasource.

## Status / success criteria

`terraform plan` is clean except `null_resource.kb_reindex` (`1 to add`), which is
intentionally unapplied so it does not fire an indexing job yet. Zero
destroys/replacements. App verified serving HTTP 200; KB + guardrails intact.

**Deferred:** the Spaces indexing job wedged on DO's side (config is correct; a
runtime issue). Applying `kb_reindex` later retriggers indexing.

## Out of scope (future)

- Point the chat-ui app at this fork (`BusinessTimeTeam/evenbreak-cv-assistant`,
  `chat-ui/` at repo root) instead of the marketplace repo. Requires the
  DigitalOcean GitHub app authorized on the private org repo.
- Remote state backend (DO Spaces).
- Commit `.terraform.lock.hcl` for reproducibility (currently gitignored).
