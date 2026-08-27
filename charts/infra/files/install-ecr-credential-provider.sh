#!/usr/bin/env bash
# install-ecr-credential-provider.sh — runs on each node, installed by the infra
# chart's ECR credential provider DaemonSet. The DaemonSet execs this script on
# the HOST via `nsenter --target 1`, so the download, the systemd unit edit and
# systemctl all act on the node itself.
#
# It installs the kubelet ECR credential provider plugin and points the kubelet
# at it, so image pulls from Amazon ECR authenticate with the node's own EC2
# instance profile. The plugin resolves credentials through the AWS SDK chain as
# the kubelet's user (root), which on an Embedded Cluster node means IMDS: there
# is no static credential on the node and nothing to rotate when ECR's 12-hour
# authorization token expires.
#
# Embedded Cluster bakes kubelet's arguments into the k0s systemd unit's
# ExecStart line and exposes no supported override, so this rewrites the
# --kubelet-extra-args value in that unit. Restarting k0s is the only way
# kubelet picks the new flags up: k0s builds kubelet's argument list once at
# startup and its supervisor re-execs kubelet from that stored list, so killing
# kubelet alone changes nothing.
#
# Idempotent: the unit is rewritten (and k0s restarted) only when the plugin
# files or the kubelet flags are not already what this script wants, so a pod
# restart on a configured node does nothing. The restart bounces the node's k0s
# components for a few seconds; running containers survive it because
# containerd's shims outlive containerd.

set -euo pipefail

# Release to install. The binaries are the upstream cloud-provider-aws plugin
# rebuilt and published by dntosas/ecr-credential-provider, because
# kubernetes/cloud-provider-aws itself ships no release binary and no tagged
# image. The download is checksum-pinned, so the release host only has to be
# reachable, not trusted.
ECR_CP_VERSION="${ECR_CP_VERSION:?ECR_CP_VERSION is required}"
ECR_CP_RELEASE_BASE_URL="${ECR_CP_RELEASE_BASE_URL:?ECR_CP_RELEASE_BASE_URL is required}"
ECR_CP_SHA256_AMD64="${ECR_CP_SHA256_AMD64:?ECR_CP_SHA256_AMD64 is required}"
ECR_CP_SHA256_ARM64="${ECR_CP_SHA256_ARM64:?ECR_CP_SHA256_ARM64 is required}"
# Host directory that holds both the plugin binary and the kubelet's
# CredentialProviderConfig. kubelet execs everything in the bin dir, so it stays
# ours alone rather than a shared location like /usr/local/bin.
ECR_CP_INSTALL_DIR="${ECR_CP_INSTALL_DIR:-/opt/openhands/image-credential-provider}"
# Newline-separated kubelet matchImages globs.
ECR_CP_MATCH_IMAGES="${ECR_CP_MATCH_IMAGES:?ECR_CP_MATCH_IMAGES is required}"
# Newline-separated NAME=VALUE pairs added to the plugin's environment in the
# CredentialProviderConfig. kubelet appends these after the host environment and
# exec keeps the last duplicate, so they override what kubelet itself inherited.
ECR_CP_PROVIDER_ENV="${ECR_CP_PROVIDER_ENV:-}"
# Optional image reference used to prove, on this node, that the plugin can
# actually mint an ECR token with the instance profile. Empty disables the check.
ECR_CP_SELF_TEST_IMAGE="${ECR_CP_SELF_TEST_IMAGE:-}"
# Shortest interval between two k0s restarts driven by this script.
ECR_CP_RESTART_COOLDOWN="${ECR_CP_RESTART_COOLDOWN:-900}"

BIN_DIR="$ECR_CP_INSTALL_DIR/bin"
BIN_PATH="$BIN_DIR/ecr-credential-provider"
CONFIG_PATH="$ECR_CP_INSTALL_DIR/config.yaml"
RESTART_STAMP="$ECR_CP_INSTALL_DIR/.last-k0s-restart"
READY_FILE=/run/ecr-credential-provider-installer.ready

err() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "==> $*"; }

# Re-gate readiness until this run finishes configuring the node.
rm -f "$READY_FILE"

# Proxy settings reach us as provider env so that kubelet's exec of the plugin
# uses them too; export them here as well so this script's own download and
# self-test take the same path the plugin will.
if [ -n "$ECR_CP_PROVIDER_ENV" ]; then
  while IFS= read -r pair; do
    [ -n "$pair" ] || continue
    export "${pair?}"
  done <<EOF
