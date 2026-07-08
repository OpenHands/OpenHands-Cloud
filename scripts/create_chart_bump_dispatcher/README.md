# Chart bump dispatcher App

Creates GitHub App credentials for OpenHands-Cloud workflows that need to
send `repository_dispatch` events to `OpenHands/saas-deploy`.

The script is intentionally generic. It does not encode dev, staging, or any
other environment behavior; receiver workflows in `saas-deploy` validate and
constrain the payload they accept.

## Permissions

The generated App manifest grants only:

- `contents: write` — required by GitHub's `repository_dispatch` endpoint.
- `metadata: read` — required for every GitHub App.

Install the App only on `OpenHands/saas-deploy`, and do not add it to
`saas-deploy` ruleset bypass actors.

## Usage

```bash
# staging
uv run scripts/create_chart_bump_dispatcher/create_chart_bump_dispatcher.py \
  --org OpenHands \
  --app-name staging-chart-bump-dispatcher

# future dev
uv run scripts/create_chart_bump_dispatcher/create_chart_bump_dispatcher.py \
  --org OpenHands \
  --app-name saas-deploy-dev-chart-dispatcher-openhands
```

The private key is written under `scripts/create_chart_bump_dispatcher/keys/`
with mode `0600`. The key directory is ignored by git. The command prints the
App ID and the private-key file path, not secret values.

After the App is created, the command redirects the same browser tab to the
GitHub installation page and polls GitHub with App auth until an installation is
visible. If installation is not detected before the timeout, the command exits
non-zero after printing the credential locations.
