# Secrets (do_token, spaces_access_id, spaces_secret_key) are supplied via
# TF_VAR_* environment variables, not this file. See env.sh.example.
project_uuid = "5382b7ad-d7d6-4785-b012-05a324d07c83"
basename     = "rag-assistant"
region       = "lon" # App Platform region slug

# Agent tuning.
agent_temperature       = 0.2 # grounded, low-variance answers for a CV assistant
agent_max_tokens        = 2048
agent_k                 = 10
agent_top_p             = 1
agent_retrieval_method  = "RETRIEVAL_METHOD_SUB_QUERIES" # retrieve from the KB
agent_provide_citations = true

# Guardrails to attach (captured from the original launchpad deployment). These
# are account-level guardrail definitions; null_resource.agent_guardrails wires
# them onto the agent via the API since the provider has no guardrail resource.
guardrail_jailbreak_uuid      = "4854718e-a5bd-4cb6-98ae-799b8b335086"
guardrail_content_mod_uuid    = "e8d3ebd2-3a82-46e8-8184-38ed7d06efd0"
guardrail_sensitive_data_uuid = "0a1b8aa6-ddea-46a9-9491-b756ca406c03"
