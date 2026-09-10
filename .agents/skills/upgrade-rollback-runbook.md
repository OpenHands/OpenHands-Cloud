---
name: upgrade-rollback-runbook
description: >-
  Produce customer-facing upgrade AND rollback notes (with a helm diff review gate)
  for an OpenHands Enterprise Helm chart bump from an older version X to a newer
  version Y. Use when the user asks to "write upgrade instructions", "create an
  upgrade/rollback runbook", "prepare upgrade notes for the customer", "helm diff
  for an upgrade", "upgrade OpenHands from X to Y", or "how do we roll back a chart
  upgrade". Pairs with the cluster-provisioning skills (eks-install, gke-install,
  local-kind-install): those stand up a running OpenHands Enterprise install, and
  this skill drives the upgrade/rollback on top of whichever one was used.
  Cloud-agnostic - it operates only through kubectl/helm and favors no provider.
  Discovers the version-specific facts (image bumps, alembic schema delta, orphan
  tables) rather than assuming them.
---

# OpenHands Enterprise upgrade + rollback runbook

Generate concise, customer-safe notes for moving an existing install from chart
version **X** to a newer version **Y**, including a validated rollback path. The
chart source is the ghcr OCI registry: `oci://ghcr.io/openhands/helm-charts/openhands`.

The single most important fact drives everything: **on an embedded-Postgres install
with persistence enabled, an upgrade never loses data** (the PVCs survive). The real
rollback hazard is a **forward Alembic schema migration** — if Y advances the app DB
schema, the older X application cannot read it, so a plain `helm rollback` is not
enough. This is why a **pre-upgrade DB dump is required whenever rollback must be
possible**. Data loss is not the risk; schema incompatibility is.

Do not hardcode the deltas below — discover them for the specific X→Y with the
commands here, then write them into the notes.

## Prerequisites (pairs with the cluster-install skills)

Start from a running OpenHands Enterprise install; do not provision a cluster here.
Use one of the companion provisioning skills, chosen only by the environment the
reproduction needs - this skill favors no provider:

- `eks-install` - AWS / EKS
- `gke-install` - GCP / GKE
- `local-kind-install` - local KinD

This skill picks up from whatever any of them produced and operates only through
`kubectl` and `helm` - no cloud storage classes, load balancers, or DNS assumptions.
Before starting, confirm `helm`, `kubectl`, the `helm-diff` plugin, and a `kubectl`
context pointing at the target cluster are available.

## Fixed facts (chart-invariant)

- Chart URL: `oci://ghcr.io/openhands/helm-charts/openhands`
- App DB: database `openhands` on pod `openhands-postgresql-0` (env `POSTGRES_PASSWORD`).
- Runtime API DB: database `runtime_api_db` (separate migration chain).
- Schema version lives in each DB's `alembic_version.version_num`.
- Resource names are release-fixed (`deploy/openhands`, `openhands-postgresql-0`);
  namespace and release name are customer-specific — use `<namespace>` / the real
  release name in customer notes.
- **Three images move on a version bump:** `enterprise-server` (used by
  `deploy/openhands`, mcp, integrations), `runtime-api`, and the sandbox
  `agent-server` that warm runtimes run. Check all three in the diff.

## Warm runtimes (handle before upgrading)

A version bump usually ships a new sandbox `agent-server` image (e.g. 0.60.0 used
`1.44.1-python`, 0.64.0 uses `1.46.0-python`). Warm pods are claimed by an **exact
image + env match**, so this must be reconciled **before** the upgrade:

- If the customer pins the tag, update it in `values.yaml` first —
  `global.agentServerImage.tag`, or per pool
  `runtime-api.warmRuntimes.configsByName.<name>.image`.
- If they do not pin it, the warm pool inherits the new default automatically; no
  change is needed.

A stale pinned tag leaves the pool **unclaimable**: new conversations cold-start and
the old sandbox keeps serving. Discover the new tag from the diff's
`agent-server` image line.

## Procedure

### 1. Capture the pre-upgrade baseline

Record what you will compare against after the upgrade and what rollback restores to:

