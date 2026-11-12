# Differences between Docker Compose and the Helm chart

Docker Compose (`docker-compose.yml` in OpenHands/enterprise) is the default OpenHands Enterprise install. This page lists every default where this chart differs from it, and why.

`scripts/check_compose_helm_defaults.py` keeps this page exact. It renders the chart with `scripts/compose_parity_values.yaml`, reads the Compose file at the chart's `appVersion` tag, and compares the app container's environment and the bundled services. It fails when a difference is not listed here, or when a listed name no longer differs.

## Defaults that differ

These change what a user sees or can do. Each one is either fixed in Helm or given a reason here.

| Name | What differs | Reason |
|---|---|---|
| `OH_WEB_CLIENT_FEATURE_FLAGS_ENABLE_OAUTH_V2_LOGIN` | Compose sets structured web client flags, so the app ignores plain UI flags. Helm sets none, so it reads plain flags. | Compose uses the built-in password login. Helm signs in through Keycloak. |
| `OH_WEB_CLIENT_FEATURE_FLAGS_ENABLE_SUPER_ADMIN` | Same as above. | Turning on the SuperAdmin dashboard in Helm is tracked in OHE-3694. |
| `OH_WEB_CLIENT_FEATURE_FLAGS_HIDE_PERSONAL_WORKSPACES` | Same as above. | Pending |
| `OH_ALLOW_USER_LLM_CONFIGURATION` | Compose: users can enter their own LLM keys. Helm: key editing is hidden (Enterprise policy default). | Enterprise policy for production installs, which matches Replicated. |
| `HIDE_BILLING_PAGE` | Compose: Billing page follows the app default (shown). Helm: hidden (Enterprise policy default). | Enterprise policy for production installs, which matches Replicated. |
| `EMAIL_CHANGE_ENABLED` | Compose: users can change their email. Helm: email changes are disabled (Enterprise policy default). | Enterprise policy for production installs, which matches Replicated. |
| `OH_WEB_CLIENT_EMAIL_CHANGE_ENABLED` | Same as `EMAIL_CHANGE_ENABLED`, for the UI. | Enterprise policy for production installs, which matches Replicated. |
| `HIDE_PERSONAL_WORKSPACES` | Compose hides personal workspaces once a user belongs to an organization. Helm shows them. | Pending |
| `ENABLE_SUPER_ADMIN` | Compose turns on the SuperAdmin dashboard. Helm leaves it off. Tracked in OHE-3694. | Turning on the SuperAdmin dashboard in Helm is tracked in OHE-3694. |
| `ENABLE_INTEGRATED_IDP` | Compose uses the app's built-in password login. Helm uses Keycloak. | Production installs sign in through Keycloak for SSO. Compose uses a built-in login for one evaluator. |
| `ENABLE_LEGACY_LITELLM` | Compose: each user brings an LLM key; no LiteLLM proxy. Helm: the LiteLLM proxy is on, with budgets. | Production installs use the LiteLLM proxy for managed models and budgets. Compose has each user bring a key. |
| `RATE_LIMITING_ENABLED` | Compose turns rate limiting off. Helm leaves it on. | Production installs keep rate limiting on. Compose turns it off for one evaluator. |
| `OH_APP_CONVERSATION_SANDBOX_GROUPING_STRATEGY` | Compose shares a sandbox between conversations. Helm gives each conversation its own sandbox. | Kubernetes runs each conversation in its own sandbox. Compose shares sandboxes to save resources on one host. |
| `ENABLE_POSTHOG_ANALYTICS` | Compose sends backend analytics to PostHog. Helm does not. | Product analytics cover the Compose evaluator funnel only (OHE-3657). Helm installs stay off. |
| `POSTHOG_CLIENT_KEY` | Compose sets a real PostHog key. Helm sets a placeholder. | Same as `ENABLE_POSTHOG_ANALYTICS`. With analytics off, Helm sends nothing, so its key is unused. |
| `service:canvas-server` | Compose bundles Agent Canvas. Helm does not (it needs an ingress host). | Agent Canvas needs an ingress host in Helm, so it cannot run with no input. |

## Not compared

