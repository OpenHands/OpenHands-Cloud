# Enterprise Replicated PR previews

This workflow publishes a temporary Replicated release for an `OpenHands/enterprise`
PR image and can optionally install that release into a full Replicated Embedded
Cluster preview VM. Infrastructure is provider-selected: `none`, `gcp`, or `aws`.

The GCP path is the preferred staging path. It mirrors the current SaaS feature
preview conventions observed in project `staging-092324`:

- region: `us-central1`
- default zone: `us-central1-a`
- network/subnetwork: `staging-core-app`
- Cloud DNS zone: `staging-all-hands-dot-dev`
- existing SaaS preview host shape: `pr-<PR>.staging.all-hands.dev`
- Replicated preview default suffix: `replicated.staging.all-hands.dev`, producing
  `pr-<PR>.replicated.staging.all-hands.dev` to avoid colliding with SaaS previews

It is intentionally opt-in. Replicated previews publish releases, create licenses,
and may run a large VM, so they should only run for PRs that need appliance-level QA.

## Workflow entry points

The workflow lives at `.github/workflows/enterprise-replicated-preview.yml` and accepts both:

- `workflow_dispatch` for manual testing and recovery
- `repository_dispatch` with event type `enterprise-replicated-preview` for an
  Enterprise-side label workflow

A dry run is the safe default. It validates naming, patches the chart in the runner
workspace, waits for the Enterprise image manifest, and builds the local Replicated
release bundle without publishing or provisioning anything.

## Required secrets and variables

For `dry_run=false`:

- `REPLICATED_APP` (optional; defaults to `openhands`)
- `REPLICATED_API_TOKEN`

For `deploy_infrastructure_provider=gcp`:

- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT`
- `ENTERPRISE_PREVIEW_ACME_EMAIL`

The GCP provider defaults to the discovered staging preview settings, but these can be overridden:

- `ENTERPRISE_PREVIEW_GCP_PROJECT_ID` (default: `staging-092324`)
- `ENTERPRISE_PREVIEW_GCP_REGION` (default: `us-central1`)
- `ENTERPRISE_PREVIEW_GCP_ZONE` (default: `us-central1-a`)
- `ENTERPRISE_PREVIEW_GCP_NETWORK` (default: `staging-core-app`)
- `ENTERPRISE_PREVIEW_GCP_SUBNETWORK` (default: `staging-core-app`)
- `ENTERPRISE_PREVIEW_GCP_DNS_ZONE` (default: `staging-all-hands-dot-dev`)
- `ENTERPRISE_PREVIEW_GCP_TF_STATE_BUCKET` for remote Terraform state

For `deploy_infrastructure_provider=aws`, the legacy AWS module still works with:

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `ENTERPRISE_PREVIEW_ROUTE53_ZONE_ID`
- `ENTERPRISE_PREVIEW_TF_STATE_BUCKET`
- `ENTERPRISE_PREVIEW_TF_STATE_LOCK_TABLE`
- `ENTERPRISE_PREVIEW_VPC_ID` and `ENTERPRISE_PREVIEW_SUBNET_ID`

Common optional settings:

- `ENTERPRISE_PREVIEW_DOMAIN_SUFFIX` defaults to `replicated.staging.all-hands.dev`
- `ENTERPRISE_PREVIEW_ALLOWED_CIDR` restricts SSH and admin-console access
- `ENTERPRISE_PREVIEW_ADMIN_PASSWORD` sets the Embedded Cluster admin-console password
- `ENTERPRISE_PREVIEW_GITHUB_TOKEN` posts a status comment back to the Enterprise PR
- `ENTERPRISE_PREVIEW_CUSTOM_LLM_BASE_URL`, `ENTERPRISE_PREVIEW_CUSTOM_LLM_API_KEY`,
  and `ENTERPRISE_PREVIEW_CUSTOM_LLM_MODELS` configure smoke-test model routing

## Manual dry-run example

Run the workflow with:

| Input | Value |
| --- | --- |
| `action` | `deploy` |
| `enterprise_pr_number` | `92` |
| `enterprise_sha` | `c23c797b11aa22bb33cc44dd55ee66ff77889900` |
| `enterprise_image_tag` | `sha-c23c797` |
| `dry_run` | `true` |
| `deploy_infrastructure_provider` | `none` |

The workflow computes these names:

| Field | Example |
| --- | --- |
| Enterprise image | `ghcr.io/openhands/enterprise-server:sha-c23c797` |
| Replicated channel | `enterprise-pr-92` |
| Replicated release version | `enterprise-pr-92-c23c797` |
| Temporary customer | `enterprise-pr-92-c23c797` |
| Preview domain | `pr-92.replicated.staging.all-hands.dev` |

It also patches the release bundle in the runner workspace so
`charts/openhands/values.yaml` points at the Enterprise PR image tag and the chart
version becomes a prerelease such as:

```text
0.35.1-enterprise-pr.92.1234
```

## Repository dispatch examples

An Enterprise-side workflow can request a dry run like this:

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
    "dry_run": "true",
    "deploy_infrastructure_provider": "none",
    "ttl_hours": "48"
  }
}
JSON
```

