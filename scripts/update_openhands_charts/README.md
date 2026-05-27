# Description

Updates the OpenHands and runtime-api helm charts to cut a new enterprise chart release.

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
