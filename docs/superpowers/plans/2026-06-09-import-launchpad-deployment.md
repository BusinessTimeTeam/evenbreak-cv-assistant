# Import Launchpad RAG Deployment — as-built record & runbook

**Status:** Implemented 2026-06-09. This records what was actually done (the
original step-by-step plan deviated substantially during execution) and how to
operate the result. See the companion spec:
`docs/superpowers/specs/2026-06-09-import-launchpad-deployment-design.md`.

## What was done

1. **Config prep** — wrote gitignored `deploy/terraform.tfvars`; introduced the
   `name_suffix`, `agent_post_setup_enabled`, `agent_retrieval_method`,
   `agent_provide_citations`, and Spaces variables; bumped the provider to
   `~> 2.85.0`.
2. **Imported** into `deploy/` state: `digitalocean_tag.tag`,
   `digitalocean_gradientai_knowledge_base.kb`,
   `digitalocean_gradientai_agent.rag_agent`, `digitalocean_app.chat_ui`.
   - `random_string.suffix`, `null_resource.agent_post_setup`, and
     `digitalocean_project_resources` could **not** be imported — handled via the
     `name_suffix` var, the `agent_post_setup_enabled` count gate, and an
     apply-time create respectively (see spec).
   - The KB only imported after its **file-upload datasource was deleted** via the
     API (provider bug); the source file is preserved at
     `knowledge-base/data-sources/businesstime.txt`.
3. **Reconciled to a clean plan** — added `lifecycle.ignore_changes` to the agent
   (guardrails/deployment/computed fields), the KB (`datasources`, `database_id`),
   and the app (`spec[0].service[0].env`); set tfvars so the agent matches the
   live launchpad settings.
4. **Applied** — agent + app updated in place (no replacements), `project_resources`
   created, gated `random_string` removed from state. Verified: app HTTP 200,
   agent API key + KB + 3 guardrails intact, `terraform plan` clean.
5. **Spaces-backed docs** — added `deploy/spaces.tf`: bucket in `tor1`, repo
   folder sync, KB datasource, and `kb_reindex`. Applied (bucket created,
   `businesstime.txt` uploaded, datasource attached). Generated account-level
   Spaces keys for the provider.

## Deferred

- **KB indexing of the Spaces bucket** is wedged on DO's side (the indexing job
  ran but never progressed; config is correct). `null_resource.kb_reindex` is in
  the config but **intentionally not yet applied** — it shows as the only `plan`
  item (`1 to add`). Applying it fires a fresh indexing job.

## Operations runbook

### Add / change knowledge base documents
1. Add or edit files under `knowledge-base/data-sources/`.
2. `cd deploy && terraform apply` — uploads changed files to the bucket and (once
   `kb_reindex` is applied) triggers a re-index.
3. Check indexing: `doctl genai knowledge-base get 1248867b-63cf-11f1-b074-4e013e2ddde4`
   or the indexing-jobs API.

### Re-trigger indexing manually (current workaround while deferred)
```bash
TOKEN=$(doctl auth token)
curl -sf -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"knowledge_base_uuid":"1248867b-63cf-11f1-b074-4e013e2ddde4","data_source_uuids":["8cb4aa90-6445-11f1-b074-4e013e2ddde4"]}' \
  "https://api.digitalocean.com/v2/gen-ai/indexing_jobs"
```

### Standard workflow
```bash
cd deploy
terraform plan      # review
terraform apply     # token/Spaces keys come from terraform.tfvars
```

### Fresh deployment in a new project
Leave `name_suffix` empty (random suffix), `project_uuid` empty (creates a
project), and `agent_post_setup_enabled = true` (attaches KB + guardrails). Supply
`do_token`, `model_uuid`, `embedding_model_uuid`, and Spaces keys.

## Required tfvars (this deployment)

```hcl
do_token                 # DO API token
spaces_access_id         # Spaces key id
spaces_secret_key        # Spaces key secret
project_uuid             = "5382b7ad-d7d6-4785-b012-05a324d07c83"
model_uuid               = "153b8921-73c5-11f0-b074-4e013e2ddde4"
embedding_model_uuid     = "bb3ab4ee-d9b5-11f0-b074-4e013e2ddde4"
basename                 = "rag-assistant"
name_suffix              = "alpg"
region                   = "lon"     # App Platform slug
spaces_region            = "tor1"
agent_post_setup_enabled = false
agent_temperature        = 1
agent_max_tokens         = 2048
agent_k                  = 10
agent_retrieval_method   = "RETRIEVAL_METHOD_NONE"
agent_provide_citations  = false
```
