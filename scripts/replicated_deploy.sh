#!/usr/bin/env bash
# Deploy one KOTS release to a Replicated Embedded Cluster instance.
#
#   KOTS_BASE=https://admin.unstable.staging.all-hands-testing.dev:30000 \
#   KOTS_PASSWORD=... KOTS_CURSOR=418 scripts/replicated_deploy.sh
set -euo pipefail

APP="${APP:-openhands}"
: "${KOTS_BASE:?}" "${KOTS_PASSWORD:?}" "${KOTS_CURSOR:?}"
# Whole-run budget in minutes; every wait loop below shares this one deadline.
TIMEOUT_MINUTES="${TIMEOUT_MINUTES:-25}"
DEADLINE=$(( $(date +%s) + TIMEOUT_MINUTES * 60 ))
waiting() { [ "$(date +%s)" -lt "$DEADLINE" ]; }

UP="$KOTS_BASE/api/v1/upgrade-service/app/$APP"
JAR="$(mktemp)"; trap 'rm -f "$JAR"' EXIT

fail() { echo "::error::$*"; exit 1; }
api()  { curl -sS -k --max-time "${TMO:-30}" -b "$JAR" -c "$JAR" "$@"; }
post() { api -H 'Content-Type: application/json' -X POST "$@"; }
ok()   { jq -e '.success == true' >/dev/null 2>&1; }

# A 401 still sets a cookie, so a non-empty jar proves nothing.
CODE="$(curl -sS -k -c "$JAR" -o /dev/null -w '%{http_code}' --max-time 30 \
  -X POST "$KOTS_BASE/api/v1/login" -H 'Content-Type: application/json' \
  --data "$(jq -nc --arg p "$KOTS_PASSWORD" '{password:$p}')")"
[ "$CODE" = 200 ] || fail "login to $KOTS_BASE returned HTTP $CODE"

# --- select --------------------------------------------------------------
# KOTS_CURSOR is the channel sequence; versionLabel is not unique.
TMO=180 post -d '{}' "$KOTS_BASE/api/v1/app/$APP/updatecheck" >/dev/null
TARGET="$(api "$KOTS_BASE/api/v1/app/$APP/updates" \
  | jq -c --arg c "$KOTS_CURSOR" '.updates[] | select(.updateCursor == $c)')"
[ -n "$TARGET" ] || fail "cursor $KOTS_CURSOR is not an available update for $APP"
[ "$(jq -r .isDeployable <<<"$TARGET")" = true ] \
  || fail "cursor $KOTS_CURSOR not deployable: $(jq -r '.nonDeployableCause // "unknown"' <<<"$TARGET")"

FROM="$(api "$KOTS_BASE/api/v1/apps" | jq -r '.apps[0].downstream.currentVersion | "\(.versionLabel) @ \(.updateCursor)"')"
echo "budget: ${TIMEOUT_MINUTES}m"
echo "deploying: $FROM -> $(jq -r .versionLabel <<<"$TARGET") @ $KOTS_CURSOR"

# --- boot the upgrade service -------------------------------------------
post --data "$(jq -c '{versionLabel, updateCursor, channelId}' <<<"$TARGET")" \
  "$KOTS_BASE/api/v1/app/$APP/start-upgrade-service" | ok \
  || fail "start-upgrade-service rejected"

# Task goes "starting" then EMPTY once up. Empty means ready, not pending.
META=""
while waiting; do
  [ "$(TMO=10 api "$KOTS_BASE/api/v1/app/$APP/task/upgrade-service" | jq -r '.status // ""')" = upgrade-failed ] \
    && fail "upgrade service failed to start"
  META="$(TMO=10 api "$UP" || true)"; ok <<<"$META" && break
  META=""; sleep 5
done
[ -n "$META" ] || fail "upgrade service never came up"

# --- config: read values, write them straight back -----------------------
if [ "$(jq -r .isConfigurable <<<"$META")" = true ]; then
  TMO=60 api "$UP/config" | jq -c '{configGroups}' >/tmp/cfg.json
  TMO=60 api -H 'Content-Type: application/json' -X PUT --data-binary @/tmp/cfg.json "$UP/config" | ok \
    || fail "config rejected — new release likely added a required item with no default"
fi

# --- preflights ----------------------------------------------------------
if [ "$(jq -r .hasPreflight <<<"$META")" = true ]; then
  TMO=60 post "$UP/preflight/run" >/dev/null
  R=""
  while waiting; do
    R="$(api "$UP/preflight/result")"
    # .preflightResult.result is a JSON string, null until the run finishes.
    jq -e '.preflightResult.result' <<<"$R" >/dev/null 2>&1 && break
    R=""; sleep 5
  done
  [ -n "$R" ] || fail "timed out waiting for preflight results"
  BAD="$(jq -r .preflightResult.result <<<"$R" | jq -c '[.results[] | select(.isPass != true)]')"
  [ "$(jq -r .preflightResult.hasFailingStrictPreflights <<<"$R")" = true ] && fail "preflights failed: $BAD"
  echo "preflights ok (non-passing: $BAD)"
fi

# --- deploy --------------------------------------------------------------
TMO=120 post -d '{"isSkipPreflights":false,"continueWithFailedPreflights":false}' "$UP/deploy" | ok \
  || fail "deploy rejected"

# The task stays empty for the whole deploy; downstream currentVersion is the
# only progress signal. An unreachable console means kotsadm is restarting.
while waiting; do
  [ "$(TMO=10 api "$KOTS_BASE/api/v1/app/$APP/task/upgrade-service" 2>/dev/null | jq -r '.status // ""')" = upgrade-failed ] \
    && fail "upgrade-failed"
  CUR="$(TMO=10 api "$KOTS_BASE/api/v1/apps" 2>/dev/null | jq -c '.apps[0].downstream.currentVersion | {updateCursor, sequence, status}' || true)"
  echo "  ${CUR:-<console unreachable, waiting>}"
  case "$(jq -r '"\(.updateCursor) \(.status)"' <<<"${CUR:-\{\}}" 2>/dev/null)" in
    "$KOTS_CURSOR deployed") DONE=1; break ;;
    "$KOTS_CURSOR failed")   fail "instance reported the deploy failed — see the admin console version history" ;;
  esac
  sleep 10
done
[ "${DONE:-}" ] || fail "timed out waiting for cursor $KOTS_CURSOR to deploy"

# --- verify every statusInformer ----------------------------------------
# Response key is "appstatus", all lowercase.
S=""
while waiting; do
  S="$(TMO=15 api "$KOTS_BASE/api/v1/app/$APP/status" 2>/dev/null || true)"
  [ "$(jq -r '.appstatus.state // ""' <<<"${S:-\{\}}")" = ready ] && break
  sleep 10
done
[ -n "$S" ] || fail "timed out waiting for the app to report ready"
jq -c '.appstatus | {state, sequence, unhealthy: [.resourceStates[] | select(.state != "ready")]}' <<<"$S"
[ "$(jq -r .appstatus.state <<<"$S")" = ready ] \
  || fail "deployed but unhealthy: $(jq -c '[.appstatus.resourceStates[] | select(.state != "ready")]' <<<"$S")"
echo "deployed cursor $KOTS_CURSOR, app ready"