These names differ in the rendered environment, but not in what a user sees. The check still requires each one to be listed here, so a new variable is never missed.

### Same behaviour, different value

- Both show Automations. Helm sets `true` because the bundled service has a URL. Compose leaves it unset, and the app default is `true`: `ENABLE_AUTOMATIONS`
- Helm sets it; no reader found in app 1.89.0: `CONVERSATION_MANAGER_CLASS`, `DISABLE_WAITLIST`, `OPENHANDS_CONVERSATION_VALIDATOR_CLS`, `OPENHANDS_EXPERIMENT_MANAGER_CLS`, `OPENHANDS_MCP_CONFIG_CLS`, `SANDBOX_CLOSE_DELAY`, `SANDBOX_KEEP_RUNTIME_ALIVE`
- Helm sets `true`, which is the app default: `DUPLICATE_EMAIL_CHECK`
- Compose sets `false`, which is the app default: `ENABLE_BILLING`
- The enterprise-server image already sets the same value: `OH_LLM_MODEL_KIND`
- Helm sets `10`, which is the app default: `OH_SANDBOX_MAX_NUM_SANDBOXES`

### Platform wiring

Helm runs on Kubernetes and Compose runs on one Docker host, so these connect different components.

- Sandbox image and user: `AGENT_SERVER_IMAGE_REPOSITORY`, `AGENT_SERVER_IMAGE_TAG`, `NO_SETUP`, `OH_AGENT_SERVER_ENV`, `RUN_AS_OPENHANDS`, `SANDBOX_USER_ID`
- Keycloak connection: `AUTH_WEB_HOST`, `KEYCLOAK_ADMIN_PASSWORD`, `KEYCLOAK_CLIENT_ID`, `KEYCLOAK_CLIENT_SECRET`, `KEYCLOAK_REALM_NAME`, `KEYCLOAK_SERVER_URL`, `KEYCLOAK_SMTP_PASSWORD`
- Automations service connection and secrets: `AUTOMATIONS_SERVICE_KEY`, `AUTOMATION_SERVICE_URL`, `AUTOMATION_WEBHOOK_SECRET`
- Database connection: `DB_HOST`, `DB_NAME`, `DB_PASS`, `DB_SSL_MODE`, `DB_USER`, `PGSSLMODE`
- File storage: bucket in Helm, local volume in Compose: `FILE_STORE`, `FILE_STORE_PATH`
- Secret reference: `JWT_SECRET`
- Public hostnames and URLs: `LAMINAR_WEB_HOST`, `OH_WEB_URL`, `PERMITTED_CORS_ORIGINS`, `WEB_HOST`
- LiteLLM proxy connection: `LITE_LLM_API_KEY`, `LITE_LLM_API_URL`, `LITE_LLM_TEAM_ID`, `OPENHANDS_PROVIDER_BASE_URL`
- Log format for the cluster log collector: `LOG_JSON`, `LOG_JSON_LEVEL_KEY`
- Server class and integration wiring: `OH_RESOLVER_LABEL`, `OPENHANDS_CONFIG_CLS`, `OPENHANDS_GITHUB_SERVICE_CLS`, `OPENHANDS_GITLAB_SERVICE_CLS`
- Sandbox runtime: Kubernetes runtime API in Helm, local Docker in Compose: `OH_SANDBOX_SPEC_DEFAULT_SPEC_NAME`, `OH_SANDBOX_SPEC_KIND`, `OH_SANDBOX_WEB_URL`, `RUNTIME`, `RUNTIME_URL_PATTERN`, `SANDBOX_API_KEY`, `SANDBOX_CONTAINER_URL_PATTERN`, `SANDBOX_DOCKER_NETWORK`, `SANDBOX_HOST_PORT`, `SANDBOX_INTERNAL_URL`, `SANDBOX_REMOTE_RUNTIME_API_TIMEOUT`, `SANDBOX_REMOTE_RUNTIME_API_URL`
- Redis connection: `REDIS_HOST`, `REDIS_PASSWORD`, `REDIS_PORT`
- Migrations: an init container in Helm, app startup in Compose: `RUN_MIGRATIONS_ON_STARTUP`
