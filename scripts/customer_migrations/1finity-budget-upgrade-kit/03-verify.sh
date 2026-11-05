#!/usr/bin/env bash

set -euo pipefail
KIT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=lib.sh
source "$KIT_DIR/lib.sh"

[[ -d "$EVIDENCE_DIR/before" ]] || fail "missing pre-upgrade evidence in $EVIDENCE_DIR"
verify_checksums "$EVIDENCE_DIR" >/dev/null || fail "pre-upgrade evidence checksum verification failed"
target_chart=$(require_openhands_version "$EXPECTED_TARGET_VERSION")
target_image=$(openhands_image)
require_core_workloads_ready
ensure_database_access

umask 077
printf '%s\n' "$target_chart" > "$EVIDENCE_DIR/openhands-chart-after.txt"
printf '%s\n' "$target_image" > "$EVIDENCE_DIR/openhands-image-after.txt"
k get cronjob "$BUDGET_CRONJOB" -o yaml > "$EVIDENCE_DIR/budget-cronjob-after.yaml"
k get cronjob "$MAINTENANCE_CRONJOB" -o yaml > "$EVIDENCE_DIR/maintenance-cronjob-after.yaml"
k patch cronjob "$BUDGET_CRONJOB" --type merge -p '{"spec":{"suspend":true}}'
k patch cronjob "$MAINTENANCE_CRONJOB" --type merge -p '{"spec":{"suspend":true}}'

printf 'Checking that organization membership stayed frozen...\n'
list_team_orgs > "$EVIDENCE_DIR/orgs-after-upgrade.txt"
list_team_org_members > "$EVIDENCE_DIR/org-members-after-upgrade.tsv"
diff -u "$EVIDENCE_DIR/orgs.txt" "$EVIDENCE_DIR/orgs-after-upgrade.txt" > "$EVIDENCE_DIR/orgs-after-upgrade.diff" || fail "organization set changed during upgrade"
diff -u "$EVIDENCE_DIR/org-members.tsv" "$EVIDENCE_DIR/org-members-after-upgrade.tsv" > "$EVIDENCE_DIR/org-members-after-upgrade.diff" || fail "organization membership changed during upgrade"

printf 'Collecting and validating upgrade job artifacts...\n'
k logs "job/$PREFLIGHT_JOB" > "$EVIDENCE_DIR/preflight.log"
k logs "job/$GATE_JOB" > "$EVIDENCE_DIR/gate.log"
python3 "$KIT_DIR/tools/check_upgrade_logs.py" \
  --expected-orgs "$EVIDENCE_DIR/orgs.txt" \
  --report "$EVIDENCE_DIR/upgrade-log-report.json" \
  "$EVIDENCE_DIR/preflight.log" "$EVIDENCE_DIR/gate.log"

printf 'Snapshotting every migrated organization after upgrade...\n'
rm -rf "$EVIDENCE_DIR/after"
mkdir -p "$EVIDENCE_DIR/after"
snapshot_all_orgs "$EVIDENCE_DIR/orgs.txt" "$EVIDENCE_DIR/after"
python3 "$KIT_DIR/tools/compare_snapshots.py" \
  --before "$EVIDENCE_DIR/before" \
  --after "$EVIDENCE_DIR/after" \
  --orgs "$EVIDENCE_DIR/orgs.txt" \
  --report "$EVIDENCE_DIR/snapshot-report.json" \
  --mismatched "$EVIDENCE_DIR/mismatched-orgs.txt"

