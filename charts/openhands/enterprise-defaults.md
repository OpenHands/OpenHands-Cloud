# Enterprise defaults: rollout and migration

The default Helm install should give an Enterprise administrator the same
product policy as a fresh Replicated install. Provider infrastructure still
needs configuration. This change does not turn the chart into a complete,
validated production installer.

| Setting | Before (0.93) | Proposed default (0.94) | Explicit override |
| --- | --- | --- | --- |
| Personal LLM-key editing | app fallback allows editing | `env.OH_ALLOW_USER_LLM_CONFIGURATION: "false"` | `"true"` |
| Billing page | app fallback shows page | `env.HIDE_BILLING_PAGE: "true"` | `"false"` |
| Email changes | app fallback allows changes | both email-change envs `"false"` | set both `"true"` |
| Automations service | disabled; UI can still follow app fallback | service and UI enabled together | `automation.enabled: false` |
| Automation auth and forwarding | disabled independently | follow `automation.enabled` when null | explicit true/false supported |
| Session file store | GCS, ambient identity | bundled S3-compatible MinIO | select managed GCS/S3 and disable MinIO |
| Automation package store | GCS, ambient identity | bundled MinIO, separate bucket | explicitly configure managed store |
| Bundled database and object storage | nonpersistent/off | persistent | keep managed services or explicit disposable-test values |

## What personal-key editing means

This flag hides the custom LLM provider/key editing controls. It is not a
server-side ban on using previously saved credentials, does not delete saved
keys, and does not change org membership or org creation. Organization-owned
models and credentials are separate configuration concerns.

The application currently uses this same visibility flag in the shared
personal/organization settings form. Turning it off also hides custom-key inputs
there for organization administrators. The managed-model selector remains
available. Customers requiring admins to enter custom organization credentials
through that form need a separate application change or must keep editing on.
Do not describe this chart flag as an independent user-only permission.

In Enterprise 1.82.0, disabling the legacy managed-model gateway forces personal
key editing back on because BYOK becomes the only model source. Therefore this
flag alone cannot enforce a no-personal-keys policy in gateway-free installs.
That requires a separately reviewed application policy, not another Helm flag.

The chart uses the application's plain feature-flag environment inputs and
does not introduce structured `OH_WEB_CLIENT_FEATURE_FLAGS_*` defaults. This
avoids switching the app into structured-feature-flags mode and accidentally
hiding Jira, Linear, or other integrations. If an existing overlay already sets
structured feature flags, reconcile its corresponding structured flags as well;
the chart cannot remove or supersede those explicit operator settings. Billing
page visibility and enabling billing/payment processing remain separate controls.

## Automations prerequisites

Provision the existing required app secrets and these automation secrets in the
release namespace before installing: `automation-service-key`,
`automation-webhook-secret`, `automation-db-secret`, `automation-git-sync-secret`,
and `automation-kv-secret`. Use independent, persistent random secrets and retain
them across upgrades. If using the separate `openhands-secrets` chart, explicitly
set its `automation.enabled: true` and supply the corresponding `config` inputs.
The service/webhook keys must match across the app and automation service.
Do not commit credentials or accept empty secret values.

With bundled PostgreSQL, the automation init container creates its database and
role using the existing PostgreSQL admin-secret reference. Set
`automation.database.createDatabaseUser: false` with a pre-provisioned role and
database for managed PostgreSQL, then explicitly set its host, port, SSL mode,
user, database and password-secret reference. Switching the parent database
alone does not configure the automation subchart.

The default connection URLs expand Helm release/namespace names. App dispatch
uses `http://openhands-service.<namespace>.svc.cluster.local:3000`; sandbox
completion callbacks use `http://automation.<namespace>.svc.cluster.local`.
These require cluster DNS and network reachability from sandbox pods. External
sandboxes, network policies or HTTPS-only deployments must override
`automation.openhandsApiUrl` and `automation.automationBaseUrl` with reachable,
trusted public HTTPS URLs. Route webhook/API traffic through the app ingress.
Private CAs require the existing automation CA-bundle configuration.

Bundled MinIO stores automation packages on its PVC and creates the separate
`automation-data` bucket on startup. Automation reads the generated MinIO Secret
(`rootUser`/`rootPassword`); explicit storage-secret/inline overrides still win.
For custom MinIO names or an existing MinIO credential Secret, set
`automation.filestore.existingSecret` and its two key names explicitly.
Disable `automation.filestore.bundledMinio` for external stores, clear its endpoint,
and choose the correct bucket, region and workload identity or secret. The
parent session store must be configured separately.

The existing bundled MinIO service-account credentials are development defaults.
Replace them before production and coordinate the app and runtime archive
credential references. The pinned bundled MinIO images support amd64 only.
ARM deployments need a supported external object store or a separately qualified
replacement. The bundled defaults request 100Gi for MinIO and 50Gi for PostgreSQL, matching
the fresh Replicated sizes. Helm uses the cluster StorageClass rather than
pinning Replicated's VM-specific `openebs-hostpath`.
Persistence requires a suitable StorageClass, capacity and backups;
a PVC alone does not provide high availability or disaster recovery.

## Existing installations

Before upgrading, inspect `helm get values` and the rendered manifests privately.
Do not paste secrets into tickets or logs. Capture effective policy choices and
explicitly carry them into the new values file. Do not assume `--reuse-values`
will populate new settings safely.

Do not change an existing release from GCS or external S3 to the bundled store
without a separately reviewed data migration. Keep existing endpoints, buckets,
identity and credential references. Likewise, changing PostgreSQL persistence
from emptyDir to a PVC is not a data migration and may be rejected as an immutable
StatefulSet update. Back up and plan the replacement/migration separately. Keep
existing persistence choices pinned until that work is complete.

For an existing deployment that does not use Automations, explicitly set
`automation.enabled: false` before the upgrade. Its UI, default authentication
references and event forwarding then stay off. Explicit remote-service wiring
continues to work with the local subchart disabled. Replicated now passes its
persisted Automations choice unconditionally, so disabled upgrades stay disabled.

Cloud must own an explicit overlay. Start with
[cloud-overrides.yaml](examples/cloud-overrides.yaml), replace every placeholder,
and preserve the deployed identity, database, buckets, integration flags and
billing/payment settings. This example is not an export of Cloud's live config.

## Release acceptance

Offline Helm renders/unit tests establish configuration wiring only. Before
merging, qualify a fresh Enterprise install and a backed-up upgrade, including
org-admin model setup, email-policy behavior, a scheduled automation, webhook
execution, completion callbacks, pod replacement and retained data. Confirm
Cloud's actual overlay with its owners before rolling out shared defaults.

Update the customer deployment docs AND linked install skills for AWS/EKS,
Azure/AKS and Google Cloud/GKE together. Check their pinned chart versions,
provider storage/identity examples, explicit old overrides and automation secrets.
The version-pinned VM install skill should continue to honor persisted Replicated
choices. This chart PR supplies the migration contract; it does not claim those
separate documentation repositories or all provider skills have been updated.
