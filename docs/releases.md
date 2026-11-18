# Releases

The `openhands` chart ships release candidates for internal use and stable
releases for customers. Merging the release-please PR cuts a release candidate.
A stable release only comes from promoting a release candidate that has already
been tested.

The other charts (`crd-check`, `image-loader`, `openhands-secrets`) keep cutting
stable releases straight from their release PRs.

## Release candidates

The openhands release PR proposes versions like `0.94.0-rc.1`, `0.94.0-rc.2`,
and so on. Each one counts up from the last stable release, so the version
says which stable release it leads to. Merging the PR tags
`openhands/0.94.0-rc.N`, and that tag:

- publishes the chart to GHCR as a prerelease
- bumps saas-deploy development and staging
- builds the Replicated release, promotes it to Beta, and deploys it to the
  beta instance, where E2E runs against it

Helm ignores prerelease versions unless you pass `--devel`, so customers pulling
from GHCR without a pinned version only ever get stable releases.

## Promoting to stable

1. Run the **Promote release candidate** workflow. Leave the input blank to
   promote the newest release candidate on `main`, or name one, such as
   `openhands/0.94.0-rc.3`.
2. The workflow opens a PR that sets the release line's version to `0.94.0`.
   Its description holds the release notes, gathered from every release
   candidate since the last stable release. Edit them in the PR if needed.
3. Merge the PR. That tags `openhands/0.94.0` on the same commit as the release
   candidate, creates the GitHub Release, publishes chart `0.94.0` to GHCR, and
   publishes the Replicated release to the stable channel. It also goes to Beta,
   unless Beta already has a newer release candidate.

After the merge, the next release PR on that line cuts the first release
candidate of the next version, for example `0.95.0-rc.1`.

The workflow refuses to promote a release candidate whose version has already
shipped, or whose release line has moved past it.

## Backports

A backport line starts from a stable tag. The tag sits on a release candidate's
commit, so its manifest and `Chart.yaml` still carry that release candidate's
version. Before backporting fixes, branch `release/X.Y` from `openhands/X.Y.Z`
and commit `.release-please-manifest.openhands.json` and the `version` in
`charts/openhands/Chart.yaml` set to `X.Y.Z`. Fixes then cut `X.Y.(Z+1)-rc.1`,
which you promote the same way: the promotion PR targets the backport branch.
