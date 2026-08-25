# Enterprise Replicated PR previews

This workflow publishes a temporary Replicated release for an `OpenHands/enterprise`
PR image and can optionally install it on a Replicated Embedded Cluster VM. GCP is
the preferred provider; AWS remains available for legacy testing.

Real previews are intentionally opt-in because they publish releases, create
Replicated customers, and run large VMs. Dry run is the default.

## Workflow entry points

`.github/workflows/enterprise-replicated-preview.yml` accepts:

- `workflow_dispatch` for manual deploy and destroy operations
- `repository_dispatch` with event type `enterprise-replicated-preview` for an
  Enterprise-side label workflow

A dry run patches the OpenHands chart to the Enterprise PR image, waits for that
image, and builds the Replicated release bundle without publishing or provisioning.

## GCP hostname and certificate layout

GCP previews use `staging.all-hands-testing.dev`. The PR number stays in the same
DNS label as each service so one pre-provisioned wildcard certificate for
`*.staging.all-hands-testing.dev` can be shared by every preview:

| Service | PR 92 hostname |
| --- | --- |
| Application | `app-pr-92.staging.all-hands-testing.dev` |
| Authentication | `auth-pr-92.staging.all-hands-testing.dev` |
| Admin console | `admin-pr-92.staging.all-hands-testing.dev:30000` |
| Runtime API | `runtime-api-pr-92.staging.all-hands-testing.dev` |
| Sandboxes | `runtime-pr-92.staging.all-hands-testing.dev/<runtime-id>` |

The installer uses Manual hostnames with path-based sandbox routing. Terraform
creates one A record per service pointing to that preview VM. It does not request
certificates, avoiding Let's Encrypt issuance limits during PR activity.

## Required secrets and variables

All real operations require:

- `REPLICATED_API_TOKEN`
- `REPLICATED_APP` (optional; defaults to `openhands`)

GCP previews require:

- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT`
- `ENTERPRISE_PREVIEW_TLS_CERTIFICATE` — shared wildcard certificate PEM
- `ENTERPRISE_PREVIEW_TLS_PRIVATE_KEY` — shared wildcard private key PEM
- `ENTERPRISE_PREVIEW_TLS_CA_CERTIFICATE` — optional issuer chain PEM
- `ENTERPRISE_PREVIEW_GCP_TF_STATE_BUCKET` — required for later destroy runs

The GCP provider has these staging defaults:

- `ENTERPRISE_PREVIEW_DOMAIN_SUFFIX`: `staging.all-hands-testing.dev`
- `ENTERPRISE_PREVIEW_GCP_PROJECT_ID`: `staging-092324`
- `ENTERPRISE_PREVIEW_GCP_REGION`: `us-central1`
- `ENTERPRISE_PREVIEW_GCP_ZONE`: `us-central1-a`
- `ENTERPRISE_PREVIEW_GCP_NETWORK`: `staging-core-app`
- `ENTERPRISE_PREVIEW_GCP_SUBNETWORK`: `staging-core-app`
- `ENTERPRISE_PREVIEW_GCP_DNS_ZONE`: `staging-all-hands-testing-dot-dev`

Optional settings include `ENTERPRISE_PREVIEW_ALLOWED_CIDR` and the custom LLM
base URL, API key, and model list.

## GitHub authentication

Every real VM preview enables GitHub authentication. Configure one GitHub App
with the following repository secrets:

- `ENTERPRISE_PREVIEW_GITHUB_CLIENT_ID`
- `ENTERPRISE_PREVIEW_GITHUB_CLIENT_SECRET`
- `ENTERPRISE_PREVIEW_GITHUB_APP_ID`
- `ENTERPRISE_PREVIEW_GITHUB_APP_SLUG`
- `ENTERPRISE_PREVIEW_GITHUB_WEBHOOK_SECRET`
- `ENTERPRISE_PREVIEW_GITHUB_PRIVATE_KEY`

The GitHub App must include each active preview's OAuth callback and webhook URL.
For PR 92 these are:

```text
https://auth-pr-92.staging.all-hands-testing.dev/realms/allhands/broker/github/endpoint
https://app-pr-92.staging.all-hands-testing.dev/integration/github/events
```

GitHub Apps support multiple callback URLs. Add the preview callback before
provisioning and remove it after cleanup when the list needs pruning.

## Manual dry run

Run the workflow with:

| Input | Value |
| --- | --- |
| `action` | `deploy` |
| `enterprise_pr_number` | `92` |
| `enterprise_sha` | `c23c797b11aa22bb33cc44dd55ee66ff77889900` |
| `enterprise_image_tag` | `sha-c23c797` |
| `dry_run` | `true` |
| `deploy_infrastructure_provider` | `none` |

The workflow computes stable per-PR channel names and an immutable release name,
then patches the chart to a prerelease version such as
`0.52.1-enterprise-pr.92.1234`.

## Repository dispatch example

```bash
gh api repos/OpenHands/OpenHands-Cloud/dispatches \
  --method POST \
  --input - <<'JSON'
{
  "event_type": "enterprise-replicated-preview",
  "client_payload": {
    "action": "deploy",
    "enterprise_pr_number": "92",
    "enterprise_sha": "c23c797b11aa22bb33cc44dd55ee66ff77889900",
    "enterprise_image_tag": "sha-c23c797",
    "dry_run": "false",
    "deploy_infrastructure_provider": "gcp"
  }
}
JSON
```

A successful GCP deploy publishes the temporary release, creates a non-expiring
development customer, provisions the VM and DNS records, and installs Embedded
Cluster with the shared TLS and GitHub App configuration. Customer expiry is not
used because extending a preview should not require synchronizing license state.

## Destroy and scheduled cleanup

Dispatch destroy with the same PR number, SHA, and provider. Remote Terraform
state is required so a separate run can remove the VM, IP, firewall, DNS records,
customer, and channel.

`.github/workflows/cleanup-enterprise-replicated-previews.yml` runs daily and
dispatches destroy for GCP preview VMs older than seven days. Manual runs default
to dry-run mode and accept a custom age. VM labels preserve the Enterprise PR and
SHA needed to reconstruct the destroy request.

## Suggested Enterprise label flow

In `OpenHands/enterprise`:

1. After the PR image builds, dispatch `deploy` when the PR has a
   `preview:replicated` label and is not from a fork.
2. On PR close or label removal, dispatch `destroy` with the PR number, last head
   SHA, and `deploy_infrastructure_provider=gcp`.

Keep Replicated, cloud, TLS, and GitHub App credentials in
`OpenHands/OpenHands-Cloud`; the Enterprise repository should only request previews
for immutable image tags.
