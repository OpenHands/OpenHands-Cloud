# Differences between Docker Compose and the Helm chart

Docker Compose (`docker-compose.yml` in OpenHands/enterprise) is the default OpenHands Enterprise install. This page lists every default where this chart differs from it, and why.

`scripts/check_compose_helm_defaults.py` keeps this page exact. It renders the chart with `scripts/compose_parity_values.yaml`, reads the Compose file at the chart's `appVersion` tag, and compares the app container's environment and the bundled services. It fails when a difference is not listed here, or when a listed name no longer differs.

## Product defaults

These change what a user sees or can do. Each one is either fixed or given a reason. Reason pending: the reference install is not yet agreed.

| Name | What differs | Reason |
|---|---|---|
| `OH_WEB_CLIENT_FEATURE_FLAGS_ENABLE_OAUTH_V2_LOGIN` | Compose sets structured web client flags, so the app ignores plain UI flags. Helm sets none, so it reads plain flags. | Pending |
| `OH_WEB_CLIENT_FEATURE_FLAGS_ENABLE_SUPER_ADMIN` | Same as above. | Pending |
| `OH_WEB_CLIENT_FEATURE_FLAGS_HIDE_PERSONAL_WORKSPACES` | Same as above. | Pending |
| `OH_ALLOW_USER_LLM_CONFIGURATION` | Compose: users can enter their own LLM keys. Helm: key editing is hidden (Enterprise policy default). | Pending |
| `HIDE_BILLING_PAGE` | Compose: Billing page follows the app default (shown). Helm: hidden (Enterprise policy default). | Pending |
| `EMAIL_CHANGE_ENABLED` | Compose: users can change their email. Helm: email changes are disabled (Enterprise policy default). | Pending |
| `OH_WEB_CLIENT_EMAIL_CHANGE_ENABLED` | Same as `EMAIL_CHANGE_ENABLED`, for the UI. | Pending |
| `ENABLE_AUTOMATIONS` | Helm renders `false` while the local Automations service runs. Fix pending. After the fix, both show Automations; move this row to the next section. | Pending |
| `HIDE_PERSONAL_WORKSPACES` | Compose hides personal workspaces once a user belongs to an organization. Helm shows them. | Pending |
| `ENABLE_SUPER_ADMIN` | Compose turns on the SuperAdmin dashboard. Helm leaves it off. Tracked in OHE-3694. | Pending |
| `ENABLE_INTEGRATED_IDP` | Compose uses the app's built-in password login. Helm uses Keycloak. | Pending |
| `ENABLE_LEGACY_LITELLM` | Compose: each user brings an LLM key; no LiteLLM proxy. Helm: the LiteLLM proxy is on, with budgets. | Pending |
| `RATE_LIMITING_ENABLED` | Compose turns rate limiting off. Helm leaves it on. | Pending |
| `OH_APP_CONVERSATION_SANDBOX_GROUPING_STRATEGY` | Compose shares a sandbox between conversations. Helm gives each conversation its own sandbox. | Pending |
| `ENABLE_POSTHOG_ANALYTICS` | Compose sends backend analytics to PostHog. Helm does not. | Product analytics cover the Compose evaluator funnel only (OHE-3657). Helm installs stay off. |
| `POSTHOG_CLIENT_KEY` | Compose sets a real PostHog key. Helm sets a placeholder. | Same as `ENABLE_POSTHOG_ANALYTICS`. With analytics off, Helm sends nothing, so its key is unused. |
| `service:canvas-server` | Compose bundles Agent Canvas. Helm does not (it needs an ingress host). | Pending |

## Same behaviour, different value

The values differ, but the app behaves the same.

| Name | Why the behaviour is the same |
|---|---|
| `CONVERSATION_MANAGER_CLASS` | Helm sets it; no reader found in app 1.89.0. |
| `DISABLE_WAITLIST` | Helm sets it; no reader found in app 1.89.0. |
| `DUPLICATE_EMAIL_CHECK` | Helm sets `true`, which is the app default. |
| `ENABLE_BILLING` | Compose sets `false`, which is the app default. |
| `OH_LLM_MODEL_KIND` | The enterprise-server image already sets the same value. |
| `OH_SANDBOX_MAX_NUM_SANDBOXES` | Helm sets `10`, which is the app default. |
| `OPENHANDS_CONVERSATION_VALIDATOR_CLS` | Helm sets it; no reader found in app 1.89.0. |
| `OPENHANDS_EXPERIMENT_MANAGER_CLS` | Helm sets it; no reader found in app 1.89.0. |
| `OPENHANDS_MCP_CONFIG_CLS` | Helm sets it; no reader found in app 1.89.0. |
| `SANDBOX_CLOSE_DELAY` | Helm sets it; no reader found in app 1.89.0. |
| `SANDBOX_KEEP_RUNTIME_ALIVE` | Helm sets it; no reader found in app 1.89.0. |

## Platform wiring

