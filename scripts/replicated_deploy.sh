#!/usr/bin/env bash
# Deploy one KOTS release to a Replicated Embedded Cluster instance.
#
#   KOTS_BASE=https://admin.unstable.staging.all-hands-testing.dev:30000 \
#   KOTS_PASSWORD=... KOTS_CURSOR=418 scripts/replicated_deploy.sh
#
# Re-runnable for one cursor: already deployed verifies and exits 0, already
# downloaded resumes, otherwise it takes the upgrade-service path.
set -euo pipefail

APP="${APP:-openhands}"
: "${KOTS_BASE:?}" "${KOTS_PASSWORD:?}" "${KOTS_CURSOR:?}"
# Whole-run budget in minutes; every wait loop below shares this one deadline.
TIMEOUT_MINUTES="${TIMEOUT_MINUTES:-25}"
DEADLINE=$(( $(date +%s) + TIMEOUT_MINUTES * 60 ))
waiting() { [ "$(date +%s)" -lt "$DEADLINE" ]; }

UP="$KOTS_BASE/api/v1/upgrade-service/app/$APP"
DS="$KOTS_BASE/api/v1/app/$APP"
JAR="$(mktemp)"; ERR="$(mktemp)"; trap 'rm -f "$JAR" "$ERR"' EXIT

fail() { echo "::error::$*"; exit 1; }
# %{stderr} keeps the status code out of the body. $ERR holds the last call's
# code plus any curl error, which is all why() has to go on when a body is empty.
api()  { curl -sS -k --max-time "${TMO:-30}" -b "$JAR" -c "$JAR" -w '%{stderr}HTTP %{http_code}' "$@" 2>"$ERR"; }
post() { api -H 'Content-Type: application/json' -X POST "$@"; }
ok()   { jq -e '.success == true' >/dev/null 2>&1; }
# KOTS puts its rejection reason in .error; fall back to the raw body, then to
# the transport detail. A bare 4xx has no body, so never print just "rejected".
why()  { local b; b="$(cat)"; jq -re '.error // empty' <<<"$b" 2>/dev/null \
           || printf '%s' "${b:-$(tr -s '\n' ' ' <"$ERR")}"; }

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

# --- shared stages -------------------------------------------------------

# $1 is whichever API base serves preflight/run and preflight/result.
preflight_gate() {
  local base="$1" R="" BAD
  TMO=60 post "$base/preflight/run" >/dev/null
  # Waits for the record, not for .result: this fleet leaves that an empty
  # string even on sequences that deployed cleanly.
  while waiting; do
    R="$(api "$base/preflight/result" || true)"
    jq -e '.preflightResult' <<<"${R:-\{\}}" >/dev/null 2>&1 && break
    sleep 5
  done
  [ -n "$R" ] || fail "no preflight record from $base"
  [ "$(jq -r '.preflightResult.hasFailingStrictPreflights // false' <<<"$R")" = true ] \
    && fail "preflights failed: $(jq -r .preflightResult.result <<<"$R" | jq -c '[.results[]? | select(.isPass != true)]')"
  BAD="$(jq -r '.preflightResult.result // ""' <<<"$R" | jq -c '[.results[]? | select(.isPass != true)]' 2>/dev/null || true)"
  echo "preflights: ${BAD:-none recorded}"
}

# The task stays empty for the whole deploy; downstream currentVersion is the
# only progress signal. An unreachable console means kotsadm is restarting.
wait_deployed() {
  local DONE="" CUR
  while waiting; do
    [ "$(TMO=10 api "$DS/task/upgrade-service" 2>/dev/null | jq -r '.status // ""')" = upgrade-failed ] \
      && fail "upgrade-failed"
    CUR="$(TMO=10 api "$KOTS_BASE/api/v1/apps" 2>/dev/null | jq -c '.apps[0].downstream.currentVersion | {updateCursor, sequence, status}' || true)"
    echo "  ${CUR:-<console unreachable, waiting>}"
    case "$(jq -r '"\(.updateCursor) \(.status)"' <<<"${CUR:-\{\}}" 2>/dev/null)" in
      "$KOTS_CURSOR deployed") DONE=1; break ;;
      "$KOTS_CURSOR failed")   fail "instance reported the deploy failed — see the admin console version history" ;;
    esac
    sleep 10
  done
  [ "$DONE" ] || fail "timed out waiting for cursor $KOTS_CURSOR to deploy"
}

# Response key is "appstatus", all lowercase.
verify_ready() {
  local S=""
  while waiting; do
    S="$(TMO=15 api "$DS/status" 2>/dev/null || true)"
    [ "$(jq -r '.appstatus.state // ""' <<<"${S:-\{\}}")" = ready ] && break
    sleep 10
  done
  [ -n "$S" ] || fail "timed out waiting for the app to report ready"
  jq -c '.appstatus | {state, sequence, unhealthy: [.resourceStates[] | select(.state != "ready")]}' <<<"$S"
  [ "$(jq -r .appstatus.state <<<"$S")" = ready ] \
    || fail "deployed but unhealthy: $(jq -c '[.appstatus.resourceStates[] | select(.state != "ready")]' <<<"$S")"
}

