# Automated e2e test revision bumps

`OpenHands/saas-deploy` runs the Playwright suite in `e2e_tests/` from a pinned
commit of this repository. When a change under `e2e_tests/` lands on `main`, this
repo notifies `saas-deploy` so that pin can move without anyone editing it by
hand.

The sender lives in
[`.github/workflows/dispatch-e2e-test-revision-bump.yml`](../.github/workflows/dispatch-e2e-test-revision-bump.yml):

1. A push to `main` touching `e2e_tests/**` starts the workflow.
2. The job mints a token for the dedicated e2e test revision dispatcher GitHub
   App.
3. The job sends `repository_dispatch` to `OpenHands/saas-deploy` with
   `event_type: bump-e2e-test-revision`.

`saas-deploy` owns the edit. Its receiver opens or updates one rolling pull
request that moves the pin to the newest revision and queues auto-merge on it.
Merging that pull request is what decides the revision the e2e runners use.

After dispatching, the sender polls the `openhands-e2e` Argo WorkflowTemplate
until its `test-revision` is the pushed commit (up to 45 minutes), then starts
an unstable E2E run through
[`e2e-replicated.yml`](../.github/workflows/e2e-replicated.yml). A newer push
cancels an older wait, so only the newest revision gets a run. If the pin never
arrives, the job fails and points at the bump pull request.

The wait reads the template with the repo-level, read-only
`ARGO_WORKFLOWS_GATE_TOKEN` (the `openhands-e2e-gate` identity in
`saas-deploy`), not the environment-scoped `ARGO_WORKFLOWS_E2E_TOKEN`. The
submit identity's template `get` is scoped to the template name, and Argo
Server checks this read without a name, so it is always denied. A 401, 403, or
404 fails the job at once rather than after 45 minutes.

## Dispatch payload

| Field | Value |
| ----- | ----- |
| `revision` | The commit pushed to `main`, as a full 40-character SHA. |
| `environment` | `development` |
| `source-repo` | `OpenHands/OpenHands-Cloud` |

The receiver treats the payload as requested state and provenance, not as its
authorization boundary. Authorization comes from the dispatcher's GitHub App
identity on the `saas-deploy` side.

## Trust boundary and prerequisites

Create the dispatcher with the generic App helper:

```bash
uv run scripts/create_chart_bump_dispatcher/create_chart_bump_dispatcher.py \
  --org OpenHands \
  --app-name e2e-test-revision-bump-dispatcher
```

Install the App only on `OpenHands/saas-deploy`. It needs only `contents: write`
and `metadata: read`, and it must not be a `saas-deploy` ruleset bypass actor.
The receiver validates both the bot login and its numeric user ID before minting
any write token, so record the ID when the App is created.

Store these environment-scoped secrets in the
`e2e-test-revision-bump-dispatcher` GitHub Environment:

- `E2E_TEST_REVISION_BUMP_DISPATCHER_APP_ID`
- `E2E_TEST_REVISION_BUMP_DISPATCHER_APP_PRIVATE_KEY`

Restrict that Environment to `main`. This sender is branch-triggered, so a branch
policy keeps the credentials out of reach of runs from any other ref. The chart
bump dispatchers cannot do this because they run from tags.
