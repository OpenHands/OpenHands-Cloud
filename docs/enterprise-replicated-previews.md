# Enterprise Replicated PR previews

This workflow publishes a temporary Replicated release for an `OpenHands/enterprise`
PR image and can optionally install that release into a full Replicated Embedded
Cluster preview on AWS.

It is intentionally opt-in. Replicated previews publish releases, create
licenses, and may run a large EC2 instance, so they should only run for PRs that
need appliance-level QA.

## Workflow entry points

The workflow lives at `.github/workflows/enterprise-replicated-preview.yml` and
accepts both:

- `workflow_dispatch` for manual testing and recovery
- `repository_dispatch` with event type `enterprise-replicated-preview` for an
  Enterprise-side label workflow

A dry run is the safe default. It validates naming, patches the chart in the
runner workspace, waits for the Enterprise image manifest, and builds the local
Replicated release bundle without publishing or provisioning anything.

## Required secrets and variables

For `dry_run=false`:

- `REPLICATED_APP` (optional; defaults to `openhands`)
- `REPLICATED_API_TOKEN`

For `deploy_infrastructure=true`:

- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `ENTERPRISE_PREVIEW_DOMAIN_SUFFIX`, for example `replicated-preview.example.com`
- `ENTERPRISE_PREVIEW_ROUTE53_ZONE_ID`
- `ENTERPRISE_PREVIEW_ACME_EMAIL`

Recommended optional settings:

- `ENTERPRISE_PREVIEW_TF_STATE_BUCKET` for remote Terraform state
- `ENTERPRISE_PREVIEW_TF_STATE_LOCK_TABLE` for Terraform state locking
- `ENTERPRISE_PREVIEW_VPC_ID` and `ENTERPRISE_PREVIEW_SUBNET_ID` to reuse a
  preview VPC instead of creating one per PR
- `ENTERPRISE_PREVIEW_ALLOWED_CIDR` to restrict SSH and admin-console access
- `ENTERPRISE_PREVIEW_ADMIN_PASSWORD` for the Embedded Cluster admin console
- `ENTERPRISE_PREVIEW_GITHUB_TOKEN` to post a status comment back to the
  Enterprise PR
- `ENTERPRISE_PREVIEW_CUSTOM_LLM_BASE_URL`, `ENTERPRISE_PREVIEW_CUSTOM_LLM_API_KEY`,
  and `ENTERPRISE_PREVIEW_CUSTOM_LLM_MODELS` for smoke-test model routing

## Manual dry-run example

Run the workflow with:

| Input | Value |
| --- | --- |
| `action` | `deploy` |
| `enterprise_pr_number` | `92` |
| `enterprise_sha` | `c23c797b11aa22bb33cc44dd55ee66ff77889900` |
| `enterprise_image_tag` | `sha-c23c797` |
| `dry_run` | `true` |
| `deploy_infrastructure` | `false` |

The workflow computes these names:

| Field | Example |
| --- | --- |
| Enterprise image | `ghcr.io/openhands/enterprise-server:sha-c23c797` |
| Replicated channel | `enterprise-pr-92` |
| Replicated release version | `enterprise-pr-92-c23c797` |
| Temporary customer | `enterprise-pr-92-c23c797` |
| Preview domain | `pr-92.<ENTERPRISE_PREVIEW_DOMAIN_SUFFIX>` |

It also patches the release bundle in the runner workspace so
`charts/openhands/values.yaml` points at the Enterprise PR image tag and the
chart version becomes a prerelease such as:

```text
0.35.1-enterprise-pr.92.1234
```

## Repository dispatch example

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
    "deploy_infrastructure": "false",
    "ttl_hours": "48"
  }
}
JSON
```

For a real preview, set `dry_run` to `false`. To install the appliance as well
as publish the temporary release, set both `dry_run=false` and
`deploy_infrastructure=true`.

## What a successful real run does

For `dry_run=false`, the workflow:

1. Builds the Replicated release bundle with the Enterprise PR image tag.
2. Publishes a temporary Replicated release to `enterprise-pr-<PR>`.
3. Creates a temporary development customer assigned to that channel.
4. Downloads the license for headless installation.

If `deploy_infrastructure=true`, it then:

1. Generates Terraform variables for `terraform/aws`.
2. Provisions an EC2 instance, DNS records, and certificates.
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
    "deploy_infrastructure": "true"
  }
}
```

Destroy tries to remove Terraform-managed infrastructure, archive the temporary
customer, and remove the temporary channel. A scheduled sweeper should still be
added before making this broadly available, because CI jobs can be cancelled or
fail between provisioning and cleanup.

## Suggested Enterprise label flow

In `OpenHands/enterprise`, add two default-branch workflows:

1. On successful Enterprise Docker workflow completion, if the PR has a
   `preview:replicated` label and is not from a fork, dispatch `deploy` with the
   PR number, head SHA, and image tag.
2. On PR close or label removal, dispatch `destroy` with the PR number and last
   known SHA.

Keep all Replicated and AWS credentials in `OpenHands/OpenHands-Cloud`; the
Enterprise repository should only request previews for immutable image tags.
