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
# KOTS returns its rejection reason in .error; print that, not just "rejected".
why()  { local b; b="$(cat)"; jq -re '.error // empty' <<<"$b" 2>/dev/null || printf '%s' "${b:-<empty response>}"; }

# A 401 still sets a cookie, so a non-empty jar proves nothing.
# Retried: kotsadm is often mid-restart when a release lands back-to-back with
# the previous one, and a refused connect must not read as a bad password.
BODY="$(jq -nc --arg p "$KOTS_PASSWORD" '{password:$p}')"
LOGIN_DEADLINE=$(( $(date +%s) + 120 ))
while :; do
  CODE="$(curl -sS -k -c "$JAR" -o /dev/null -w '%{http_code}' --max-time 30 \
    -X POST "$KOTS_BASE/api/v1/login" -H 'Content-Type: application/json' \
    --data "$BODY" || echo 000)"
  [ "$CODE" = 200 ] && break
  [ "$(date +%s)" -lt "$LOGIN_DEADLINE" ] || fail "login to $KOTS_BASE returned HTTP $CODE"
  echo "  login returned $CODE, retrying"
  sleep 10
done

# --- select --------------------------------------------------------------
# KOTS_CURSOR is the channel sequence; versionLabel is not unique.
# Never POST /updatecheck here: on embedded cluster it downloads the pending
# release, which advances the store's update cursor to the target, after which
# start-upgrade-service can no longer find it upstream. GET /updates fetches
# live from replicated.app, so there is no cache a check would populate.
# `// []` keeps an absent key from failing the pipe under pipefail.
# Capped at 3m: a cursor absent this long is a real problem, not a settling restart.
TARGET=""
SELECT_DEADLINE=$(( $(date +%s) + 180 ))
while :; do
  TARGET="$( (api "$KOTS_BASE/api/v1/app/$APP/updates" || true) \
    | jq -c --arg c "$KOTS_CURSOR" '(.updates // [])[] | select(.updateCursor == $c)' || true)"
  [ -n "$TARGET" ] && break
  if [ "$(date +%s)" -ge "$SELECT_DEADLINE" ] || ! waiting; then
    # A downloaded cursor is a pending version and no longer an upstream update;
    # no wait recovers it. Deploying it needs the console (skips cluster upgrade).
    SEQ="$( (api "$KOTS_BASE/api/v1/apps" || true) | jq -r --arg c "$KOTS_CURSOR" \
      '.apps[0].downstream.pendingVersions[]? | select(.updateCursor == $c) | .sequence' | head -1)"
    [ -n "$SEQ" ] && fail "cursor $KOTS_CURSOR was already downloaded as pending sequence $SEQ, so it is no longer an upstream update — deploy that version from the admin console"
    fail "cursor $KOTS_CURSOR never became an available update for $APP"
  fi
  echo "  cursor $KOTS_CURSOR not in the update list yet, rechecking"
  sleep 15
done
[ "$(jq -r .isDeployable <<<"$TARGET")" = true ] \
  || fail "cursor $KOTS_CURSOR not deployable: $(jq -r '.nonDeployableCause // "unknown"' <<<"$TARGET")"

FROM="$(api "$KOTS_BASE/api/v1/apps" | jq -r '.apps[0].downstream.currentVersion | "\(.versionLabel) @ \(.updateCursor)"')"
echo "budget: ${TIMEOUT_MINUTES}m"
echo "deploying: $FROM -> $(jq -r .versionLabel <<<"$TARGET") @ $KOTS_CURSOR"

# --- boot the upgrade service -------------------------------------------
R="$(post --data "$(jq -c '{versionLabel, updateCursor, channelId}' <<<"$TARGET")" \
  "$KOTS_BASE/api/v1/app/$APP/start-upgrade-service" || true)"
if ! ok <<<"$R"; then
  # The reason is a cursor or license-channel mismatch; show both sides.
  L="$(TMO=30 api "$KOTS_BASE/api/v1/app/$APP/license" || true)"
  echo "  license: $(jq -c '.license | {channelName, licenseSequence, lastSyncedAt}' <<<"${L:-\{\}}" 2>/dev/null)"
  echo "  release: $(jq -c '{versionLabel, updateCursor, channelId}' <<<"$TARGET")"
  fail "start-upgrade-service rejected: $(why <<<"$R")"
fi

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
  R="$(TMO=60 api -H 'Content-Type: application/json' -X PUT --data-binary @/tmp/cfg.json "$UP/config" || true)"
  ok <<<"$R" || fail "config rejected: $(why <<<"$R") — new release likely added a required item with no default"
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
R="$(TMO=120 post -d '{"isSkipPreflights":false,"continueWithFailedPreflights":false}' "$UP/deploy" || true)"
ok <<<"$R" || fail "deploy rejected: $(why <<<"$R")"

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