$ECR_CP_PROVIDER_ENV
EOF
fi

# --- Resolve the release artifact for this node's architecture ---------------
case "$(uname -m)" in
  x86_64 | amd64) ARCH=amd64; WANT_SHA256="$ECR_CP_SHA256_AMD64" ;;
  aarch64 | arm64) ARCH=arm64; WANT_SHA256="$ECR_CP_SHA256_ARM64" ;;
  *) err "unsupported architecture $(uname -m); the ECR credential provider ships linux/amd64 and linux/arm64 only" ;;
esac
URL="$ECR_CP_RELEASE_BASE_URL/$ECR_CP_VERSION/ecr-credential-provider-linux-$ARCH"
NODE_NAME="$(uname -n)"

log "node $NODE_NAME ($ARCH), plugin $ECR_CP_VERSION"

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$1" | awk '{print $NF}'
  else
    err "no sha256sum or openssl on the host; cannot verify the download"
  fi
}

# Content comparison via sha256_of rather than cmp/diff: diffutils is not part
# of a minimal RHEL or Amazon Linux install, and the hashing tools are already
# a hard requirement for verifying the download.
same_content() {
  [ -f "$2" ] && [ "$(sha256_of "$1")" = "$(sha256_of "$2")" ]
}

fetch() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --connect-timeout 15 --retry 5 --retry-delay 3 -o "$2" "$1"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -T 15 -t 5 -O "$2" "$1"
  else
    err "no curl or wget on the host; cannot download $1"
  fi
}

# --- 1. Plugin binary --------------------------------------------------------
if [ -x "$BIN_PATH" ] && [ "$(sha256_of "$BIN_PATH")" = "$WANT_SHA256" ]; then
  log "plugin already installed at $BIN_PATH"
else
  log "downloading $URL"
  TMP_BIN="$(mktemp)"
  fetch "$URL" "$TMP_BIN" || err "failed to download the ECR credential provider from $URL"
  GOT_SHA256="$(sha256_of "$TMP_BIN")"
  if [ "$GOT_SHA256" != "$WANT_SHA256" ]; then
    rm -f "$TMP_BIN"
    err "checksum mismatch for $URL (want $WANT_SHA256, got $GOT_SHA256)"
  fi
  install -D -m 0755 "$TMP_BIN" "$BIN_PATH"
  rm -f "$TMP_BIN"
  log "installed plugin to $BIN_PATH"
fi

# --- 2. CredentialProviderConfig --------------------------------------------
# defaultCacheDuration is 0s on purpose: it applies only when the plugin returns
# no duration of its own, and an ECR token that outlives its own expiry in
# kubelet's cache is worse than an extra exec per pull. The plugin does return a
# duration (half the token's remaining life), so this is a floor, not the norm.
NEW_CONFIG="$(mktemp)"
{
  echo "apiVersion: kubelet.config.k8s.io/v1"
  echo "kind: CredentialProviderConfig"
  echo "providers:"
  echo "  - name: ecr-credential-provider"
  echo "    apiVersion: credentialprovider.kubelet.k8s.io/v1"
  echo "    defaultCacheDuration: 0s"
  echo "    matchImages:"
  while IFS= read -r glob; do
    glob="$(echo "$glob" | tr -d '[:space:]')"
    [ -n "$glob" ] && echo "      - \"$glob\""
  done <<EOF
$ECR_CP_MATCH_IMAGES
EOF
  if [ -n "$ECR_CP_PROVIDER_ENV" ]; then
    echo "    env:"
    while IFS= read -r pair; do
      [ -n "$pair" ] || continue
      echo "      - name: \"${pair%%=*}\""
      echo "        value: \"${pair#*=}\""
    done <<EOF
$ECR_CP_PROVIDER_ENV
EOF
  fi
} > "$NEW_CONFIG"

config_changed=false
if same_content "$NEW_CONFIG" "$CONFIG_PATH"; then
  log "credential provider config already current at $CONFIG_PATH"
  rm -f "$NEW_CONFIG"
else
  install -D -m 0644 "$NEW_CONFIG" "$CONFIG_PATH"
  rm -f "$NEW_CONFIG"
  config_changed=true
  log "wrote credential provider config to $CONFIG_PATH"
fi

