# Automated staging chart bumps

When release-please publishes a stable `openhands` chart tag, this repo now
notifies `OpenHands/saas-deploy` so staging can receive the new chart through a
normal pull request.

The sender lives in
[`.github/workflows/publish-release-charts.yml`](../.github/workflows/publish-release-charts.yml):

1. An `openhands/<version>` tag push publishes
   `ghcr.io/openhands/helm-charts/openhands:<version>`.
2. After that publish job succeeds, the `dispatch-staging-chart-bump` job runs.
3. The job mints a token for the dedicated staging-chart dispatcher GitHub App.
4. The job sends `repository_dispatch` to `OpenHands/saas-deploy` with
   `event_type: bump-chart-to-staging`.

Only `openhands/*` tags dispatch to staging. Other released charts still publish
to GHCR but do not ask `saas-deploy` for a staging PR.

## Dispatch Payload

The payload sent to `saas-deploy` is:

| Field | Value |
| ----- | ----- |
| `chart` | `openhands` |
| `version` | The chart version from the tag, for example `0.8.3`. |
| `environment` | `staging` |
| `source-repo` | The publishing repository, currently `OpenHands/OpenHands-Cloud`. |
| `source-sha` | The commit checked out at the release tag. |

The receiver treats this payload as provenance and requested state, not as an
authorization boundary. Authorization comes from GitHub's
`repository_dispatch.sender.login`, which must be the dedicated dispatcher App's
bot account on the `saas-deploy` side.

## Receiver Responsibilities

`OpenHands/saas-deploy` owns the actual staging edit. Its receiver workflow must:

- validate the sender bot and payload before minting its own write token;
- reject anything except `chart=openhands` and `environment=staging`;
- compare SemVer to avoid stale dispatch downgrades;
- bump every staging wrapper that depends on the `openhands` OCI chart;
- run `helm dependency update` for each changed wrapper so `Chart.lock` stays in
  sync with `Chart.yaml`;
- open or update the rolling staging PR for human merge.

This repo deliberately does not edit `saas-deploy` directly. It only dispatches
after GHCR has the stable chart package available for the receiver to relock.

## Prerequisites

Create a generic chart-bump dispatcher GitHub App with
[`scripts/create_chart_bump_dispatcher`](../scripts/create_chart_bump_dispatcher/):

```bash
uv run scripts/create_chart_bump_dispatcher/create_chart_bump_dispatcher.py \
  --org OpenHands \
  --app-name staging-chart-bump-dispatcher
```

Install it only on `OpenHands/saas-deploy`. The generated App has
`contents: write` and `metadata: read`, with no pull-request permission,
workflow permission, webhook events, or OAuth-on-install flow. Do not add it to
`saas-deploy` ruleset bypass actors.

Store these secrets for this repository in the `staging-chart-bump-dispatcher`
environment:

- `STAGING_CHART_BUMP_DISPATCHER_APP_ID`
- `STAGING_CHART_BUMP_DISPATCHER_APP_PRIVATE_KEY`

The environment must allow `openhands/*` tag-triggered runs. A branch-only
environment, such as one restricted to `main`, will hide the secrets from this
workflow because stable chart publishes run from tags.
