# Description

Updates the OpenHands and image-loader helm charts to cut a new enterprise chart release.

For a given OpenHands cloud tag, the script:

- Updates image tags in `charts/openhands/values.yaml`, `replicated/openhands.yaml`, and `replicated/config.yaml`.
- Updates image tags in the embedded runtime-api and automation subcharts (`charts/openhands/charts/<name>/values.yaml`). These subcharts have no chart version of their own — they ship inside the openhands chart, so a change to their values triggers a patch bump of the openhands chart version instead. Their dependency entries in `charts/openhands/Chart.yaml` are repository-less with version `"*"` and are never rewritten.
- Updates `appVersion` and bumps the patch version in `charts/openhands/Chart.yaml` when any of the above changed.
- Updates the agent-server image tag in `charts/image-loader/values.yaml` and bumps the image-loader chart version when it changed (image-loader is still a standalone chart).

## Prerequisites

- [uv](https://docs.astral.sh/uv/) must be installed
- A GitHub token with read access to the OpenHands repository

## Usage

1. Set the `GITHUB_TOKEN` environment variable:

   ```bash
   export GITHUB_TOKEN=your_github_token
   ```

   > Try getting with: `gh auth status --show-token`

2. Run the script:

   ```bash
   ./scripts/update_openhands_charts/update_openhands_charts.py
   ```

   Or using uv directly:

   ```bash
   uv run scripts/update_openhands_charts/update_openhands_charts.py
   ```

   If you want to pass in a specific cloud tag instead of getting the latest GitHub OpenHands cloud tag:
   ```bash
   ./scripts/update_openhands_charts/update_openhands_charts.py --cloud-tag cloud-x.x.x
   ```

   Or using uv directly:

   ```bash
   uv run scripts/update_openhands_charts/update_openhands_charts.py --cloud-tag cloud-x.x.x
   ```

   > View help for available arguments: `uv run scripts/update_openhands_charts/update_openhands_charts.py --help`

### DRY RUN mode

```bash
./scripts/update_openhands_charts/update_openhands_charts.py --dry-run
```

Or using uv directly:

```bash
uv run scripts/update_openhands_charts/update_openhands_charts.py --dry-run
```

## Tests

Run the tests:

```bash
./scripts/update_openhands_charts/test_update_openhands_charts.py
```

Or using uv directly:

```bash
uv run scripts/update_openhands_charts/test_update_openhands_charts.py
```
