# Make Automations available without putting an existing installation at risk

This is the follow-up to the Enterprise policy-defaults change. Local
Automations is now enabled by default, and its app URL, request authentication,
webhook verification and event forwarding follow that choice. Explicit
`automation.enabled: false` keeps a release without the local service; a
separately managed service remains supported through `automationService.*`.

**This is a guarded feature rollout, not a storage migration.** Conversation
storage, bundled object-store enablement, PostgreSQL/MinIO persistence and volume
sizes retain their previous defaults. The chart does not silently move sessions
or automation packages into a new empty bucket.

Use Helm 3.18.4 (the repository CI baseline) or a compatible Helm 4 release.
Older Helm 3 versions are not qualified here; the guards use the `.Subcharts` context.

## New installations

An enabled service needs real prerequisites. The unconfigured chart now fails
with a configuration error instead of installing half-wired pods:

1. Configure a durable automation database. The default host is
   `<release>-postgresql`, and its bootstrap init container creates the
   `automations` database and `automation_user`. For a **fresh** bundled install,
   set `postgresql.primary.persistence.enabled: true` before installation.
   `existingClaim` only mounts a PVC when `persistence.enabled` is also true.
   Existing ephemeral databases need a separate backup/migration, not a flag flip.
   Set `automation.database.host` explicitly for a customized PostgreSQL service
   name. For an external pre-created database, set its host/port/name/user,
   password Secret and TLS mode, and `createDatabaseUser: false`. Automatic
   creation on an external database requires a permitted superuser and its
   explicit Secret reference; the chart cannot establish database privileges.
2. Set `automation.filestore.type` explicitly to `s3` or `gcs`, its bucket, and
   endpoint/identity/credentials as appropriate. Provision the bucket in advance,
   or deliberately set `createBucket: true` where the service identity may create
   it. The chart does not grant cloud IAM permissions. A MinIO/RustFS endpoint
   requires that store to be enabled and durable; it is not implicitly deployed.
3. Provision secrets **before** installing the app. `openhands-secrets` also
   defaults `automation.enabled` to true and now requires nonempty
   `config.automation_service_key`, `config.automation_webhook_secret`,
   `config.automation_git_sync_secret`, `config.automation_kv_secret`, and
   `config.automations_db_password` (the last can fall back to the existing
   PostgreSQL password). Use the existing installer/secret-manager workflow;
   never put real values in a committed example. The app and service must
   reference the same service/webhook keys. Git/KV keys protect stored encrypted
   data: retain their exact existing values on every upgrade. This chart neither
   generates nor rotates them.
4. Set `automation.openhandsApiUrl` and `automation.automationBaseUrl` to the
   public app origin when callers/sandboxes cannot resolve cluster DNS or when
   exposing webhook URLs. Their fallback is namespace-qualified in-cluster HTTP;
   it is not a public webhook endpoint. Match Replicated's convention, e.g.
   `https://app.example.com`, without appending `/api/automation`. Use the normal
   CA-bundle settings for a private CA; do not disable TLS verification.
5. Run a server-side Helm dry run after provisioning the prerequisites. For an
   existing namespace, the chart checks exact automation Secret references and
   nonempty keys before updating pods. Its non-secret API probe needs `get` on
   the namespace's `default` ServiceAccount, and Secret checks need `get` on the
   referenced Secrets. An RBAC error stops the render. Offline `helm template`
   and GitOps renderers cannot prove that Secrets exist; an empty lookup is not
   proof of readiness. Never publish rendered secret output.

See [the fresh-install example](examples/enterprise-automations.yaml). It uses
operator-provisioned S3-compatible storage and enables PostgreSQL persistence
explicitly; its host and Secret names are placeholders. Configure conversation
storage separately through the ordinary `filestore.*` values.

### Cloud-provider choices

Apply the same feature policy on all providers; only infrastructure differs:

| Provider | Package storage | Database/identity responsibilities |
| --- | --- | --- |
| AWS/EKS | `type: s3`, empty endpoint, real bucket/region | Configure the automation ServiceAccount's IRSA/Pod Identity, or an existing credentials Secret; durable RDS or persistent bundled PostgreSQL |
| Azure/AKS | An S3-compatible service with an HTTPS endpoint and existing credentials Secret | `s3` is not native Azure Blob API support; use a supported S3 endpoint or explicitly configured GCS identity. Durable Azure PostgreSQL or persistent bundled PostgreSQL |
| GCP/GKE | `type: gcs`, real bucket | Configure the automation ServiceAccount's Workload Identity and bucket access; durable Cloud SQL or persistent bundled PostgreSQL |

