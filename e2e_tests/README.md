# OpenHands Cloud end-to-end tests

This directory contains the Playwright harness and release-level end-to-end tests for OpenHands Cloud. The Argo WorkflowTemplate checks out an explicit OpenHands-Cloud revision and runs these tests against the release environment supplied through `BASE_URL`.

## Requirements

- Node.js 20 or newer
- A supported Playwright browser
- An explicit non-production or intentionally selected release target
- Dedicated test-account credentials or a pre-generated Playwright storage state

## Install

```bash
cd e2e_tests
npm ci
npx playwright install chromium
```

## Run

`BASE_URL` is required; the harness has no default deployment target.

```bash
BASE_URL=https://release-under-test.example.test npm run test:chromium
```

Useful checks:

```bash
npm run lint
BASE_URL=https://release-under-test.example.test \
  npx playwright test tests/example.spec.ts --list
```

## ReportPortal

ReportPortal reporting is disabled unless `REPORTPORTAL_ENABLED=true`. When it
is enabled, the harness keeps the existing Playwright reporters and also uploads
test results, steps, traces, videos, screenshots, and other Playwright
attachments through `@reportportal/agent-js-playwright`.

Required variables:

- `REPORTPORTAL_ENDPOINT`: the full ReportPortal API endpoint, preferably ending
  in `/api/v2` for asynchronous reporting;
- `REPORTPORTAL_PROJECT`: the destination project name;
- `REPORTPORTAL_API_KEY`: the reporter credential, supplied only through a
  secret; and
- `REPORTPORTAL_ENVIRONMENT`: the release environment attached to each launch.

Optional launch metadata:

- `REPORTPORTAL_LAUNCH` defaults to `OpenHands Cloud E2E`;
- `REPORTPORTAL_REVISION` identifies the exact tested release commit or tag; and
- `REPORTPORTAL_WORKFLOW` identifies the Argo Workflow run.

Do not pass Playwright's `--reporter` CLI option in Argo. That option replaces
this configured reporter array and would silently disable ReportPortal uploads.

## Authentication

The setup project runs before browser tests and writes `fixtures/auth.json`. That file contains session data, is ignored by Git, and must never be committed.

Supported authentication modes:

- `AUTH_METHOD=github` uses `GITHUB_TEST_USERNAME`, `GITHUB_TEST_PASSWORD`, and optional `GITHUB_TEST_TOTP_SECRET`.
- `AUTH_METHOD=keycloak` uses `KEYCLOAK_USERNAME` and `KEYCLOAK_PASSWORD`.
- `AUTH_METHOD=skip` requires `fixtures/auth.json` to exist before Playwright starts.

Tests that verify isolation between users require a second storage-state file through `SECONDARY_AUTH_STATE`. The Argo workflow must mount that file separately from the primary state.

## Argo execution contract

The release workflow is responsible for:

1. Checking out the exact OpenHands-Cloud release commit or tag.
2. Setting `BASE_URL` to the environment created from that release.
3. Supplying credentials and explicit `TEST_*` fixture values without logging them.
4. Running from `/workspace/e2e_tests` with locked dependencies.
5. Supplying the ReportPortal settings and API-key Secret when reporting is
   enabled. Argo retains failed pod logs for failures before Playwright starts.

The harness intentionally does not infer a deployment from a branch name or default to a hosted environment.

## Layout

```text
e2e_tests/
├── fixtures/          # Generated or mounted authentication state
├── pages/             # Shared page objects
├── tests/             # Setup and independent Playwright specs
├── utils/             # Shared test helpers
├── package-lock.json  # Locked Node dependencies
├── playwright.config.ts
└── tsconfig.json
```