```bash
# image versions
kubectl get deploy openhands openhands-runtime-api -n <namespace> \
  -o jsonpath='{range .items[*]}{.metadata.name}={.spec.template.spec.containers[0].image}{"\n"}{end}'
# app + runtime schema revisions
kubectl exec openhands-postgresql-0 -n <namespace> -- bash -c \
  'PGPASSWORD=$POSTGRES_PASSWORD psql -U postgres -d openhands -tAc "select version_num from alembic_version;"'
kubectl exec openhands-postgresql-0 -n <namespace> -- bash -c \
  'PGPASSWORD=$POSTGRES_PASSWORD psql -U postgres -d runtime_api_db -tAc "select version_num from alembic_version;"'
```

### 2. Take the required DB dump (gates rollback)

Use `--clean --if-exists` so the file can be restored straight into the existing
database later without a `DROP DATABASE`:

```bash
kubectl exec -n <namespace> openhands-postgresql-0 -- bash -c \
  'PGPASSWORD=$POSTGRES_PASSWORD pg_dump --clean --if-exists -U postgres -d openhands' \
  > pre<Y>_openhands.sql
```

### 3. Run the helm diff and classify every change

```bash
helm diff upgrade <release-name> \
  oci://ghcr.io/openhands/helm-charts/openhands \
  --namespace <namespace> \
  --values values.yaml \
  --version <Y> \
  --context=0
```

`--context=0` trims the surrounding unchanged lines so the diff shows only the
changed lines. Customers routinely find the default output too noisy; use this flag
to keep the review focused on what actually changed. Drop it (or raise the value) if
you need surrounding context to interpret a specific hunk.

Split the output into **substantive** (image bumps, added/removed workloads,
ConfigMap/logic changes) and **cosmetic/artifact**. Known noise to expect and label
as safe:

- Version-label churn on ~all resources: `helm.sh/chart` and
  `app.kubernetes.io/version` bumping X→Y. Ignore.
- **`Secret openhands-minio` "has changed"** — a **helm-diff artifact**, not a real
  change. The MinIO chart's password helper reads the existing secret via `lookup`
  and only randomizes when the secret is absent; `helm diff` cannot resolve that
  lookup, so it shows a phantom `rootPassword`. A real upgrade preserves it. Ignore.

Confirm there are no image versions other than the expected old→new bumps — including
the sandbox `agent-server` image that warm runtimes claim on an exact match:

```bash
grep -E '^[+-].*image:' <diff> | grep -iE 'enterprise-server|runtime-api|agent-server' | sort -u
```

Instruct the customer: proceed only if the diff contains only the expected changes;
otherwise stop, investigate, and send the notes to the OpenHands team.

### 4. Upgrade

```bash
helm upgrade <release-name> \
  oci://ghcr.io/openhands/helm-charts/openhands \
  --namespace <namespace> \
  --values values.yaml \
  --version <Y> \
  --timeout 18m
kubectl rollout status deploy/openhands -n <namespace>
```

Existing `values.yaml` normally carries over unchanged. Confirm this by checking the
diff had no removed/renamed values keys; only claim "no new values required" after
verifying.

### 5. Verify the upgrade

Re-run the step-1 checks. Expect the images at Y, the app `alembic_version`
**advanced** (record the from→to), and no unhealthy pods. Note whether
`runtime_api_db` advanced too. Confirm the sandbox/warm pods run the new
`agent-server` image, so warm claims actually hit:

```bash
kubectl get pods -n <namespace> \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[0].image}{"\n"}{end}' \
  | grep agent-server
```

Then have the user run a real conversation.

### 6. Rollback (only if needed)

Restore the pre-upgrade dump, then roll the release back. Restoring **before** the
rollback means the older app starts against a schema it understands:

```bash
kubectl scale deploy/openhands -n <namespace> --replicas=0
kubectl exec -i -n <namespace> openhands-postgresql-0 -- bash -c \
  'PGPASSWORD=$POSTGRES_PASSWORD psql -U postgres -d openhands' < pre<Y>_openhands.sql
helm rollback <release-name> -n <namespace>
kubectl rollout status deploy/openhands -n <namespace>
```

Verify: images back at X, `alembic_version` back at the X value, pods healthy, no
`migrate-db` crash-loop.

## Non-obvious findings to bake into the notes

- **Only `deploy/openhands` needs scaling down** before the restore. Verify no other
  component holds app-DB connections (mcp, integrations typically do not):
  ```bash
  kubectl exec -i openhands-postgresql-0 -n <namespace> -- bash -c \
    'PGPASSWORD=$POSTGRES_PASSWORD psql -U postgres -d postgres' <<'SQL'
  select application_name, usename, state, count(*) from pg_stat_activity
  where datname='openhands' group by 1,2,3;
  SQL
  ```
  Zero rows after the scale-down confirms the single scale-down is sufficient.