Both parent and automation ServiceAccounts need their own identity permissions
when they use the same cloud bucket. App `filestore.*` values are not copied into
the Automations service. All three provider docs **and their linked install
skills** must provision these inputs and pin the release together; do not claim
provider end-to-end qualification from a chart render.

## Existing installations

First save the deployed values and manifests and take recoverable backups of
the database, package bucket and encryption keys. Preserve exact storage sizes,
backends, endpoints and bucket names. Pin any formerly implicit GCS choice to
`automation.filestore.type: gcs`. Pin `automation.database.createDatabaseUser`
to its deployed behavior; use `false` for a pre-provisioned external database.
An online upgrade refuses to add privileged bootstrap to an existing service
that did not already run it. Configure both the app and secrets releases;
they are separate Helm releases and are not automatically upgraded together.

Choose either:

- **Retain the old default-off behavior:** set `automation.enabled: false` in the
  app values. For a secrets release that never provisioned Automations, also set
  `automation.enabled: false` there. For a previously active service, retain its
  existing secrets/configuration even while the app feature is disabled, so
  disabling it does not discard keys required to decrypt saved data later.
- **Enable/retain the local service:** configure its durable DB/store and all
  secrets, then set `automation.upgradeAcknowledged: true` in the app values.
  This records explicit review; it does not migrate data or skip the checks.

On an online upgrade, the chart refuses changes to the deployed Automations
DB host/port/name/user or effective package backend/bucket/endpoint. A deliberate
repointing needs a separately planned data migration. It also refuses converting
an existing shared ephemeral PostgreSQL StatefulSet to PVC storage in place.
An existing PVC mounted through `existingClaim` is durable when persistence is
enabled; lack of a StatefulSet claim template does not make it ephemeral.
No volume-size defaults change in this PR. Back up, migrate and independently
qualify any needed storage change before the feature rollout.

Critical `automation.env` connection overrides must agree with the typed
`automation.database`/`filestore` values used by bootstrap and prerequisite
checks. Update the typed values to the same effective target; do not leave the
bootstrap writing to one database while the service reads another. Other env
overrides, including explicit app-side auth/UI overrides, remain supported.

### Separately managed Automations / Cloud

Set `automation.enabled: false` and explicitly configure
`automationService.url`, `automationService.eventForwardingEnabled`,
`automationServiceKey.enabled` and `automationWebhookSecret.enabled`, with their
existing Secret references. The URL/visibility behavior introduced in the policy
PR remains intact, including the legacy app `env.AUTOMATION_SERVICE_URL` override.
No local DB/store acknowledgment is required when the local service is off.

Replicated continues to pass its saved on/off choice and explicit DB/store
settings. Its adapter acknowledges that existing managed configuration; the
online secret/data-path checks still apply. This does not turn a saved OFF
installation ON, and is not an official KOTS upgrade qualification.

### Deliberate data migration

The guard has no force/bypass flag. For a separately planned migration, quiesce
new/running automations, take recoverable DB/bucket/key backups, disable the
local service while retaining the secrets release and all data, and carry out
the reviewed migration separately. Re-enable with explicit target values and
acknowledgment only after verification. Qualify a real run and retained packages
and encrypted Git/KV data before resuming schedules. A deployment so old that it
does not advertise an explicit backend cannot be proven safe by this guard;
reconcile its historical defaults and data before following a migration plan.
This document does not perform or qualify that migration.

## Qualification still needed

Render and mocked-Kubernetes guard tests establish wiring and rejection behavior.
They do not establish live migrations, database privileges, bucket permissions,
webhook delivery, scheduled execution, sandbox callbacks, encrypted Git/KV data
continuity, inference, or production readiness. Before release, run a backed-up
upgrade and rollback on an authorized disposable installation, exercise a real
scheduled/event-driven run and retained package data, and update AWS, Azure and
GCP docs plus linked install skills together. Leave those release checks open
until that evidence exists.
