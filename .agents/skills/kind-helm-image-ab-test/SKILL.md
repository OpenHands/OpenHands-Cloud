---
name: kind-helm-image-ab-test
description: This skill should be used when the user asks to "test a PR on a local kind helm install", "control vs treatment image test on kind", "A/B an enterprise-server image on kind", "verify an enterprise PR on a kind helm cluster", "swap the enterprise-server image on kind and re-run", or wants to compare a control vs treatment build of openhands on a local KinD Helm install.
---

# Local KinD Helm control-vs-treatment image testing

## Purpose
Verify an enterprise PR on a local KinD Helm install by **swapping only the
enterprise-server image** between a **control** (branch-base) and **treatment**
(branch-head) build **on the same database**, so the two runs differ by *exactly
the PR's commits*. Confirm the control **reproduces** the bug and the treatment
**fixes** it, anchored on a deterministic in-pod probe.

This is faster and more rigorous than reinstalling, because the DB, config, and
chart stay fixed — the image is the only variable.

## When to use
Reproducing/verifying an enterprise-server behavior change (rotation, settings
resolution, auth, etc.) against a real running SaaS stack without a Replicated VM.
Note: KOTS-layer changes (anything in `replicated/…` templated with
`ConfigOption`/`repl{{}}`) are **not** exercised by plain Helm — those need
KOTS-into-KinD or a VM.

## SAFETY (read first)
The local kubeconfig frequently also contains **production, staging, and EKS**
contexts. **Pin every command** to the local cluster and verify it is local:
```bash
KCTX=kind-<cluster-name>
kubectl --context "$KCTX" get node -o jsonpath='{.items[0].spec.providerID}{"\n"}'  # must be kind://…
```
Never run an unpinned `kubectl`/`helm` while doing this work.

## Core workflow

### 1. Pick control vs treatment images (same DB)
Control = branch-base SHA image; treatment = branch-head SHA image. They must
share the same alembic head so they swap cleanly on one DB. See
`references/image-selection.md`:
- `git log --oneline <base>..<pr-head>` = only the PR commits.
- `git diff --stat <base>..<pr-head> -- migrations/` = empty (no migration delta).
- The running DB's `alembic_version` must exist in **both** images
  (`git grep -l "<rev>" <commit> -- 'migrations/versions/*'`).
- A **release-tag** image is often *older* than the branch base and may lack a
  revision the DB already has → it will fail `Can't locate revision` and is **not**
  a valid control. Use the branch-base SHA.

### 2. Swap ALL enterprise-server containers (not just the main one)
The Deployment's init containers (`migrate-db`, `keycloak-config`,
`litellm-config`) default to the **chart appVersion** tag, not whatever the main
container is set to. If `migrate-db` runs an image that predates the DB revision,
the pod is stuck in `Init:Error` (`Can't locate revision`). Always set them
together:
```bash
IMG=ghcr.io/openhands/enterprise-server:sha-<tag>
kubectl --context "$KCTX" set image deploy/openhands -n <ns> \
  openhands=$IMG migrate-db=$IMG keycloak-config=$IMG litellm-config=$IMG
kubectl --context "$KCTX" rollout status deploy/openhands -n <ns> --timeout=200s
```

### 3. Confirm which build is live (gate check)
Verify the running code actually is control vs treatment — e.g. inspect a changed
function's signature:
```bash
kubectl --context "$KCTX" exec -n <ns> deploy/openhands -c openhands -- \
  python -c "import inspect; from storage.saas_settings_store import SaasSettingsStore; \
print(inspect.signature(SaasSettingsStore.rotate_managed_llm_key))"
```
Control shows the old signature; treatment shows the fixed one.

### 4. Run the deterministic probe (control, then treatment)
Write the probe to `/tmp/*.py`, `kubectl cp` it in, and exec it (inline heredocs
get mangled through `kubectl exec`). Reset shared state to a known baseline before
each run. Start from `scripts/probe_template.py`. The control result must show the
bug; the treatment result must show it fixed. Re-run 2–3× for races.

### 5. End-to-end conversation (optional confirmation)
See `references/access-and-e2e.md`. Key gotchas:
- Managed `openhands/…` models need `OPENHANDS_PROVIDER_BASE_URL` set to the
  in-cluster LiteLLM, or the key is sent to the **public** proxy → `token_not_found`
  (a deterministic, every-time failure unrelated to any rotation race).
- Mint an API key for the test user via `ApiKeyStore` in the app pod (keys are
  stored **plaintext**), then drive `POST /api/v1/app-conversations` through a
  port-forward, or open the browser UI.

### 6. Restore + report
Leave the cluster on the treatment image (the intended good state), clean up any
throwaway API key/conversation, and record PASS/FAIL with the control vs treatment
probe values.

## Resources
- `references/image-selection.md` — branch-base vs branch-head SHAs, migration/
  alembic compatibility, why a release tag is usually the wrong control.
- `references/access-and-e2e.md` — port-forward + API key minting + browser access
  (`*.oh.example.com` wildcard DNS, traefik NodePort, TLS) and the base_url routing
  gotcha.
- `references/gotchas.md` — node memory, `Init:Error` migrate-db skew, stale tmux
  session, and other KinD-specific traps.
- `scripts/probe_template.py` — adaptable in-pod probe skeleton.