printf 'Checking exact OpenHands ownership after upgrade...\n'
list_managed_orgs > "$EVIDENCE_DIR/managed-orgs-after-upgrade.txt"
diff -u "$EVIDENCE_DIR/orgs.txt" "$EVIDENCE_DIR/managed-orgs-after-upgrade.txt" > "$EVIDENCE_DIR/managed-orgs-after-upgrade.diff" || fail "OpenHands managed-org set differs from the migration plan"
psql_db "$APP_DB" -Atc '
  SELECT jsonb_build_object(
    '"'"'orgs'"'"', COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        '"'"'org_id'"'"', s.org_id,
        '"'"'enabled'"'"', s.enabled,
        '"'"'monthly_limit'"'"', s.monthly_limit,
        '"'"'reset_day'"'"', s.reset_day,
        '"'"'default_user_monthly_limit'"'"', s.default_user_monthly_limit,
        '"'"'cycle_start_at'"'"', s.cycle_start_at,
        '"'"'cycle_start_spend'"'"', s.cycle_start_spend,
        '"'"'user_cycle_start_spend'"'"', s.user_cycle_start_spend,
        '"'"'litellm_last_sync_status'"'"', s.litellm_last_sync_status,
        '"'"'litellm_last_sync_error'"'"', s.litellm_last_sync_error
      ) ORDER BY s.org_id)
      FROM org_budget_settings s
      LEFT JOIN "user" personal ON personal.id = s.org_id
      WHERE personal.id IS NULL
    ), '"'"'[]'"'"'::jsonb),
    '"'"'overrides'"'"', COALESCE((
      SELECT jsonb_agg(jsonb_build_object(
        '"'"'org_id'"'"', override.org_id,
        '"'"'user_id'"'"', override.user_id,
        '"'"'monthly_limit'"'"', override.monthly_limit,
        '"'"'is_disabled'"'"', override.is_disabled
      ) ORDER BY override.org_id, override.user_id)
      FROM org_user_budget_override override
      JOIN org ON org.id = override.org_id
      LEFT JOIN "user" personal ON personal.id = org.id
      WHERE personal.id IS NULL
    ), '"'"'[]'"'"'::jsonb)
  );
' > "$EVIDENCE_DIR/database-policy-after-upgrade.json"
python3 "$KIT_DIR/tools/check_database_plan.py" \
  --plan "$EVIDENCE_DIR/migration-plan.json" \
  --state "$EVIDENCE_DIR/database-policy-after-upgrade.json" \
  --report "$EVIDENCE_DIR/database-policy-report.json"
psql_db "$APP_DB" -P pager=off -c 'SELECT version_num FROM alembic_version;' > "$EVIDENCE_DIR/alembic-after.txt"

printf 'Running %s controlled budget-maintenance verification job(s)...\n' "$BUDGET_CRON_VERIFICATION_RUNS"
for ((run=1; run<=BUDGET_CRON_VERIFICATION_RUNS; run++)); do
  job="1finity-budget-verify-${run}-$(date +%s)"
  k create job --from="cronjob/$BUDGET_CRONJOB" "$job"
  k wait --for=condition=complete "job/$job" --timeout=300s
  k logs "job/$job" > "$EVIDENCE_DIR/budget-cron-run-$run.log"
  destination="$EVIDENCE_DIR/after-cron-$run"
  rm -rf "$destination"
  mkdir -p "$destination"
  snapshot_all_orgs "$EVIDENCE_DIR/orgs.txt" "$destination"
  python3 "$KIT_DIR/tools/compare_snapshots.py" \
    --before "$EVIDENCE_DIR/before" \
    --after "$destination" \
    --orgs "$EVIDENCE_DIR/orgs.txt" \
    --report "$EVIDENCE_DIR/snapshot-report-cron-$run.json" \
    --mismatched "$EVIDENCE_DIR/mismatched-orgs-cron-$run.txt"
  k delete job "$job" --wait=true
  sleep 2
done

printf 'Activating OpenHands budget ownership and restoring maintenance state...\n'
k patch cronjob "$BUDGET_CRONJOB" --type merge -p '{"spec":{"suspend":false}}'
k get cronjob "$BUDGET_CRONJOB" -o jsonpath='{.metadata.name}{" schedule="}{.spec.schedule}{" suspend="}{.spec.suspend}{"\n"}' > "$EVIDENCE_DIR/budget-cronjob-final.txt"
restore_cronjob_state "$MAINTENANCE_CRONJOB" "$EVIDENCE_DIR/maintenance-cronjob-pre-upgrade.txt" "$EVIDENCE_DIR/maintenance-cronjob-final.txt"
require_core_workloads_ready
remove_external_db_helper

cat > "$EVIDENCE_DIR/RESULT.txt" <<'EOF'
VERIFICATION PASSED.

Every team organization is now managed by OpenHands Budgets. Both upgrade
artifacts contained exactly the migrated organizations with no blocking
findings or cap drift. Team and member caps, spend, blocked state, key IDs,
per-key budgets, and team model allowlists matched the pre-migration snapshots
after upgrade and after both controlled maintenance runs. Budget maintenance
is active.
EOF
write_checksums "$EVIDENCE_DIR"
chmod -R go-rwx "$EVIDENCE_DIR"
printf '\nVERIFY PASSED. OpenHands Budgets now owns %s organization(s).\n' "$(wc -l < "$EVIDENCE_DIR/orgs.txt" | tr -d ' ')"
printf 'The maintenance window may close after operator review of RESULT.txt.\n'