Helm runs on Kubernetes and Compose runs on one Docker host, so these connect different components. They do not change what a user sees.

| Name | What it wires |
|---|---|
| `AGENT_SERVER_IMAGE_REPOSITORY` | Sandbox image and user |
| `AGENT_SERVER_IMAGE_TAG` | Sandbox image and user |
| `AUTH_WEB_HOST` | Keycloak connection |
| `AUTOMATIONS_SERVICE_KEY` | Automations service connection and secrets |
| `AUTOMATION_SERVICE_URL` | Automations service connection and secrets |
| `AUTOMATION_WEBHOOK_SECRET` | Automations service connection and secrets |
| `DB_HOST` | Database connection |
| `DB_NAME` | Database connection |
| `DB_PASS` | Database connection |
| `DB_SSL_MODE` | Database connection |
| `DB_USER` | Database connection |
| `FILE_STORE` | File storage: bucket in Helm, local volume in Compose |
| `FILE_STORE_PATH` | File storage: bucket in Helm, local volume in Compose |
| `JWT_SECRET` | Secret reference |
| `KEYCLOAK_ADMIN_PASSWORD` | Keycloak connection |
| `KEYCLOAK_CLIENT_ID` | Keycloak connection |
| `KEYCLOAK_CLIENT_SECRET` | Keycloak connection |
| `KEYCLOAK_REALM_NAME` | Keycloak connection |
| `KEYCLOAK_SERVER_URL` | Keycloak connection |
| `KEYCLOAK_SMTP_PASSWORD` | Keycloak connection |
| `LAMINAR_WEB_HOST` | Public hostnames and URLs |
| `LITE_LLM_API_KEY` | LiteLLM proxy connection |
| `LITE_LLM_API_URL` | LiteLLM proxy connection |
| `LITE_LLM_TEAM_ID` | LiteLLM proxy connection |
| `LOG_JSON` | Log format for the cluster log collector |
| `LOG_JSON_LEVEL_KEY` | Log format for the cluster log collector |
| `NO_SETUP` | Sandbox image and user |
| `OH_AGENT_SERVER_ENV` | Sandbox image and user |
| `OH_RESOLVER_LABEL` | Server class and integration wiring |
| `OH_SANDBOX_SPEC_DEFAULT_SPEC_NAME` | Sandbox runtime: Kubernetes runtime API in Helm, local Docker in Compose |
| `OH_SANDBOX_SPEC_KIND` | Sandbox runtime: Kubernetes runtime API in Helm, local Docker in Compose |
| `OH_SANDBOX_WEB_URL` | Sandbox runtime: Kubernetes runtime API in Helm, local Docker in Compose |
| `OH_WEB_URL` | Public hostnames and URLs |
| `OPENHANDS_CONFIG_CLS` | Server class and integration wiring |
| `OPENHANDS_GITHUB_SERVICE_CLS` | Server class and integration wiring |
| `OPENHANDS_GITLAB_SERVICE_CLS` | Server class and integration wiring |
| `OPENHANDS_PROVIDER_BASE_URL` | LiteLLM proxy connection |
| `PERMITTED_CORS_ORIGINS` | Public hostnames and URLs |
| `PGSSLMODE` | Database connection |
| `REDIS_HOST` | Redis connection |
| `REDIS_PASSWORD` | Redis connection |
| `REDIS_PORT` | Redis connection |
| `RUNTIME` | Sandbox runtime: Kubernetes runtime API in Helm, local Docker in Compose |
| `RUNTIME_URL_PATTERN` | Sandbox runtime: Kubernetes runtime API in Helm, local Docker in Compose |
| `RUN_AS_OPENHANDS` | Sandbox image and user |
| `RUN_MIGRATIONS_ON_STARTUP` | Migrations: an init container in Helm, app startup in Compose |
| `SANDBOX_API_KEY` | Sandbox runtime: Kubernetes runtime API in Helm, local Docker in Compose |
| `SANDBOX_CONTAINER_URL_PATTERN` | Sandbox runtime: Kubernetes runtime API in Helm, local Docker in Compose |
| `SANDBOX_DOCKER_NETWORK` | Sandbox runtime: Kubernetes runtime API in Helm, local Docker in Compose |
| `SANDBOX_HOST_PORT` | Sandbox runtime: Kubernetes runtime API in Helm, local Docker in Compose |
| `SANDBOX_INTERNAL_URL` | Sandbox runtime: Kubernetes runtime API in Helm, local Docker in Compose |
| `SANDBOX_REMOTE_RUNTIME_API_TIMEOUT` | Sandbox runtime: Kubernetes runtime API in Helm, local Docker in Compose |
| `SANDBOX_REMOTE_RUNTIME_API_URL` | Sandbox runtime: Kubernetes runtime API in Helm, local Docker in Compose |
| `SANDBOX_USER_ID` | Sandbox image and user |
| `WEB_HOST` | Public hostnames and URLs |
