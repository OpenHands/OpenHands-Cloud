# OpenHands Cloud end-to-end tests

This directory contains the Playwright harness and release-level end-to-end tests for OpenHands Cloud. The Argo WorkflowTemplate checks out an explicit OpenHands-Cloud revision and runs these tests against the release environment supplied through `BASE_URL`.

## Requirements

- Node.js 20 or newer
- A supported Playwright browser
- An explicit non-production or intentionally selected release target
- Three credential sets: Keycloak admin, Returning User (GitHub), and New User (GitHub) — or pre-generated Playwright storage states for the two user roles

> **Opt-in roles:** Each user role is enabled by setting its `*_GITHUB_USERNAME` env var. Leave either unset (or empty) to skip that role entirely — its setup project, Keycloak cleanup, and test projects are excluded from the run. This is useful for fresh clusters (e.g. a spun-up test cluster with no existing users) where the "returning" path doesn't apply, or where only one role is relevant.

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
  npx playwright test tests/home.spec.ts --list
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

OpenHands Cloud uses Keycloak as its identity provider, federating identities from GitHub. The harness exercises the same spec suite under two user roles so both the "returning user" and "brand-new user" paths are covered in every run.

### Credential roles

Three credential sets are required before any test run:

| Role | Provider | Credentials | Purpose |
| --- | --- | --- | --- |
| Keycloak admin | Keycloak | username + password | Administers Keycloak; deletes the New User before each run |
| Returning User | GitHub | username + password + optional TOTP secret | A user whose OpenHands account already exists |
| New User | GitHub | username + password + optional TOTP secret | A user whose OpenHands account is deleted at the start of the run so they get a fresh account (and a fresh user id) on next login |

The New User's Keycloak account is identified by email. At the start of a run, the Keycloak admin logs in and deletes any existing user whose email matches `KEYCLOAK_NEW_USER_EMAIL`. The New User then logs in via GitHub and is provisioned from scratch, exercising the onboarding path.

### Environment variables

Deployment:

- `BASE_URL` (required) — release environment under test.

Keycloak admin (cleanup):

- `KEYCLOAK_REALM` — realm to administer (default: `allhands`).
- `KEYCLOAK_ADMIN_USERNAME` — admin username.
- `KEYCLOAK_ADMIN_PASSWORD` — admin password.
- `KEYCLOAK_NEW_USER_EMAIL` — email of the New User to delete.

The Keycloak server URL is derived from `BASE_URL` by prefixing the subdomain with `auth.` (e.g. `https://staging.all-hands.dev` → `https://auth.staging.all-hands.dev`).

Returning User (GitHub):

- `RETURNING_GITHUB_USERNAME` — **required to enable this role**; leave unset to skip the Returning User entirely.
- `RETURNING_GITHUB_PASSWORD` — required when the role is enabled.
- `RETURNING_GITHUB_TOTP_SECRET` (optional) — 2FA secret.

New User (GitHub):

- `NEW_GITHUB_USERNAME` — **required to enable this role**; leave unset to skip the New User (and Keycloak cleanup) entirely.
- `NEW_GITHUB_PASSWORD` — required when the role is enabled.
- `NEW_GITHUB_TOTP_SECRET` (optional) — 2FA secret.

Test fixtures (optional overrides):

- `TEST_REPO_URL` — repo used in conversations (default: `https://github.com/OpenHands/deploy`).
- `TEST_PROMPT` — prompt used in conversations (default: `Flip a coin!`).
- `TEST_ENV` — label for the environment.

### Storage-state files

Each role has its own setup project that logs in via GitHub and writes a Playwright storage-state file (session cookies + localStorage). These files contain session data, are ignored by Git, and must never be committed.

- `fixtures/auth.returning.json` — produced by `setup:returning`.
- `fixtures/auth.new-user.json` — produced by `setup:new-user`.

### Project topology

```text
keycloak-cleanup ──▶ setup:new-user
setup:returning

chromium:returning ──▶ setup:returning
chromium:new-user  ──▶ setup:new-user
(and firefox / webkit variants)
```

Every `*.spec.ts` file is picked up by both the `:returning` and `:new-user` variants of each browser, so the same suite runs once per user role. Specs read the active role via `runUser(testInfo)` (see `utils/config.ts`), which resolves Playwright project metadata (`project.metadata.user`) and falls back to the `AUTH_RUN_USER` env var for ad-hoc single-spec runs.

### Auth behavior: `AUTH_METHOD`

- Default — each setup project logs in fresh and overwrites its storage-state file.
- `AUTH_METHOD=skip` — a setup project reuses its existing storage-state file if present, and skips login. If the file is missing the setup project fails. This is how the Argo workflow consumes pre-generated state files.

Tests that verify isolation between users require a second storage-state file through `SECONDARY_AUTH_STATE`. The Argo workflow must mount that file separately from the primary state.

### Common commands

```bash
# Both users, chromium only (primary browser)
BASE_URL=https://release-under-test.example.test npm run test:chromium

# One role across all browsers
BASE_URL=https://release-under-test.example.test npm run test:returning
BASE_URL=https://release-under-test.example.test npm run test:new-user

# Just the auth setup (both users)
BASE_URL=https://release-under-test.example.test npm run setup:auth

# Just the Keycloak cleanup
BASE_URL=https://release-under-test.example.test npm run keycloak:cleanup
```

## Argo execution contract

The release workflow is responsible for:

1. Checking out the exact OpenHands-Cloud release commit or tag.
2. Setting `BASE_URL` to the environment created from that release.
3. Supplying all three credential sets (Keycloak admin, Returning User, New User) plus `KEYCLOAK_NEW_USER_EMAIL` and explicit `TEST_*` fixture values, without logging them.
4. Running from `/workspace/e2e_tests` with locked dependencies.
5. Supplying the ReportPortal settings and API-key Secret when reporting is
   enabled. Argo retains failed pod logs for failures before Playwright starts.

The harness intentionally does not infer a deployment from a branch name or default to a hosted environment.

## Layout

```text
e2e_tests/
├── fixtures/          # Generated or mounted authentication state
├── pages/             # Shared page objects
├── tests/
│   ├── setup/         # Per-role setup projects (keycloak-cleanup, setup-returning, setup-new-user)
│   └── *.spec.ts      # Specs run under both user roles
├── utils/             # Shared config, auth helpers, keycloak admin, test helpers
├── package-lock.json  # Locked Node dependencies
├── playwright.config.ts
└── tsconfig.json
```
