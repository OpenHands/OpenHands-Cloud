# Automated development chart bumps

When release-please publishes a stable `openhands` chart tag, this repository
notifies `OpenHands/saas-deploy` so the new chart can deploy to development
without waiting for a chart-bump pull request.

The sender lives in
[`.github/workflows/publish-release-charts.yml`](../.github/workflows/publish-release-charts.yml):

1. An `openhands/<version>` tag created by `openhands-release-bot[bot]` publishes
   `ghcr.io/openhands/helm-charts/openhands:<version>`.
2. After the publish job succeeds, the `dispatch-development-chart-bump` job
   mints a token for the dedicated development dispatcher GitHub App.
3. The job sends `repository_dispatch` to `OpenHands/saas-deploy` with
   `event_type: bump-chart-to-development` and `environment=development`.

The development and staging dispatch jobs are independent siblings. A failure
in one environment does not prevent the other dispatch from running.

## Dispatch payload

The payload sent to `saas-deploy` is:

| Field | Value |
| ----- | ----- |
| `chart` | `openhands` |
| `version` | The chart version from the tag, for example `0.21.0`. |
| `environment` | `development` |
| `source-repo` | `OpenHands/OpenHands-Cloud` |
| `source-sha` | The commit checked out at the release tag. |

The receiver treats the payload as requested state and provenance, not as its
authorization boundary. Authorization comes from the dedicated dispatcher's
GitHub App identity on the `saas-deploy` side.

## Trust boundary and prerequisites

Create the dispatcher with the generic App helper:

```bash
uv run scripts/create_chart_bump_dispatcher/create_chart_bump_dispatcher.py \
  --org OpenHands \
  --app-name dev-chart-bump-dispatcher
```

Install the App only on `OpenHands/saas-deploy`. It needs only `contents: write`
and `metadata: read`, and it must not be a `saas-deploy` ruleset bypass actor.

Store these environment-scoped secrets in the `dev-chart-bump-dispatcher`
GitHub Environment:

- `CHART_BUMP_DISPATCHER_APP_ID`
- `CHART_BUMP_DISPATCHER_APP_PRIVATE_KEY`

The Environment must allow the `openhands/*` tag pattern. Before merging the
sender, also add an active tag ruleset that protects `openhands/*` creation,
updates, and deletion, with the release App as the intended bypass actor. The
expected release identity is `openhands-release-bot[bot]` (GitHub user ID
`290150379`). The sender checks both values as defense in depth, but a workflow
check cannot protect secrets from workflow code on an untrusted tag.

The `bump-chart-to-development` receiver must already exist on the default
branch of `OpenHands/saas-deploy`. GitHub accepts dispatches only for event types
handled by a workflow on the target repository's default branch.

## Failure behavior

A successful sender run means GitHub accepted the dispatch; it does not mean
the `saas-deploy` receiver committed the bump or Argo CD deployed the chart.
Receiver and deployment failures remain visible in `OpenHands/saas-deploy`.

If token minting or `gh api` fails, this publish workflow is red and no dispatch
is sent. Rerun the failed development dispatch job after fixing the credential
or API problem; the chart does not need to be republished. Duplicate dispatches
are safe because the receiver compares chart freshness and converges the same
requested version idempotently.