# --- 3. Point the kubelet at the plugin via the k0s unit ---------------------
# Controller nodes (including the single-node install, which k0s runs with
# --enable-worker) use k0scontroller.service; nodes joined with the sandbox role
# are plain workers and use k0sworker.service.
# A running unit is the authoritative answer; a node reset from one role to the
# other can leave the unit file for the role it no longer runs.
UNIT_NAME=""
for candidate in k0scontroller k0sworker; do
  if systemctl is-active --quiet "$candidate.service" 2>/dev/null; then
    UNIT_NAME="$candidate"
    break
  fi
done
if [ -z "$UNIT_NAME" ]; then
  for candidate in k0scontroller k0sworker; do
    if [ -f "/etc/systemd/system/$candidate.service" ]; then
      UNIT_NAME="$candidate"
      break
    fi
  done
fi
[ -n "$UNIT_NAME" ] || err "no k0scontroller.service or k0sworker.service on this host; is this an Embedded Cluster node?"
UNIT_PATH="/etc/systemd/system/$UNIT_NAME.service"
grep -qE '^ExecStart=.*/k0s ' "$UNIT_PATH" \
  || err "$UNIT_PATH does not look like a k0s unit; refusing to modify it"

log "k0s unit: $UNIT_PATH"

# k0s takes kubelet's flags as one --kubelet-extra-args=<flags> argument, and
# kardianos/service (which writes the unit) escapes the spaces inside that value
# as \x20. So the edit happens inside a single ExecStart field: split it on
# \x20, drop any credential provider flags a previous run left, and append the
# current ones. Passing the flags through ENVIRON rather than -v keeps awk from
# interpreting \x20 as an escape and collapsing it to a real space.
render_unit() {
  CFG_FLAG="--image-credential-provider-config=$CONFIG_PATH" \
  BIN_FLAG="--image-credential-provider-bin-dir=$BIN_DIR" \
  awk '
    function rebuild(tok,   n, parts, j, out) {
      n = split(tok, parts, /\\x20/)
      out = ""
      for (j = 1; j <= n; j++) {
        if (parts[j] ~ /^--image-credential-provider-(config|bin-dir)=/) continue
        out = (out == "") ? parts[j] : out "\\x20" parts[j]
      }
      return out "\\x20" ENVIRON["CFG_FLAG"] "\\x20" ENVIRON["BIN_FLAG"]
    }
    /^ExecStart=/ && !patched {
      found = 0
      for (i = 1; i <= NF; i++) {
        if ($i ~ /^--kubelet-extra-args=/) { $i = rebuild($i); found = 1 }
      }
      if (!found) {
        $0 = $0 " --kubelet-extra-args=" ENVIRON["CFG_FLAG"] "\\x20" ENVIRON["BIN_FLAG"]
      }
      patched = 1
    }
    { print }
  ' "$1"
}

NEW_UNIT="$(mktemp)"
render_unit "$UNIT_PATH" > "$NEW_UNIT"
grep -qF -- "--image-credential-provider-bin-dir=$BIN_DIR" "$NEW_UNIT" \
  || { rm -f "$NEW_UNIT"; err "failed to add the credential provider flags to $UNIT_PATH"; }

unit_changed=false
if same_content "$NEW_UNIT" "$UNIT_PATH"; then
  log "kubelet flags already present in $UNIT_PATH"
  rm -f "$NEW_UNIT"
else
  # Keep the unit as Embedded Cluster first wrote it, so the node can be put
  # back by hand without reinstalling. Written once; later runs edit the live
  # unit, which already carries our flags, and must not overwrite this copy.
  [ -f "$ECR_CP_INSTALL_DIR/$UNIT_NAME.service.orig" ] \
    || install -D -m 0644 "$UNIT_PATH" "$ECR_CP_INSTALL_DIR/$UNIT_NAME.service.orig"
  install -m 0644 "$NEW_UNIT" "$UNIT_PATH"
  rm -f "$NEW_UNIT"
  unit_changed=true
  log "added the credential provider flags to $UNIT_PATH"
fi

# Walks /proc rather than using pgrep, which is not part of a minimal RHEL or
# Amazon Linux install. Matching argv[0] first keeps this from being satisfied by
# some other process that merely mentions the flag, such as this script's own
# grep should it land on a recycled PID.
kubelet_configured() {
  local proc argv0
  for proc in /proc/[0-9]*; do
    [ -r "$proc/cmdline" ] || continue
    argv0="$(tr '\0' '\n' < "$proc/cmdline" 2>/dev/null | head -1)"
    case "$argv0" in */kubelet | kubelet) ;; *) continue ;; esac
    if tr '\0' '\n' < "$proc/cmdline" 2>/dev/null \
      | grep -qxF -- "--image-credential-provider-config=$CONFIG_PATH"; then
      return 0
    fi
  done
  return 1
}

