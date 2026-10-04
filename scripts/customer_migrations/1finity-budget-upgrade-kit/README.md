# 1finity LiteLLM-to-OpenHands budget migration kit

This kit transitions 1finity from manually managed LiteLLM budgets to OpenHands-managed budgets while upgrading exact chart `openhands-0.55.1-rc.1` to exact chart `openhands-0.74.0`. It is tailored to 1finity's external TLS PostgreSQL installation and runs on the OpenHands host.

The migration is customer-specific operational tooling. It does not require product adoption code.

## Migration semantics

For every team organization, the kit reads live LiteLLM policy and spend, then stages equivalent OpenHands ownership:

- Team `max_budget` becomes the organization `monthly_limit`.
- The current organization baseline is zero, so first reconciliation preserves the existing absolute cap.
- Each private member cap becomes an explicit user override with a zero baseline.
- Shared-budget members receive no user override.
- The current cycle starts on `MIGRATION_RESET_DAY`. At the next reset, OpenHands re-anchors cumulative spend and grants the same configured limits for the new monthly cycle.

The kit fails closed if a team is blocked, lacks a positive cap, is over cap, has a private member over cap, has incomplete financial data, differs from the OpenHands roster, or changes after planning.

A fail-closed exit is printed as `MIGRATION HALTED (FAIL CLOSED)`. It is not permission to retry automatically. Keep the maintenance window active, leave both migration writers suspended, preserve the evidence, and investigate or run a separately approved guarded restore. Budget maintenance is activated only at the end of a completely successful Stage 3.

### Intentional boundary

OpenHands Budgets owns team and member caps, not every LiteLLM field.

- Existing team model allowlists are outside budget ownership and must remain exactly unchanged through the upgrade and controlled maintenance runs.
- Per-key budgets cannot be represented by OpenHands Budgets. Any non-null per-key cap blocks planning; key identifiers remain part of verification evidence.
- Teams must remain unblocked.

If 1finity uses per-key caps, do not run this migration until those caps are removed under an approved customer plan or OpenHands supports them. The kit never silently drops unsupported policy.

## Stages

### `01-plan.sh`

1. Requires maintenance-window and clock acknowledgments and exact source `0.55.1-rc.1`.
2. Creates the temporary external-TLS PostgreSQL helper from existing image and Secret references.
3. Records all team orgs/members and suspends both maintenance CronJobs.
4. Saves complete LiteLLM policy and financial snapshots.
5. Takes validated LiteLLM and OpenHands budget-table dumps.
6. Produces reviewable `migration-plan.json` and guarded `apply-migration.sql` without changing budget ownership.

### `02-apply.sh`

1. Requires explicit confirmation that every plan entry was reviewed.
2. Rejects a stale plan or any org, roster, policy, or spend change.
3. Creates `rh_backup_*` tables and applies all settings/overrides in one guarded transaction.
4. Requires the managed-org set to equal the complete planned set.
5. Proves SQL staging did not alter LiteLLM.

It does not run the source-version budget CronJob. That synchronizer can discard baselines for shared-budget members; target migrations preserve the staged baselines correctly.

### Manual upgrade

In the Replicated Admin Console:

1. Keep pre-upgrade Budget Preflight in **Acknowledge** mode.
2. Keep Post-upgrade Budget Reconciliation Gate in **Strict** mode.
3. Deploy exact target `0.74.0`.
4. Preserve logs before investigating or retrying any failed release.

Both artifacts must contain exactly every migrated org with no drift, blocking finding, unreachable org, or sync error.

### `03-verify.sh`

1. Requires exact target `0.74.0` and ready core workloads.
2. Immediately suspends both CronJobs again.
3. Requires org and membership sets to remain frozen.
4. Validates both upgrade artifacts against the exact migrated-org set.
5. Requires caps, spend, blocked state, key IDs, per-key budgets, and team model allowlists to match pre-migration snapshots exactly.
6. Requires every planned org, and only those orgs, to be OpenHands-managed.
7. Runs two controlled budget-maintenance Jobs and repeats all comparisons.
8. Activates budget maintenance and restores general maintenance only after every gate passes.

### `04-restore.sh`

Emergency rollback for approved mismatched orgs:

1. Suspends both CronJobs.
2. Removes OpenHands ownership for only approved orgs.
3. Restores original LiteLLM policy fields through LiteLLM APIs.
4. Requires an exact snapshot match.

Restored orgs are unmanaged and need a new migration plan before retry. Never `pg_restore` LiteLLM after live traffic resumes because that rewinds spend.

## Setup

```bash
cd scripts/customer_migrations/1finity-budget-upgrade-kit
cp config.env.example config.env
chmod 700 *.sh tools/*.py
chmod 600 config.env
```

Review `config.env`:

- Keep `DATABASE_MODE=external` for 1finity.
- Confirm namespace, deployments, databases, CronJobs, and upgrade Jobs.
- Use a new absolute `EVIDENCE_DIR`.
- Confirm `MIGRATION_RESET_DAY`; `1` matches the existing setup.
- Keep the plan-age window short enough to prevent spend drift.

The helper reuses OpenHands' cached `wait-for-db` image with `imagePullPolicy: Never` and existing database Secret references. Credentials never leave Kubernetes. Successful verification removes it.

If planning exits early, delete the helper only after confirming label `app.kubernetes.io/managed-by=1finity-budget-upgrade-kit`.

## Maintenance-window procedure

Tell admins not to save Budgets pages, onboard users, or start sessions until verification passes.

### 1. Plan and back up

```bash
CLOCK_CHECK_CONFIRMED=YES \
MAINTENANCE_WINDOW_CONFIRMED=YES \
./01-plan.sh
```

Review `migration-plan.json`, all `before/` snapshots, `orgs.txt`, `org-members.tsv`, dumps, and `apply-migration.sql`.

### 2. Apply reviewed ownership

```bash
MIGRATION_PLAN_REVIEW_CONFIRMED=YES ./02-apply.sh
```

Do not upgrade unless it prints `APPLY PASSED` and managed-org/snapshot diffs are empty. Any `MIGRATION HALTED (FAIL CLOSED)` message means stop: keep the maintenance window and writers suspended, preserve the evidence, and do not retry automatically.

### 3. Upgrade and verify

Deploy exact `0.74.0`, then run:

```bash
./03-verify.sh
```

Close the window only after `VERIFY PASSED` and review of:

- `upgrade-log-report.json`
- `snapshot-report.json`
- `snapshot-report-cron-1.json`
- `snapshot-report-cron-2.json`
- `database-policy-after-upgrade.json`
- `database-policy-report.json`
- `budget-cronjob-final.txt` (`suspend=false`)
- `maintenance-cronjob-final.txt`
- `RESULT.txt`

After acceptance, admins may manage team and member limits through OpenHands Budgets. OpenHands is the budget source of truth.

## Emergency rollback

```bash
RESTORE_CONFIRMED=YES \
RESTORE_MISMATCH_FILE="$EVIDENCE_DIR/mismatched-orgs.txt" \
RESTORE_ORGS='org-uuid-1,org-uuid-2' \
./04-restore.sh
```

Keep the maintenance window and both CronJobs suspended until the failure is understood and a new plan is generated.

## Evidence and cleanup

Evidence contains sensitive operational data and LiteLLM key identifiers. Keep it mode `0700`, encrypted, and limited to the authorized upgrade team.

After acceptance and the rollback window, an authorized DBA may remove:

```sql
DROP TABLE IF EXISTS rh_backup_org_budget_settings;
DROP TABLE IF EXISTS rh_backup_org_user_budget_override;
```
