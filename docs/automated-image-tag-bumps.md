# Automated chart image-tag bumps

When a component (e.g. `runtime-api`, `automation`) cuts a release, it can open a
PR in this repo that points the component's chart `image.tag` at the freshly
built image — without anyone editing `values.yaml` by hand.

This is implemented as a **reusable workflow** that component repos call from
their own release workflow:

- Workflow: [`.github/workflows/bump-image-tag.yml`](../.github/workflows/bump-image-tag.yml)
- Edit script: [`scripts/bump_image_tag/bump_image_tag.py`](../scripts/bump_image_tag/bump_image_tag.py)

The caller passes just three things: which **file** to edit, the **path** to the
tag inside it, and the new **tag**.

## What it changes (and what it deliberately doesn't)

The PR changes **only the image tag scalar** — a single line. It does **not**
bump the chart `version` in `Chart.yaml` and does nothing else.

Because of that, the **Validate Chart Versions** check (`enforce_version_bump:
true`) will **fail** on these PRs: a chart file changed without a version bump.
This is intentional — a maintainer bumps the chart version while reviewing/merging
the bump.

The edit is path-aware and minimal by design. `runtime-api/values.yaml` has three
`tag:` keys (`image.tag`, `kvm.image.tag`, `kvm.initImage.tag`); the script edits
exactly the one you name and leaves quoting, comments, and blank lines untouched.
(`yq -i` strips blank lines; a full `ruamel` re-dump rewrites unrelated scalars
like `dryRun: False` → `false`; a blanket `sed` hits the wrong `tag:`. The script
avoids all three.)

## Authentication

This reuses the **same GitHub App** as
[`OpenHands/release-actions`](https://github.com/OpenHands/release-actions) — the
org secrets **`RELEASE_APP_ID`** and **`RELEASE_APP_PRIVATE_KEY`**. The component
repo already has these in scope for its release-please workflow, so the caller just
forwards them with `secrets: inherit`.

The workflow mints a short-lived installation token from the App. There is **no
`GITHUB_TOKEN` fallback** (matching release-actions): a PR opened with the default
token wouldn't trigger this repo's required checks.

> **Prerequisite to confirm.** release-actions uses the App on the repo it runs in.
> Here the workflow runs in the *component* repo but writes to *this* repo, so it
> scopes the token to `OpenHands/OpenHands-Cloud` via `owner`/`repositories`. That
> requires the **same App to be installed on `OpenHands/OpenHands-Cloud`** with
> **Contents: Read and write** and **Pull requests: Read and write**. If it's
> installed org-wide, you're set; otherwise add this repo to its installation.

## Caller setup (in the component repo)

A reusable-workflow caller job can't run steps, so if you need to compute the tag
(e.g. a short-SHA tag like `sha-61fc535`), do it in a small upstream job and pass
the result.

```yaml
# .github/workflows/release.yml (in the runtime-api repo, for example)
name: Release
on:
  push:
    branches: [main]      # or: release: { types: [published] }

jobs:
  # ... your existing build-and-push-image job ...

  prepare-tag:
    runs-on: ubuntu-latest
    outputs:
      tag: ${{ steps.t.outputs.tag }}
    steps:
      - uses: actions/checkout@v4
      # Must match the tag you pushed the image with.
      - id: t
        run: echo "tag=sha-$(git rev-parse --short HEAD)" >> "$GITHUB_OUTPUT"

  bump-chart:
    needs: prepare-tag
    uses: OpenHands/OpenHands-Cloud/.github/workflows/bump-image-tag.yml@main
    # Forwards RELEASE_APP_ID / RELEASE_APP_PRIVATE_KEY to the reusable workflow.
    secrets: inherit
    with:
      component: runtime-api
      chart_file: charts/runtime-api/values.yaml
      image_tag_path: .image.tag          # optional — this is the default
      tag: ${{ needs.prepare-tag.outputs.tag }}
```

A ready-to-copy version is in
[`docs/examples/component-release-bump.yml`](examples/component-release-bump.yml).

### The `automation` component

```yaml
  bump-chart:
    needs: prepare-tag
    uses: OpenHands/OpenHands-Cloud/.github/workflows/bump-image-tag.yml@main
    secrets: inherit
    with:
      component: automation
      chart_file: charts/automation/values.yaml
      tag: ${{ needs.prepare-tag.outputs.tag }}
```

## Inputs

| Input | Required | Default | Description |
| ----- | -------- | ------- | ----------- |
| `component` | yes | — | Used in the branch, PR title, and commit message. |
| `chart_file` | yes | — | YAML file to edit, relative to the chart repo root. |
| `tag` | yes | — | New image tag to set. Must match the tag you pushed. |
| `image_tag_path` | no | `.image.tag` | yq-style path to the tag scalar. Supports nested keys, e.g. `.warmRuntimes.configsByName.default.image`, and list indices, e.g. `.containers[0].image`. |
| `base_branch` | no | `main` | Branch to open the PR against. |
| `chart_repo` | no | `OpenHands/OpenHands-Cloud` | Repo to update, `owner/name`. |
| `pr_branch` | no | `bump-image-tag/<component>` | Head branch. The default rolls a single open PR per component, advancing it to the latest tag. Set something tag-specific (e.g. `bump-image-tag/runtime-api/${{ needs.prepare-tag.outputs.tag }}`) to get one PR per release. |
| `pr_labels` | no | `automated`, `image-bump` | Labels to add to the PR. |
| `draft` | no | `false` | Open the PR as a draft. |

Secrets (both required, forwarded via `secrets: inherit`): `RELEASE_APP_ID` and
`RELEASE_APP_PRIVATE_KEY` — the shared release GitHub App. See
[Authentication](#authentication).

Outputs: `pull-request-number`, `pull-request-url` (empty when the tag was already
current and no PR was needed).

## Behavior notes

- **Idempotent:** if the tag already matches, the script makes no change and no PR
  is opened.
- **One rolling PR per component (default):** a second release before the first PR
  merges updates the same PR to the newer tag. After a PR merges and its branch is
  deleted, the next release opens a fresh PR.
- **Concurrency:** runs are serialized per `component` + `chart_repo` so two
  near-simultaneous releases can't clobber each other's branch.

## Testing the edit script locally

```bash
# Dry run against a real chart file (prints old -> new, writes nothing):
uv run scripts/bump_image_tag/bump_image_tag.py \
  --file charts/runtime-api/values.yaml --path .image.tag --tag sha-1234567 --dry-run

# Unit tests:
uv run scripts/bump_image_tag/test_bump_image_tag.py
```
