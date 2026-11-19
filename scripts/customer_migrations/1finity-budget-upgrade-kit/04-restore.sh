#!/usr/bin/env bash

set -euo pipefail
KIT_DIR=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=lib.sh
source "$KIT_DIR/lib.sh"

[[ ${RESTORE_CONFIRMED:-} == YES ]] || fail "set RESTORE_CONFIRMED=YES only after reviewing the mismatch report"
mismatch_file=${RESTORE_MISMATCH_FILE:-$EVIDENCE_DIR/mismatched-orgs.txt}
[[ -s "$mismatch_file" ]] || fail "no mismatched orgs are recorded in $mismatch_file"
require_openhands_version "$EXPECTED_TARGET_VERSION" >/dev/null
ensure_database_access

mapfile -t approved_orgs < <(printf '%s\n' "${RESTORE_ORGS:-}" | tr ',' '\n' | sed '/^[[:space:]]*$/d' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
(( ${#approved_orgs[@]} > 0 )) || fail "set RESTORE_ORGS to a comma-separated subset of mismatched org IDs"

umask 077
mkdir -p "$EVIDENCE_DIR/restored"
: > "$EVIDENCE_DIR/restored/approved-orgs.txt"
for org_id in "${approved_orgs[@]}"; do
  grep -Fxq "$org_id" "$mismatch_file" || fail "$org_id is not listed in $mismatch_file"
  grep -Fxq "$org_id" "$EVIDENCE_DIR/orgs.txt" || fail "$org_id was not in the migration plan"
  [[ -f "$EVIDENCE_DIR/before/$org_id.json" ]] || fail "missing before snapshot for $org_id"
  verify_evidence_file "$EVIDENCE_DIR" "before/$org_id.json"
  printf '%s\n' "$org_id" >> "$EVIDENCE_DIR/restored/approved-orgs.txt"
done

python3 - "$EVIDENCE_DIR/restored/approved-orgs.txt" > "$EVIDENCE_DIR/restored/remove-ownership.sql" <<'PY'
import sys
from pathlib import Path
from uuid import UUID
orgs = [str(UUID(line)) for line in Path(sys.argv[1]).read_text().splitlines() if line]
values = ", ".join(f"'{org}'::uuid" for org in orgs)
print("BEGIN;")
print("UPDATE org_budget_settings")
print("SET enabled=false, default_user_monthly_limit=NULL,")
print("    litellm_last_sync_status='skipped', litellm_last_sync_error=NULL")
print(f"WHERE org_id IN ({values});")
print(f"DELETE FROM org_user_budget_override WHERE org_id IN ({values});")
print("COMMIT;")
PY

printf 'Suspending budget processing and removing OpenHands ownership for approved orgs...\n'
k patch cronjob "$BUDGET_CRONJOB" --type merge -p '{"spec":{"suspend":true}}'
k patch cronjob "$MAINTENANCE_CRONJOB" --type merge -p '{"spec":{"suspend":true}}'
psql_db "$APP_DB" -f - < "$EVIDENCE_DIR/restored/remove-ownership.sql"
list_managed_orgs > "$EVIDENCE_DIR/restored/managed-orgs-after-removal.txt"
for org_id in "${approved_orgs[@]}"; do
  if grep -Fxq "$org_id" "$EVIDENCE_DIR/restored/managed-orgs-after-removal.txt"; then
    fail "$org_id is still managed; refusing LiteLLM restore"
  fi
done

for org_id in "${approved_orgs[@]}"; do
  printf 'Restoring LiteLLM policy for %s...\n' "$org_id"
  restore_snapshot "$org_id" "$EVIDENCE_DIR/before/$org_id.json" > "$EVIDENCE_DIR/restored/$org_id-operation.json"
  llsnap "$org_id" > "$EVIDENCE_DIR/restored/$org_id.json"
  if ! diff -u "$EVIDENCE_DIR/before/$org_id.json" "$EVIDENCE_DIR/restored/$org_id.json" > "$EVIDENCE_DIR/restored/$org_id.diff"; then
    fail "restore verification failed for $org_id; maintenance window must remain active"
  fi
  printf 'Restore verified for %s.\n' "$org_id"
done

write_checksums "$EVIDENCE_DIR"
chmod -R go-rwx "$EVIDENCE_DIR"
printf '\nRESTORE PASSED for %s org(s). They are now unmanaged by OpenHands.\n' "${#approved_orgs[@]}"
printf 'Keep both CronJobs suspended and create a new migration plan before retrying.\n'
printf 'Do not pg_restore the LiteLLM database after live traffic; that would rewind spend.\n'
