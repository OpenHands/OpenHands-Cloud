# Replicated Hotfix Runbook

Emergency fast path: publish a hotfix to a dedicated channel with `make release`,
then move the affected customer onto that channel. The channel is the record of
what that customer is running.

## Prerequisites (one-time)

```bash
brew install replicatedhq/replicated/cli yq   # yq is used internally by the Makefile
replicated login                               # or: export REPLICATED_API_TOKEN=<token>
export REPLICATED_APP=openhands
```

## Steps

1. **Branch.**
   ```bash
   git checkout -b hotfix/<customer-or-issue>
   ```

2. **Set the hotfix image tag** in the component's `values.yaml`. Semver, not `sha-*`.
   ```yaml
   # charts/openhands/charts/automation/values.yaml
   image:
     tag: 1.1.6
   ```
   Other components: same edit in `charts/openhands/charts/<component>/values.yaml`.

3. **Bump the chart version** (must be unique — do not reuse the released version).
   ```yaml
   # charts/openhands/Chart.yaml
   version: 0.21.1
   ```

4. **Publish** to a dedicated channel (created on the fly):
   ```bash
   make release CHANNEL=hotfix-<customer-or-issue>
   ```
   Prints `SEQUENCE: <n>` + vendor-portal link on success.

5. **Deliver.** Vendor portal → assign the customer's license to `hotfix-<...>`.
   They pull it on next update check; no other customer is affected.

6. **Settle.** Move the customer back to their standard channel once the fix ships
   in a normal release. Stale channels auto-archive via `cleanup-stale-channels.yml`.

## Notes

- The guard (`check-release-guard`) allows any channel except `main → Unstable`; no flags needed.
- A `make release` sequence has **no git tag or GHCR chart** — not reproducible from git.
  Acceptable for emergencies. Do not leave a customer on a hotfix channel long-term.
- Use `sha-*` tags only for SaaS/OHE dev environments — never for Replicated.