- **`--clean` leaves orphan tables only when Y adds new tables.** Column-only
  migrations (e.g. 155→157, which just add columns) leave none and restore cleanly.
  When Y does add tables they are not in the X dump, so `--clean` cannot drop them;
  they are harmless — X ignores them — but call them out. Identify them by diffing
  current tables against the dump's `CREATE TABLE` list:
  ```bash
  kubectl exec -i openhands-postgresql-0 -n <namespace> -- bash -c \
    'PGPASSWORD=$POSTGRES_PASSWORD psql -U postgres -d openhands -tAc \
    "select tablename from pg_tables where schemaname='"'"'public'"'"' order by 1;"' | sort > /tmp/cur.txt
  grep -oiE 'CREATE TABLE [a-z0-9_."]+' pre<Y>_openhands.sql | sed -E 's/CREATE TABLE //I; s/public\.//; s/"//g' | sort -u > /tmp/dump.txt
  comm -23 /tmp/cur.txt /tmp/dump.txt   # orphans; ignore hyphenated false positives from the regex
  ```
- If the app DB schema did **not** advance X→Y, a plain `helm rollback` is sufficient
  and the restore step can be skipped — but still take the dump as insurance.
- `runtime_api_db` is often unchanged across an upgrade; if so, it needs no restore.
- Do not restate the customer's settings, do not offer backup/restore alternatives
  (stick to `pg_dump`), and do not reference cloud-specific storage (EBS/PD) — the
  customer's platform may differ from the test rig.

## Worked example (0.60.0 → 0.64.0)

Concrete result of running this procedure once, as a reference for tone and content:

- Images: `enterprise-server 1.57.0 → 1.59.1`, `runtime-api 0.9.0 → 0.10.0`, and the
  sandbox/warm `agent-server 1.44.1-python → 1.46.0-python`.
- App DB alembic `155 → 157` (adds only columns — org budget-spend snapshot, then a
  user-disabled flag); `runtime_api_db` unchanged.
- Substantive diff: the three image bumps; one CronJob added
  (`openhands-app-conversation-start-task-clean`, daily at 03:00); no workloads removed.
- Artifacts: label/version churn and the phantom `openhands-minio` secret change.
- Rollback restored cleanly with **no orphan tables** (column-only migration).
- No new values required — except re-pinning the warm-runtime `agent-server` tag.

## Output template (write to a single .md file)

```markdown
# OpenHands Enterprise — Upgrade <X> → <Y>

No new values are required — your existing `values.yaml` carries over — **except**
the warm-runtime `agent-server` image if you pin it (see Warm runtimes below).

Prereq: the `helm-diff` plugin
(`helm plugin install https://github.com/databus23/helm-diff`).

## Warm runtimes — update the pinned agent-server image (only if you pin it)
<Y> ships agent-server `<new-tag>` (<X> used `<old-tag>`). If you pin it, set it in
`values.yaml` **before** upgrading; if you do not pin it, the pool inherits the new
default automatically. A stale pinned tag leaves the warm pool unclaimable.

## 1. Take a DB dump (required for rollback)
<pg_dump --clean --if-exists command>

## 2. Review the diff — gate before upgrading
<helm diff command with --context=0 so only changed lines show>
Proceed only if the output contains only the changes below; otherwise stop and
investigate, then send the investigation notes to the OpenHands team.

Expected — substantive:
- <image bump: enterprise-server old → new>
- <image bump: runtime-api old → new>
- <image bump: agent-server old → new>
- <added/removed workloads, if any>

Expected — cosmetic (safe to ignore, on ~20 resources):
- label/version churn (`helm.sh/chart`, `app.kubernetes.io/version`)
- phantom `openhands-minio` secret change

## 3. Upgrade
<helm upgrade command>
The app DB migrates automatically (alembic <from> → <to>) on startup.

## 4. Verify
<kubectl get pods>; confirm sandbox pods run agent-server `<new-tag>`, then log in
and start a conversation.

## Rollback (only if needed)
Restore the pre-upgrade dump, then roll back the release:
<scale down / psql restore / helm rollback commands>
> Note: if <Y> added tables, --clean does not drop them (<orphans>) and <X> ignores
> them; column-only migrations leave no orphans.
```

Keep the final notes lean: only what the customer runs and what to expect.