# --- where is this cursor? ----------------------------------------------
# A cursor is an upstream update until something downloads it, then a pending
# version, then the current one. Only an upstream update can boot the upgrade
# service, so the other two need their own paths or a re-run of a deploy that
# already got somewhere reads as "cursor never became an available update".
echo "budget: ${TIMEOUT_MINUTES}m"
# jq yields null with exit 0 on an error body, so an unguarded read here would
# fail open and spin out the select loop below instead of naming the reason.
APPS="$(api "$KOTS_BASE/api/v1/apps" || true)"
D="$(jq -c '.apps[0].downstream // empty' <<<"${APPS:-}" 2>/dev/null || true)"
[ -n "$D" ] || fail "could not read app state for $APP: $(why <<<"$APPS")"
FROM="$(jq -r '.currentVersion | "\(.versionLabel) @ \(.updateCursor)"' <<<"$D")"

if [ "$(jq -r '.currentVersion.updateCursor // ""' <<<"$D")" = "$KOTS_CURSOR" ] \
   && [ "$(jq -r '.currentVersion.status // ""' <<<"$D")" = deployed ]; then
  echo "cursor $KOTS_CURSOR is already deployed as sequence $(jq -r .currentVersion.sequence <<<"$D"); verifying only"
  verify_ready
  echo "cursor $KOTS_CURSOR already deployed, app ready"
  exit 0
fi

PENDING_SEQ="$(jq -r --arg c "$KOTS_CURSOR" \
  '[.pendingVersions[]? | select(.updateCursor == $c) | .sequence] | first // ""' <<<"$D")"

# A superseded cursor is never re-offered upstream, so waiting out the select
# loop below cannot recover it. Asking for one is a rollback, not a re-run.
jq -e --arg c "$KOTS_CURSOR" 'any(.pastVersions[]?; .updateCursor == $c)' <<<"$D" >/dev/null 2>&1 \
  && fail "cursor $KOTS_CURSOR was deployed before and the instance has moved past it — rolling back needs the admin console"

if [ -n "$PENDING_SEQ" ]; then
  # --- resume an already-downloaded cursor -------------------------------
  # It is no longer an upstream update, so the upgrade service cannot claim it;
  # KOTS deploys a downloaded version from its downstream sequence instead.
  echo "deploying: $FROM -> sequence $PENDING_SEQ @ $KOTS_CURSOR (already downloaded)"
  preflight_gate "$DS/sequence/$PENDING_SEQ"
  R="$(TMO=120 post -d '{}' "$DS/sequence/$PENDING_SEQ/deploy" || true)"
  ok <<<"$R" || fail "deploy of sequence $PENDING_SEQ rejected: $(why <<<"$R")"
else
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
      fail "cursor $KOTS_CURSOR never became an available update for $APP"
    fi
    echo "  cursor $KOTS_CURSOR not in the update list yet, rechecking"
    sleep 15
  done
  [ "$(jq -r .isDeployable <<<"$TARGET")" = true ] \
    || fail "cursor $KOTS_CURSOR not deployable: $(jq -r '.nonDeployableCause // "unknown"' <<<"$TARGET")"

  echo "deploying: $FROM -> $(jq -r .versionLabel <<<"$TARGET") @ $KOTS_CURSOR"

  # --- boot the upgrade service -------------------------------------------
  R="$(post --data "$(jq -c '{versionLabel, updateCursor, channelId}' <<<"$TARGET")" \
    "$KOTS_BASE/api/v1/app/$APP/start-upgrade-service" || true)"
  if ! ok <<<"$R"; then
    # Read the reason before the license call below overwrites $ERR.
    REASON="$(why <<<"$R")"
    # The reason is a cursor or license-channel mismatch; show both sides.
    L="$(TMO=30 api "$KOTS_BASE/api/v1/app/$APP/license" || true)"
    echo "  license: $(jq -c '.license | {channelName, licenseSequence, lastSyncedAt}' <<<"${L:-\{\}}" 2>/dev/null)"
    echo "  release: $(jq -c '{versionLabel, updateCursor, channelId}' <<<"$TARGET")"
    fail "start-upgrade-service rejected: $REASON"
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
  [ "$(jq -r .hasPreflight <<<"$META")" = true ] && preflight_gate "$UP"

  # --- deploy --------------------------------------------------------------
  R="$(TMO=120 post -d '{"isSkipPreflights":false,"continueWithFailedPreflights":false}' "$UP/deploy" || true)"
  ok <<<"$R" || fail "deploy rejected: $(why <<<"$R")"
fi

wait_deployed
verify_ready
echo "deployed cursor $KOTS_CURSOR, app ready"
