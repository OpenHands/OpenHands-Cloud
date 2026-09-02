---
name: upgrade-rollback-runbook
description: Produce customer-facing upgrade AND rollback notes (with a helm diff review gate) for an OpenHands Enterprise Helm chart bump from an older version X to a newer version Y. Use when the user asks to "write upgrade instructions", "create an upgrade/rollback runbook", "prepare upgrade notes for the customer", "helm diff for an upgrade", "upgrade OpenHands from X to Y", or "how do we roll back a chart upgrade". Pairs with the cluster-provisioning skills (eks-install, gke-install, local-kind-install): those stand up a running OpenHands Enterprise install, and this skill drives the upgrade/rollback on top of whichever one was used. Cloud-agnostic - it operates only through kubectl/helm and favors no provider. Discovers the version-specific facts (image bumps, alembic schema delta, orphan tables) rather than assuming them.
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
  --version <Y>
```

Split the output into **substantive** (image bumps, added/removed workloads,
ConfigMap/logic changes) and **cosmetic/artifact**. Known noise to expect and label
as safe:

- Version-label churn on ~all resources: `helm.sh/chart` and
  `app.kubernetes.io/version` bumping X→Y. Ignore.
- **`Secret openhands-minio` "has changed"** — a **helm-diff artifact**, not a real
  change. The MinIO chart's password helper reads the existing secret via `lookup`
  and only randomizes when the secret is absent; `helm diff` cannot resolve that
  lookup, so it shows a phantom `rootPassword`. A real upgrade preserves it. Ignore.

Confirm there are no image versions other than the expected old→new pair:

```bash
grep -E '^[+-].*image:' <diff> | grep -iE 'enterprise-server|runtime-api' | sort -u
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
`runtime_api_db` advanced too. Then have the user run a real conversation.

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
- **`--clean` leaves orphan tables** that Y's migration added (they are not in the X
  dump, so they are not dropped). They are harmless — X ignores them — but call them
  out. Identify them by diffing current tables against the dump's `CREATE TABLE` list:
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

## Worked example (0.55.0 → 0.60.0)

Concrete result of running this procedure once, as a reference for tone and content:

- Images: `enterprise-server 1.56.0 → 1.57.0`, `runtime-api 0.8.2 → 0.9.0`.
- App DB alembic `153 → 155`; `runtime_api_db` unchanged.
- Substantive diff: the two image bumps; the single `openhands-runtime-api-cleanup`
  CronJob replaced by four (`-reaper`, `-k8s-garbage`, `-snapshotter`, `-retention`);
  Keycloak ConfigMaps gained a managed `enterprise_sso` SAML IdP, auto-disabled when
  `enterpriseSSO.*` is unset (no effect on existing auth).
- Artifacts: label/version churn and the phantom `openhands-minio` secret change.
- `--clean` orphan tables from 155: `feature_flags`, `feature_flag_rules` (harmless).
- No new values required.

## Output template (write to a single .md file)

```markdown
# OpenHands Enterprise — Upgrade <X> → <Y>

No new values are required — your existing `values.yaml` carries over.

Prereq: the `helm-diff` plugin
(`helm plugin install https://github.com/databus23/helm-diff`).

## 1. Take a DB dump (required for rollback)
<pg_dump --clean --if-exists command>

## 2. Review the diff — gate before upgrading
<helm diff command>
Proceed only if the output contains only the changes below; otherwise stop and
investigate, then send the investigation notes to the OpenHands team.
Expected — substantive: <discovered image bumps / workload changes>
Expected — cosmetic (safe to ignore): label churn; phantom openhands-minio secret.

## 3. Upgrade
<helm upgrade command>
The app DB migrates automatically (alembic <from> → <to>) on startup.

## 4. Verify
<kubectl get pods>  then log in and start a conversation.

## Rollback (only if needed)
Restore the pre-upgrade dump, then roll back the release:
<scale down / psql restore / helm rollback commands>
> Note: --clean does not drop the tables <Y> added (<orphans>); <X> ignores them.
```

Keep the final notes lean: only what the customer runs and what to expect.
