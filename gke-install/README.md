# GKE Enterprise Helm installation

Use the [GKE install skill](../.agents/skills/gke-install.md) with
`gke-install/values.yaml.tmpl` for the version-matched workflow and provider
constraints. The skill covers context/project selection, persistent disks,
Traefik/TLS, secrets and cloud identity. Reuse an authorized evaluation and its
recorded access; this documentation does not authorize provisioning a cluster.

## Automations follow-up

Before adopting the proposed chart 0.95.0, read the
[guarded rollout guide](../charts/openhands/automations-rollout.md). Explicitly
configure GCS bucket access for the automation ServiceAccount (or a supported
S3-compatible store), durable PostgreSQL, and the existing automation Secrets.
The app's conversation bucket is separate from automation package storage.

Keep the previous tested chart pin until provider docs and the linked skill are
coordinated with release and the upgrade is qualified. A default-off baseline
checkpoint is not proof that Automations works. Required release validation
includes preserved package/DB/encryption data, a real scheduled/event-driven run
and callbacks. No storage migration or live GKE qualification is claimed here.
