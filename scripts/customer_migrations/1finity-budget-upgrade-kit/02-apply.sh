#!/usr/bin/env bash

set -euo pipefail
KIT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=lib.sh
source "$KIT_DIR/lib.sh"

[[ ${MIGRATION_PLAN_REVIEW_CONFIRMED:-} == YES ]] || fail "set MIGRATION_PLAN_REVIEW_CONFIRMED=YES only after reviewing every org in migration-plan.json"
[[ -d "$EVIDENCE_DIR/before" ]] || fail "missing plan evidence in $EVIDENCE_DIR"
verify_checksums "$EVIDENCE_DIR" >/dev/null || fail "plan evidence checksum verification failed"
require_openhands_version "$EXPECTED_SOURCE_VERSION" >/dev/null
require_core_workloads_ready
ensure_database_access

python3 - "$EVIDENCE_DIR/migration-plan.json" "$MIGRATION_PLAN_MAX_AGE_SECONDS" <<'PY'
import json, sys
from datetime import UTC, datetime
plan = json.load(open(sys.argv[1]))
generated = datetime.fromisoformat(plan["generated_at"])
age = (datetime.now(UTC) - generated.astimezone(UTC)).total_seconds()
if age < 0 or age > int(sys.argv[2]):
    raise SystemExit(f"migration plan age {age:.0f}s is outside the allowed window; create a new plan")
PY

umask 077
printf 'Checking that organizations and memberships did not change after planning...\n'
list_team_orgs > "$EVIDENCE_DIR/orgs-pre-apply.txt"
list_team_org_members > "$EVIDENCE_DIR/org-members-pre-apply.tsv"
diff -u "$EVIDENCE_DIR/orgs.txt" "$EVIDENCE_DIR/orgs-pre-apply.txt" > "$EVIDENCE_DIR/orgs-pre-apply.diff" || fail "organization set changed after planning; start over"
diff -u "$EVIDENCE_DIR/org-members.tsv" "$EVIDENCE_DIR/org-members-pre-apply.tsv" > "$EVIDENCE_DIR/org-members-pre-apply.diff" || fail "organization membership changed after planning; start over"

printf 'Checking that LiteLLM policy and spend did not change after planning...\n'
rm -rf "$EVIDENCE_DIR/pre-apply"
mkdir -p "$EVIDENCE_DIR/pre-apply"
snapshot_all_orgs "$EVIDENCE_DIR/orgs.txt" "$EVIDENCE_DIR/pre-apply"
python3 "$KIT_DIR/tools/compare_snapshots.py" \
  --before "$EVIDENCE_DIR/before" \
  --after "$EVIDENCE_DIR/pre-apply" \
  --orgs "$EVIDENCE_DIR/orgs.txt" \
  --report "$EVIDENCE_DIR/pre-apply-snapshot-report.json" \
  --mismatched "$EVIDENCE_DIR/pre-apply-mismatched-orgs.txt"

printf 'Applying reviewed migration plan transactionally...\n'
verify_evidence_file "$EVIDENCE_DIR" migration-plan.json
verify_evidence_file "$EVIDENCE_DIR" apply-migration.sql
psql_db "$APP_DB" -f - < "$EVIDENCE_DIR/apply-migration.sql"

printf 'Checking that every team org, and only those orgs, is now managed...\n'
list_managed_orgs > "$EVIDENCE_DIR/managed-orgs-after-apply.txt"
diff -u "$EVIDENCE_DIR/orgs.txt" "$EVIDENCE_DIR/managed-orgs-after-apply.txt" > "$EVIDENCE_DIR/managed-orgs-after-apply.diff" || fail "managed organization set differs from migration plan; DO NOT UPGRADE"

printf 'Proving SQL staging did not alter LiteLLM...\n'
rm -rf "$EVIDENCE_DIR/after-apply"
mkdir -p "$EVIDENCE_DIR/after-apply"
snapshot_all_orgs "$EVIDENCE_DIR/orgs.txt" "$EVIDENCE_DIR/after-apply"
python3 "$KIT_DIR/tools/compare_snapshots.py" \
  --before "$EVIDENCE_DIR/before" \
  --after "$EVIDENCE_DIR/after-apply" \
  --orgs "$EVIDENCE_DIR/orgs.txt" \
  --report "$EVIDENCE_DIR/after-apply-snapshot-report.json" \
  --mismatched "$EVIDENCE_DIR/after-apply-mismatched-orgs.txt"

psql_db "$APP_DB" -P pager=off -c '
  SELECT count(*) AS settings_rows,
         count(*) FILTER (WHERE enabled) AS enabled_rows,
         count(*) FILTER (WHERE monthly_limit > 0) AS rows_with_positive_team_limit,
         count(*) FILTER (WHERE default_user_monthly_limit IS NOT NULL) AS rows_with_default_limit,
         count(*) FILTER (WHERE litellm_last_sync_status = '"'"'success'"'"') AS synchronized_rows
  FROM org_budget_settings s
  LEFT JOIN "user" personal ON personal.id = s.org_id
  WHERE personal.id IS NULL;
  SELECT count(*) AS explicit_private_member_overrides
  FROM org_user_budget_override;
' > "$EVIDENCE_DIR/post-apply-summary.txt"

cat > "$EVIDENCE_DIR/NEXT_STEP.txt" <<'EOF'
MIGRATION APPLY PASSED.

Deploy the approved target release in the Admin Console. Keep the pre-upgrade
preflight in Acknowledge mode and the post-upgrade reconciliation gate in
Strict mode. The artifacts must contain exactly every ID in orgs.txt, with no
blocking findings or cap drift.

Do not resume Budget saves, onboarding, or new sessions. After the release
reaches a terminal state, run 03-verify.sh.
EOF

write_checksums "$EVIDENCE_DIR"
chmod -R go-rwx "$EVIDENCE_DIR"
printf '\nAPPLY PASSED. OpenHands ownership is staged for %s org(s).\n' "$(wc -l < "$EVIDENCE_DIR/orgs.txt" | tr -d ' ')"
printf 'Do not deploy unless managed-orgs-after-apply.diff is empty.\n'
