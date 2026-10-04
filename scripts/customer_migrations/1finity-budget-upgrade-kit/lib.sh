#!/usr/bin/env bash

set -euo pipefail

KIT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CONFIG_FILE=${CONFIG_FILE:-$KIT_DIR/config.env}

fail() {
  printf 'MIGRATION HALTED (FAIL CLOSED): %s\n' "$*" >&2
  printf 'Keep the maintenance window active and all migration writers suspended. Do not retry, restore, or resume traffic until the failure is understood and the evidence is reviewed.\n' >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || fail "required command not found: $1"
}

require_config() {
  [[ -f "$CONFIG_FILE" ]] || fail "missing $CONFIG_FILE; copy config.env.example and review it"
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
  : "${KUBECTL:?KUBECTL is required}"
  : "${NAMESPACE:?NAMESPACE is required}"
  : "${OPENHANDS_DEPLOYMENT:?OPENHANDS_DEPLOYMENT is required}"
  : "${OPENHANDS_CONTAINER:?OPENHANDS_CONTAINER is required}"
  : "${LITELLM_DEPLOYMENT:?LITELLM_DEPLOYMENT is required}"
  : "${EXPECTED_SOURCE_VERSION:?EXPECTED_SOURCE_VERSION is required}"
  : "${EXPECTED_TARGET_VERSION:?EXPECTED_TARGET_VERSION is required}"
  : "${DATABASE_MODE:?DATABASE_MODE is required}"
  : "${DB_EXEC_POD:?DB_EXEC_POD is required}"
  : "${DB_EXEC_CONTAINER:?DB_EXEC_CONTAINER is required}"
  : "${APP_DB:?APP_DB is required}"
  : "${LITELLM_DB:?LITELLM_DB is required}"
  : "${BUDGET_CRONJOB:?BUDGET_CRONJOB is required}"
  : "${MAINTENANCE_CRONJOB:?MAINTENANCE_CRONJOB is required}"
  : "${PREFLIGHT_JOB:?PREFLIGHT_JOB is required}"
  : "${GATE_JOB:?GATE_JOB is required}"
  : "${MIGRATION_RESET_DAY:?MIGRATION_RESET_DAY is required}"
  : "${MIGRATION_PLAN_MAX_AGE_SECONDS:?MIGRATION_PLAN_MAX_AGE_SECONDS is required}"
  : "${BUDGET_CRON_VERIFICATION_RUNS:?BUDGET_CRON_VERIFICATION_RUNS is required}"
  : "${EVIDENCE_DIR:?EVIDENCE_DIR is required}"
  [[ "$DATABASE_MODE" == external || "$DATABASE_MODE" == embedded ]] || fail "DATABASE_MODE must be external or embedded"
  [[ "$MIGRATION_RESET_DAY" =~ ^([1-9]|1[0-9]|2[0-8])$ ]] || fail "MIGRATION_RESET_DAY must be between 1 and 28"
  [[ "$MIGRATION_PLAN_MAX_AGE_SECONDS" =~ ^[1-9][0-9]*$ ]] || fail "MIGRATION_PLAN_MAX_AGE_SECONDS must be a positive integer"
  [[ "$BUDGET_CRON_VERIFICATION_RUNS" =~ ^[1-9][0-9]*$ ]] || fail "BUDGET_CRON_VERIFICATION_RUNS must be a positive integer"
  if [[ "$DATABASE_MODE" == external ]]; then
    : "${DB_HELPER_SOURCE_INIT_CONTAINER:?DB_HELPER_SOURCE_INIT_CONTAINER is required in external mode}"
  fi
  [[ "$EVIDENCE_DIR" = /* ]] || fail "EVIDENCE_DIR must be an absolute path"
  read -r -a KUBECTL_COMMAND <<< "$KUBECTL"
}

k() {
  "${KUBECTL_COMMAND[@]}" -n "$NAMESPACE" "$@"
}

openhands_image() {
  k get deploy "$OPENHANDS_DEPLOYMENT" -o "jsonpath={.spec.template.spec.containers[?(@.name==\"$OPENHANDS_CONTAINER\")].image}"
}

openhands_chart() {
  k get deploy "$OPENHANDS_DEPLOYMENT" -o 'jsonpath={.metadata.labels.helm\.sh/chart}'
}

require_openhands_version() {
  local expected=$1
  local chart
  chart=$(openhands_chart)
  [[ -n "$chart" ]] || fail "could not read the Helm chart label from deployment/$OPENHANDS_DEPLOYMENT"
  [[ "$chart" == "openhands-$expected" ]] || fail "expected OpenHands chart openhands-$expected but deployment uses $chart"
  printf '%s\n' "$chart"
}

require_core_workloads_ready() {
  k rollout status "deployment/$OPENHANDS_DEPLOYMENT" --timeout=120s
  k rollout status "deployment/$LITELLM_DEPLOYMENT" --timeout=120s
}



record_and_suspend_cronjob() {
  local cronjob=$1
  local state_file=$2
  if k get cronjob "$cronjob" >/dev/null 2>&1; then
    k get cronjob "$cronjob" -o json | jq -r '"present=true\nsuspend=" + ((.spec.suspend // false)|tostring) + "\nschedule=" + .spec.schedule' > "$state_file"
    k patch cronjob "$cronjob" --type merge -p '{"spec":{"suspend":true}}'
  else
    printf 'present=false\n' > "$state_file"
  fi
}

restore_cronjob_state() {
  local cronjob=$1
  local state_file=$2
  local final_file=$3
  if grep -Fxq 'present=true' "$state_file"; then
    local original_suspend
    original_suspend=$(awk -F= '$1 == "suspend" {print $2}' "$state_file")
    [[ "$original_suspend" == true || "$original_suspend" == false ]] || fail "invalid recorded suspend state for $cronjob"
    k get cronjob "$cronjob" >/dev/null 2>&1 || fail "cronjob/$cronjob existed before upgrade but is now missing"
    k patch cronjob "$cronjob" --type merge -p "{\"spec\":{\"suspend\":$original_suspend}}"
    k get cronjob "$cronjob" -o jsonpath='{.metadata.name}{" schedule="}{.spec.schedule}{" suspend="}{.spec.suspend}{"\n"}' > "$final_file"
  else
    printf '%s absent before upgrade; current state unchanged\n' "$cronjob" > "$final_file"
  fi
}


create_external_db_helper() {
  if k get pod "$DB_EXEC_POD" >/dev/null 2>&1; then
    [[ $(k get pod "$DB_EXEC_POD" -o jsonpath='{.metadata.labels.app\.kubernetes\.io/managed-by}') == "1finity-budget-upgrade-kit" ]] \
      || fail "pod/$DB_EXEC_POD already exists and is not managed by this kit"
    local phase
    phase=$(k get pod "$DB_EXEC_POD" -o jsonpath='{.status.phase}')
    if [[ "$phase" == Failed || "$phase" == Succeeded || "$phase" == Unknown || -z "$phase" ]]; then
      k delete pod "$DB_EXEC_POD" --wait=true
    fi
  fi
  if ! k get pod "$DB_EXEC_POD" >/dev/null 2>&1; then
    k get deploy "$OPENHANDS_DEPLOYMENT" -o json | jq \
      --arg source "$DB_HELPER_SOURCE_INIT_CONTAINER" \
      --arg pod "$DB_EXEC_POD" \
      --arg container "$DB_EXEC_CONTAINER" '
        .spec.template.spec as $podspec
        | ($podspec.initContainers[] | select(.name == $source)) as $source_container
        | {
            apiVersion: "v1",
            kind: "Pod",
            metadata: {
              name: $pod,
              labels: {"app.kubernetes.io/managed-by": "1finity-budget-upgrade-kit"}
            },
            spec: {
              restartPolicy: "Never",
              automountServiceAccountToken: false,
              imagePullSecrets: ($podspec.imagePullSecrets // []),
              containers: [{
                name: $container,
                image: $source_container.image,
                imagePullPolicy: "Never",
                command: ["sh", "-c", "trap : TERM INT; sleep infinity & wait"],
                env: [
                  ($source_container.env // [])[]
                  | select(
                      .name == "DB_HOST"
                      or .name == "DB_PORT"
                      or .name == "DB_USER"
                      or .name == "DB_NAME"
                      or .name == "DB_SSL_MODE"
                      or .name == "DB_PASS"
                    )
                ],
                resources: {
                  requests: {cpu: "10m", memory: "32Mi"},
                  limits: {cpu: "500m", memory: "256Mi"}
                }
              }]
            }
          }
      ' | k apply -f -
  fi
  k wait --for=condition=Ready "pod/$DB_EXEC_POD" --timeout=120s
}

ensure_database_access() {
  if [[ "$DATABASE_MODE" == external ]]; then
    create_external_db_helper
  else
    k get pod "$DB_EXEC_POD" >/dev/null
  fi
  k exec "$DB_EXEC_POD" -c "$DB_EXEC_CONTAINER" -- sh -c '
    command -v psql >/dev/null
    command -v pg_dump >/dev/null
    command -v pg_restore >/dev/null
    password=${DB_PASS:-${POSTGRES_PASSWORD:-${POSTGRES_POSTGRES_PASSWORD:-}}}
    user=${DB_USER:-${POSTGRES_USER:-postgres}}
    test -n "$password" || { echo "Postgres password environment variable not found" >&2; exit 1; }
    test -z "${DB_HOST:-}" || export PGHOST="$DB_HOST"
    test -z "${DB_PORT:-}" || export PGPORT="$DB_PORT"
    test -z "${DB_SSL_MODE:-}" || export PGSSLMODE="$DB_SSL_MODE"
    server_version_num=$(PGPASSWORD="$password" psql -U "$user" -v ON_ERROR_STOP=1 -d "$DB_NAME" -Atc "SHOW server_version_num")
    client_major=$(pg_dump --version | sed -E "s/.* ([0-9]+)(\\..*)?$/\\1/")
    server_major=$((server_version_num / 10000))
    test "$client_major" -ge "$server_major" || {
      echo "pg_dump major version $client_major is older than PostgreSQL server major version $server_major" >&2
      exit 1
    }
  '
  psql_db "$APP_DB" -Atc 'SELECT 1' >/dev/null
  psql_db "$LITELLM_DB" -Atc 'SELECT 1' >/dev/null
}

remove_external_db_helper() {
  if [[ "$DATABASE_MODE" == external ]] && k get pod "$DB_EXEC_POD" >/dev/null 2>&1; then
    [[ $(k get pod "$DB_EXEC_POD" -o jsonpath='{.metadata.labels.app\.kubernetes\.io/managed-by}') == "1finity-budget-upgrade-kit" ]] \
      || fail "refusing to delete unmanaged pod/$DB_EXEC_POD"
    k delete pod "$DB_EXEC_POD" --wait=true
  fi
}

psql_db() {
  local database=$1
  shift
  k exec -i "$DB_EXEC_POD" -c "$DB_EXEC_CONTAINER" -- sh -c '
    database=$1
    shift
    password=${DB_PASS:-${POSTGRES_PASSWORD:-${POSTGRES_POSTGRES_PASSWORD:-}}}
    user=${DB_USER:-${POSTGRES_USER:-postgres}}
    test -n "$password" || { echo "Postgres password environment variable not found" >&2; exit 1; }
    test -z "${DB_HOST:-}" || export PGHOST="$DB_HOST"
    test -z "${DB_PORT:-}" || export PGPORT="$DB_PORT"
    test -z "${DB_SSL_MODE:-}" || export PGSSLMODE="$DB_SSL_MODE"
    PGPASSWORD="$password" exec psql -U "$user" -v ON_ERROR_STOP=1 -d "$database" "$@"
  ' sh "$database" "$@"
}

pg_dump_database() {
  local database=$1
  local destination=$2
  k exec "$DB_EXEC_POD" -c "$DB_EXEC_CONTAINER" -- sh -c '
    database=$1
    password=${DB_PASS:-${POSTGRES_PASSWORD:-${POSTGRES_POSTGRES_PASSWORD:-}}}
    user=${DB_USER:-${POSTGRES_USER:-postgres}}
    test -n "$password" || { echo "Postgres password environment variable not found" >&2; exit 1; }
    test -z "${DB_HOST:-}" || export PGHOST="$DB_HOST"
    test -z "${DB_PORT:-}" || export PGPORT="$DB_PORT"
    test -z "${DB_SSL_MODE:-}" || export PGSSLMODE="$DB_SSL_MODE"
    PGPASSWORD="$password" exec pg_dump -U "$user" -Fc -d "$database"
  ' sh "$database" > "$destination"
}

pg_dump_budget_tables() {
  local destination=$1
  local maintenance_table
  maintenance_table=$(psql_db "$APP_DB" -Atc "SELECT CASE WHEN to_regclass('public.maintenance_task') IS NOT NULL THEN 'maintenance_task' WHEN to_regclass('public.maintenance_tasks') IS NOT NULL THEN 'maintenance_tasks' ELSE '' END")
  [[ -n "$maintenance_table" ]] || fail "neither maintenance_task nor maintenance_tasks exists"
  k exec "$DB_EXEC_POD" -c "$DB_EXEC_CONTAINER" -- sh -c '
    database=$1
    maintenance_table=$2
    password=${DB_PASS:-${POSTGRES_PASSWORD:-${POSTGRES_POSTGRES_PASSWORD:-}}}
    user=${DB_USER:-${POSTGRES_USER:-postgres}}
    test -n "$password" || { echo "Postgres password environment variable not found" >&2; exit 1; }
    test -z "${DB_HOST:-}" || export PGHOST="$DB_HOST"
    test -z "${DB_PORT:-}" || export PGPORT="$DB_PORT"
    test -z "${DB_SSL_MODE:-}" || export PGSSLMODE="$DB_SSL_MODE"
    PGPASSWORD="$password" exec pg_dump -U "$user" -Fc -d "$database" \
      -t org_budget_settings \
      -t org_user_budget_override \
      -t org_budget_threshold \
      -t org_budget_cycle_baseline \
      -t "$maintenance_table"
  ' sh "$APP_DB" "$maintenance_table" > "$destination"
}

validate_dump() {
  local dump_file=$1
  [[ -s "$dump_file" ]] || fail "empty dump: $dump_file"
  k exec -i "$DB_EXEC_POD" -c "$DB_EXEC_CONTAINER" -- pg_restore -l < "$dump_file" >/dev/null
}

list_team_orgs() {
  psql_db "$APP_DB" -Atc '
    SELECT o.id
    FROM org o
    LEFT JOIN "user" u ON u.id = o.id
    WHERE u.id IS NULL
    ORDER BY o.id;
  '
}

list_team_org_members() {
  psql_db "$APP_DB" -At -F $'\t' -c '
    SELECT member.org_id, member.user_id
    FROM org_member member
    JOIN org ON org.id = member.org_id
    LEFT JOIN "user" personal ON personal.id = org.id
    WHERE personal.id IS NULL
    ORDER BY member.org_id, member.user_id;
  '
}


list_managed_orgs() {
  psql_db "$APP_DB" -Atc '
    SELECT s.org_id
    FROM org_budget_settings s
    LEFT JOIN "user" u ON u.id = s.org_id
    WHERE u.id IS NULL
      AND (
        s.enabled
        OR s.default_user_monthly_limit IS NOT NULL
        OR s.litellm_last_sync_status IN ('"'"'pending'"'"', '"'"'success'"'"', '"'"'error'"'"')
        OR EXISTS (
          SELECT 1
          FROM org_user_budget_override o
          WHERE o.org_id = s.org_id
        )
      )
    ORDER BY s.org_id;
  '
}

llsnap() {
  local org_id=$1
  k exec "deploy/$OPENHANDS_DEPLOYMENT" -- \
    python -c "$(cat "$KIT_DIR/tools/snapshot_litellm.py")" "$org_id"
}

restore_snapshot() {
  local org_id=$1
  local snapshot_file=$2
  k exec -i "deploy/$OPENHANDS_DEPLOYMENT" -- python -c "$(cat "$KIT_DIR/tools/restore_snapshot.py")" "$org_id" < "$snapshot_file"
}

snapshot_all_orgs() {
  local org_file=$1
  local destination=$2
  mkdir -p "$destination"
  while IFS= read -r org_id; do
    [[ -n "$org_id" ]] || continue
    printf 'Snapshotting org %s\n' "$org_id"
    llsnap "$org_id" > "$destination/$org_id.json"
    python3 -m json.tool "$destination/$org_id.json" >/dev/null
  done < "$org_file"
}

write_checksums() {
  local directory=$1
  (
    cd "$directory"
    find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
  )
}

verify_evidence_file() {
  local directory=$1
  local relative_path=$2
  local checksum_line
  checksum_line=$(awk -v path="./$relative_path" '$2 == path {print}' "$directory/SHA256SUMS")
  [[ -n "$checksum_line" ]] || fail "no checksum recorded for $relative_path"
  [[ $(printf '%s\n' "$checksum_line" | wc -l | tr -d ' ') == 1 ]] || fail "multiple checksums recorded for $relative_path"
  (cd "$directory" && printf '%s\n' "$checksum_line" | sha256sum -c - >/dev/null)
}


verify_checksums() {
  local directory=$1
  (cd "$directory" && sha256sum -c SHA256SUMS)
}

require_config
require_command jq
require_command python3
require_command sha256sum
