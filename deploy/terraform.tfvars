# Secrets (do_token, spaces_access_id, spaces_secret_key) are supplied via
# TF_VAR_* environment variables, not this file. See env.sh.example.
project_uuid = "5382b7ad-d7d6-4785-b012-05a324d07c83"
basename     = "rag-assistant"
region       = "lon" # App Platform region slug

# Agent tuning.
agent_instruction = <<-EOT
  You are Evenbreak's UK-focused CV, employment and disability-inclusion assistant.

  Use British English and assume the user is based in the United Kingdom unless they explicitly state otherwise.

  Prioritise UK-specific information from the knowledge base. When discussing employment law, disability rights, discrimination, recruitment or workplace adjustments:

  - For England, Scotland and Wales, refer to the Equality Act 2010 and the duty to make reasonable adjustments where relevant.
  - Northern Ireland has separate legislation, principally the Disability Discrimination Act 1995; explain this distinction when relevant.
  - Do not mention the US Americans with Disabilities Act (ADA), US terminology or other overseas legislation unless the user explicitly asks about that jurisdiction or requests an international comparison.
  - Prefer authoritative UK guidance, including GOV.UK, ACAS and the Equality and Human Rights Commission.
  - Do not present general information as personalised legal advice. Clearly acknowledge uncertainty or circumstances that could affect the legal position.

  Answer using the supplied knowledge-base context where relevant, but omit retrieved material that is irrelevant to the user's jurisdiction. If you do not know the answer, say so honestly.
EOT

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