To publish the temporary Replicated release and install it on a GCP VM, set:

```json
{
  "dry_run": "false",
  "deploy_infrastructure_provider": "gcp"
}
```

## What a successful real GCP run does

For `dry_run=false`, the workflow:

1. Builds the Replicated release bundle with the Enterprise PR image tag.
2. Publishes a temporary Replicated release to `enterprise-pr-<PR>`.
3. Creates a temporary development customer assigned to that channel.
4. Downloads the license for headless installation.

If `deploy_infrastructure_provider=gcp`, it then:

1. Generates Terraform variables for `terraform/gcp`.
2. Provisions a Compute Engine VM, static IP, firewall rules, Cloud DNS records,
   and ACME certificates.
3. Generates a KOTS `ConfigValues` file.
4. Copies the license and config to the VM.
5. Downloads the Embedded Cluster assets for that release.
6. Runs:

   ```bash
   sudo ./openhands install \
     --license license.yaml \
     --config-values preview-config-values.yaml \
     --admin-console-password "$ADMIN_CONSOLE_PASSWORD"
   ```

The application URL is:

```text
https://app.pr-<PR>.<ENTERPRISE_PREVIEW_DOMAIN_SUFFIX>
```

With the default staging suffix, PR 92 becomes:

```text
https://app.pr-92.replicated.staging.all-hands.dev
```

## Destroy example

A closing or unlabeling workflow in `OpenHands/enterprise` should dispatch:

```json
{
  "event_type": "enterprise-replicated-preview",
  "client_payload": {
    "action": "destroy",
    "enterprise_pr_number": "92",
    "enterprise_sha": "c23c797b11aa22bb33cc44dd55ee66ff77889900",
    "dry_run": "false",
    "deploy_infrastructure_provider": "gcp"
  }
}
```

Destroy tries to remove Terraform-managed infrastructure, archive the temporary
customer, and remove the temporary channel. Configure remote Terraform state before
using real GCP previews so destroy can run from a separate workflow execution. A
scheduled sweeper should still be added before making this broadly available,
because CI jobs can be cancelled or fail between provisioning and cleanup.

## Suggested Enterprise label flow

In `OpenHands/enterprise`, add two default-branch workflows:

1. On successful Enterprise Docker workflow completion, if the PR has a
   `preview:replicated` label and is not from a fork, dispatch `deploy` with the
   PR number, head SHA, image tag, and `deploy_infrastructure_provider=gcp`.
2. On PR close or label removal, dispatch `destroy` with the PR number, last known
   SHA, and the same provider.

Keep all Replicated and cloud credentials in `OpenHands/OpenHands-Cloud`; the
Enterprise repository should only request previews for immutable image tags.