wait_for_kubelet() {
  local deadline=$((SECONDS + $1))
  until kubelet_configured; do
    [ "$SECONDS" -lt "$deadline" ] || return 1
    sleep 5
  done
}

need_restart=false
if [ "$unit_changed" = true ] || [ "$config_changed" = true ]; then
  # A real change to what the kubelet should be running with. Restart on its
  # own merit, uncooled: the desired state is derived from the chart values, so
  # once it is written it stops changing and this cannot repeat.
  need_restart=true
elif ! wait_for_kubelet 60; then
  # Files were already right but the running kubelet is not using them, so an
  # earlier run patched the unit and never got as far as restarting k0s. The
  # grace period keeps a kubelet that is merely slow to come up from being read
  # as unconfigured.
  log "kubelet is not using the credential provider yet"
  # This branch is the repeat: nothing changed since last time and the restart
  # did not take. Rate-limit it so a node that never comes back configured
  # cannot turn this pod's restart loop into a k0s restart loop. Failing here
  # leaves the pod un-ready and the node on the kubelet it already had.
  now="$(date +%s)"
  last=0
  [ -f "$RESTART_STAMP" ] && last="$(cat "$RESTART_STAMP" 2>/dev/null || echo 0)"
  [ $((now - last)) -ge "$ECR_CP_RESTART_COOLDOWN" ] \
    || err "$UNIT_NAME was already restarted $((now - last))s ago and the kubelet still is not using the credential provider; not restarting again within ${ECR_CP_RESTART_COOLDOWN}s. Check journalctl -u $UNIT_NAME"
  need_restart=true
fi

if [ "$need_restart" = true ]; then
  date +%s > "$RESTART_STAMP"
  systemctl daemon-reload
  # Detach the restart from this shell. Stopping k0s takes kubelet down with it,
  # and a kubelet that is mid-restart can leave this pod's exec plumbing in a
  # bad state; handing the job to systemd means the restart finishes either way.
  systemctl reset-failed openhands-ecr-cp-k0s-restart.service 2>/dev/null || true
  log "restarting $UNIT_NAME to load the new kubelet flags (one time; the node's k0s components bounce, running containers do not)"
  systemd-run --no-block --collect \
    --unit=openhands-ecr-cp-k0s-restart \
    --description="Restart k0s to load the OpenHands ECR credential provider" \
    /bin/systemctl restart "$UNIT_NAME.service"

  log "waiting for the kubelet to come back with the credential provider configured"
  wait_for_kubelet 300 \
    || err "kubelet did not come back with the credential provider flags within 300s; check journalctl -u $UNIT_NAME"
fi

log "kubelet is running with the ECR credential provider"

# --- 4. Prove the node's IAM identity can actually mint an ECR token ---------
# Same binary, same credential chain, same user kubelet will use, so a pass here
# means the next ECR pull authenticates. Non-fatal: the node is configured
# either way and the fix is an IAM change, not a redeploy.
if [ -n "$ECR_CP_SELF_TEST_IMAGE" ]; then
  log "self-test: requesting an ECR token for $ECR_CP_SELF_TEST_IMAGE"
  SELF_TEST_ERR="$(mktemp)"
  REQUEST="{\"kind\":\"CredentialProviderRequest\",\"apiVersion\":\"credentialprovider.kubelet.k8s.io/v1\",\"image\":\"$ECR_CP_SELF_TEST_IMAGE\"}"
  # The response carries a live registry password, so it is matched and dropped
  # rather than logged.
  if printf '%s' "$REQUEST" | "$BIN_PATH" 2>"$SELF_TEST_ERR" | grep -q '"auth"'; then
    log "self-test passed: this node's IAM identity can pull from ECR"
  else
    echo "ERROR: self-test failed: the plugin could not get an ECR token on this node." >&2
    echo "ERROR: the node's instance profile needs ecr:GetAuthorizationToken plus" >&2
    echo "ERROR: ecr:BatchGetImage and ecr:GetDownloadUrlForLayer on the repository," >&2
    echo "ERROR: and IMDS must be reachable from the host. Plugin output follows." >&2
    sed 's/^/ERROR: /' "$SELF_TEST_ERR" >&2
  fi
  rm -f "$SELF_TEST_ERR"
fi

log "ECR credential provider ready on $NODE_NAME"
: > "$READY_FILE"

# Hold the pod open; a re-run on restart keeps the node configured.
exec sleep infinity
