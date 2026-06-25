# OpenHands-Cloud agent notes

## LLM proxy auth in runtime (agent-server)
- Runtime (agent-server) does **not** inherit app-server env vars except for prefixes `LLM_` and `LMNR_`.
- To pass other vars (e.g., `LITELLM_PROXY_API_KEY`) into runtime pods, set `OH_AGENT_SERVER_ENV` on the **app server** (Helm values `env:`), which is forwarded to the agent-server container.
- In Replicated/KOTS config (`replicated/openhands.yaml`), `OH_AGENT_SERVER_ENV` is already used for proxy/CA bundle and provider-specific keys (Azure/Vertex/Custom/Bedrock), but **does not** include `LITELLM_PROXY_API_KEY` by default.
- In testenvs, `deploy-branch.sh` now ensures `lite-llm-api-key` exists and injects it into runtime via `--set-string env.OH_AGENT_SERVER_ENV=...` (redacted in logs).
- Per-branch `litellm-helm` deployments require a namespace-local `litellm-env-secrets` (provider API keys) and a `litellm-helm.proxy_config.model_list` that references those env vars; otherwise LiteLLM serves zero models.
- LITE_LLM_API_URL in the app server should point at the LiteLLM **service name**. In branch deployments, set `litellm-helm.fullnameOverride: "<branch>-litellm"` and `litellm.url: "http://<branch>-litellm:4000"`; otherwise the default `{{ .Release.Name }}-litellm` can produce a non-existent service (e.g., `openhands-admin-dashboard-litellm`).
- LiteLLM 1.80.8 in staging does **not** recognize `minimax` as a native provider. For MiniMax M2.7, set `custom_llm_provider: anthropic` in `litellm-helm.proxy_config.model_list` and supply `MINIMAX_API_KEY` + `MINIMAX_API_BASE` (`https://api.minimax.io/anthropic/v1/messages`) in `litellm-env-secrets`. The model then appears as `prod/minimax-m2.7`.

- Admin-dashboard org usage stats regression: /app/server/services/org_conversation_service.py references storage.user.User.name, but storage.user maps to table `user` (singular) which has no `name` column; DB still has a separate `users` table with `name`, but it's not used by the service. Results in AttributeError + 500s.


