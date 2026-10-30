# Give Enterprise administrators consistent starting options

A Helm install should start with the same everyday product choices as a fresh
Replicated Enterprise install. This PR aligns personal LLM-key editing, Billing
page visibility and email changes. Cloud can explicitly override these choices.

| Setting | Previous Helm behavior | Enterprise default | Override |
| --- | --- | --- | --- |
| Personal LLM-key editing | app fallback allows editing | `env.OH_ALLOW_USER_LLM_CONFIGURATION: "false"` | `"true"` |
| Billing page | app fallback shows page | `env.HIDE_BILLING_PAGE: "true"` | `"false"` |
| Email changes | app fallback allows changes | `env.EMAIL_CHANGE_ENABLED` and `env.OH_WEB_CLIENT_EMAIL_CHANGE_ENABLED`: `"false"` | set both `"true"` |
| Automations visibility | app fallback can show UI without a service | follows enabled local subchart OR non-empty `automationService.url` | `env.ENABLE_AUTOMATIONS` remains an explicit override |

Billing page visibility is separate from enabling billing or requiring payment.
This PR changes visibility only. Existing organization configuration and stored
credentials are not rewritten.

## Personal keys and organization-owned credentials

The personal-key flag hides editing controls; it is not a server-side ban on
using saved personal credentials. It does not delete keys or change organization
membership or creation.

In Enterprise 1.82.0, the shared personal/organization form uses this same flag
for both custom-key inputs AND the organization-owned provider-connection
selector. Turning it off therefore prevents org admins from entering custom
credentials or linking a provider connection in that form too. The managed-model
dropdown remains available. Independently allowing org-admin credential setup
while hiding personal-key editing requires an application change.

Disabling the legacy managed-model gateway forces personal-key editing back on
in 1.82.0, because BYOK becomes the only model source. This chart flag alone
cannot enforce a no-personal-keys policy for gateway-free installs.

## Overrides and existing installs

Explicit `env` overrides win without duplicate environment names. See the
[Cloud policy override example](examples/cloud-overrides.yaml). Before upgrading,
privately inspect saved values and pin any policy choices the installation
intends to retain. Omitted values pick up the new defaults. Do not paste secrets
from rendered manifests or values into tickets or logs.

The chart adds no structured `OH_WEB_CLIENT_FEATURE_FLAGS_*` defaults. Operators
already using structured feature flags must reconcile the corresponding
structured values too, including `ALLOW_USER_LLM_CONFIGURATION`,
`HIDE_BILLING_PAGE` and `ENABLE_AUTOMATIONS`; plain inputs do not override that
mode. `OH_WEB_CLIENT_EMAIL_CHANGE_ENABLED` is a top-level web-client field, not
one of those feature flags.

A separately deployed Automations service keeps its UI when
`automationService.url` is configured with the local subchart disabled. The
effective `env.AUTOMATION_SERVICE_URL` override is also respected, including an
explicit empty override that clears the typed URL. Preserve
its existing authentication, webhook, event-forwarding and callback settings.
If an overlay intentionally exposes the UI before a service URL is configured,
retain `env.ENABLE_AUTOMATIONS: "true"` explicitly. With no local or external
service configured, the chart now hides the UI. Replicated passes its persisted
Automations choice into both its plain and structured flags, so saved OFF stays
OFF and saved ON retains its existing complete wiring.

## Separate Automations and storage rollout

Automations remains default-off in this Helm chart. Its existing install-time
configuration and companion secret provisioning are still required when enabled.
The planned Enterprise-first Automations default needs a separate change with
matching secrets, callback/storage/database wiring and upgrade safeguards.

This PR does not change session/package store types, MinIO enablement, volume
sizes, persistence, database bootstrap or the companion secrets chart defaults.
This avoids redirecting existing GCS users to an empty store, changing immutable
StatefulSet volume templates, or adding required secrets to existing app pods.
It does not improve the pre-existing storage defaults; installations must still
configure production durability and credentials appropriately.

## Documentation and release qualification

Update AWS/EKS, Azure/AKS and GCP/GKE customer docs AND their linked install skills
with these policy defaults and the chart version when releasing. Verify explicit
old policy overrides and managed-service setup in each provider's examples. These
separate documentation repositories and install skills are follow-up work, not
claimed updated by this chart PR. Keep the version-pinned VM install guidance
consistent with its persisted Replicated options.

Offline chart tests establish rendered configuration, not running app behavior.
Before release, validate the upgraded settings UI, org-admin model setup and
email policy in a representative Enterprise install. A live cluster upgrade and
Helm 3/CI qualification remain release checks.
