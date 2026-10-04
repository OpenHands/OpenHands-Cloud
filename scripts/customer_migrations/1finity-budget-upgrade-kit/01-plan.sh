#!/usr/bin/env bash

set -euo pipefail
KIT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=lib.sh
source "$KIT_DIR/lib.sh"

[[ ${MAINTENANCE_WINDOW_CONFIRMED:-} == YES ]] || fail "set MAINTENANCE_WINDOW_CONFIRMED=YES only after admins stop Budget saves, onboarding, and new sessions"
[[ ${CLOCK_CHECK_CONFIRMED:-} == YES ]] || fail "set CLOCK_CHECK_CONFIRMED=YES only after confirming the host clock is authoritative or the bundle timestamp was anonymized"
[[ ! -e "$EVIDENCE_DIR" ]] || fail "EVIDENCE_DIR already exists; use a new directory for this attempt"
source_chart=$(require_openhands_version "$EXPECTED_SOURCE_VERSION")
source_image=$(openhands_image)
printf 'Validating core workload readiness...\n'
require_core_workloads_ready
printf 'Validating PostgreSQL client and connectivity...\n'
ensure_database_access

umask 077
mkdir -p "$EVIDENCE_DIR/before"
printf '%s\n' "$source_chart" > "$EVIDENCE_DIR/openhands-chart-before.txt"
printf '%s\n' "$source_image" > "$EVIDENCE_DIR/openhands-image-before.txt"
printf '%s\n' "$DATABASE_MODE" > "$EVIDENCE_DIR/database-mode.txt"
k exec "$DB_EXEC_POD" -c "$DB_EXEC_CONTAINER" -- sh -c 'psql --version; pg_dump --version; pg_restore --version' > "$EVIDENCE_DIR/postgres-client-versions.txt"

printf 'Recording org roster and pre-upgrade state...\n'
list_team_orgs > "$EVIDENCE_DIR/orgs.txt"
[[ -s "$EVIDENCE_DIR/orgs.txt" ]] || fail "no team organizations found"
list_team_org_members > "$EVIDENCE_DIR/org-members.tsv"
[[ -s "$EVIDENCE_DIR/org-members.tsv" ]] || fail "no team organization members found"
psql_db "$APP_DB" -P pager=off -c 'SELECT version_num FROM alembic_version;' > "$EVIDENCE_DIR/alembic-before.txt"
k get cronjob -o wide > "$EVIDENCE_DIR/cronjobs-before.txt"
record_and_suspend_cronjob "$BUDGET_CRONJOB" "$EVIDENCE_DIR/budget-cronjob-pre-upgrade.txt"
record_and_suspend_cronjob "$MAINTENANCE_CRONJOB" "$EVIDENCE_DIR/maintenance-cronjob-pre-upgrade.txt"

printf 'Snapshotting LiteLLM policy and spend for every team organization...\n'
snapshot_all_orgs "$EVIDENCE_DIR/orgs.txt" "$EVIDENCE_DIR/before"

printf 'Dumping LiteLLM database...\n'
pg_dump_database "$LITELLM_DB" "$EVIDENCE_DIR/litellm-before.dump"
validate_dump "$EVIDENCE_DIR/litellm-before.dump"

printf 'Dumping OpenHands budget tables...\n'
pg_dump_budget_tables "$EVIDENCE_DIR/app-budget-before.dump"
validate_dump "$EVIDENCE_DIR/app-budget-before.dump"

printf 'Building fail-closed migration plan...\n'
python3 "$KIT_DIR/tools/build_migration_plan.py" \
  --snapshots "$EVIDENCE_DIR/before" \
  --orgs "$EVIDENCE_DIR/orgs.txt" \
  --roster "$EVIDENCE_DIR/org-members.tsv" \
  --reset-day "$MIGRATION_RESET_DAY" \
  --output "$EVIDENCE_DIR/migration-plan.json"
python3 "$KIT_DIR/tools/render_migration_sql.py" \
  --plan "$EVIDENCE_DIR/migration-plan.json" \
  --output "$EVIDENCE_DIR/apply-migration.sql"

cat > "$EVIDENCE_DIR/NEXT_STEP.txt" <<'EOF'
MIGRATION PLAN PASSED.

Review migration-plan.json for every organization. The plan preserves each
existing LiteLLM absolute team/member cap as the OpenHands monthly limit,
initializes the current cycle baselines to zero, and represents private member
caps as explicit overrides. Shared-budget members receive no override. Team
model allowlists must remain unchanged throughout the migration.

Planning rejects non-null per-key budgets because OpenHands cannot own them;
key identifiers still must stay unchanged.

Only after review, run 02-apply.sh with MIGRATION_PLAN_REVIEW_CONFIRMED=YES.
Do not deploy the target release until APPLY PASSED.
EOF

write_checksums "$EVIDENCE_DIR"
chmod -R go-rwx "$EVIDENCE_DIR"
printf '\nPLAN PASSED. Evidence: %s\n' "$EVIDENCE_DIR"
printf 'Review migration-plan.json and apply-migration.sql before continuing.\n'
