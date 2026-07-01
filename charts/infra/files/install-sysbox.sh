#!/usr/bin/env bash
# install-sysbox.sh — runs on each node, installed by the infra chart's sysbox
# DaemonSet. The DaemonSet execs this on the HOST via `nsenter --target 1`, so
# apt/dpkg/systemctl and the containerd drop-in all act on the node itself.
#
# It installs Sysbox and registers the sysbox containerd runtime through a
# k0s containerd drop-in, so pods with runtimeClassName: sysbox-runc get
# VM-like (user-namespace) isolation. Idempotent: safe to re-run on every pod
# restart.
#
# Order matters: Sysbox is installed BEFORE the containerd drop-in is written.
# That way, when k0s reloads containerd to pick up the drop-in, containerd's
# first registration of the sysbox runtime queries the binary's `features` subcommand
# and reports userns support to kubelet — no second containerd bounce needed.

set -euo pipefail

SYSBOX_VERSION="${SYSBOX_VERSION:-0.7.0}"
SYSBOX_DEB_BASE="${SYSBOX_DEB_BASE:-https://downloads.nestybox.com/sysbox/releases}"
READY_FILE=/run/sysbox-installer.ready

err() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "==> $*"; }

# Re-gate readiness until this run finishes configuring the node.
rm -f "$READY_FILE"

case "$(uname -m)" in
  aarch64|arm64) ARCH=arm64 ;;
  x86_64|amd64)  ARCH=amd64 ;;
  *) err "unsupported architecture: $(uname -m)" ;;
esac

# --- Discover the k0s-managed containerd config + binary --------------------
# Embedded Cluster runs k0s with a custom --data-dir, so don't assume /etc/k0s.
# Find containerd's actual --config and binary from the running process.
conf_from_pid() {
  local pid="$1" args
  args="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
  echo "$args" | grep -oE -- '--config[ =][^ ]+' | head -1 | sed -E 's/--config[ =]//'
}

CONTAINERD_CONF=""
CONTAINERD_BIN=""
for _ in $(seq 1 30); do
  pid="$(pgrep -x containerd 2>/dev/null | head -1 || true)"
  if [ -n "$pid" ]; then
    CONTAINERD_CONF="$(conf_from_pid "$pid")"
    CONTAINERD_BIN="$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)"
    [ -n "$CONTAINERD_CONF" ] && break
  fi
  sleep 2
done
[ -n "$CONTAINERD_CONF" ] || CONTAINERD_CONF=/etc/k0s/containerd.toml
[ -f "$CONTAINERD_CONF" ] || err "containerd config not found at $CONTAINERD_CONF"
grep -q '^# k0s_managed=true' "$CONTAINERD_CONF" \
  || err "$CONTAINERD_CONF is not k0s-managed; refusing to modify it"

DROPIN_DIR="$(dirname "$CONTAINERD_CONF")/containerd.d"
DROPIN="$DROPIN_DIR/sysbox.toml"

log "containerd config: $CONTAINERD_CONF"
log "containerd binary: ${CONTAINERD_BIN:-<unresolved>}"
log "drop-in:           $DROPIN"

# --- 1. Install Sysbox -------------------------------------------------------
installed_ok=false
if command -v sysbox-runc >/dev/null 2>&1; then
  cur="$(sysbox-runc --version 2>/dev/null | awk '/^[[:space:]]*version:/ {print $2}')"
  if [ "$cur" = "$SYSBOX_VERSION" ]; then
    log "sysbox-runc $SYSBOX_VERSION already installed"
    installed_ok=true
  else
    log "sysbox-runc ${cur:-unknown} present; upgrading to $SYSBOX_VERSION"
  fi
fi

if ! $installed_ok; then
  command -v apt-get >/dev/null \
    || err "apt-get not found; Sysbox install requires a Debian/Ubuntu host"
  log "installing apt dependencies"
  export DEBIAN_FRONTEND=noninteractive
  # Stop needrestart from bouncing k0scontroller (and the whole control plane)
  # when apt upgrades a library k0s links against. k0s reloads containerd on its
  # own when the drop-in changes, so we never need needrestart's help here.
  export NEEDRESTART_SUSPEND=1
  apt-get update -qq
  # fuse3 provides fusermount3 (sysbox-fs); iptables/nftables are pulled in by
  # the sysbox-ce deb anyway but listing them keeps minimal images happy.
  apt-get install -y -qq jq rsync iproute2 fuse3 iptables nftables curl ca-certificates

  DEB="sysbox-ce_${SYSBOX_VERSION}-0.linux_${ARCH}.deb"
  URL="${SYSBOX_DEB_BASE}/v${SYSBOX_VERSION}/${DEB}"
  TMP="$(mktemp -d)"
  log "downloading $URL"
  curl -fsSL "$URL" -o "$TMP/$DEB" || { rm -rf -- "$TMP"; err "download failed: $URL"; }
  log "dpkg -i $DEB"
  dpkg -i "$TMP/$DEB" || apt-get install -f -y -qq
  rm -rf -- "$TMP"
fi

# --- 2. Verify Sysbox services ----------------------------------------------
log "verifying sysbox systemd units"
systemctl is-active --quiet sysbox     || systemctl restart sysbox
systemctl is-active --quiet sysbox-mgr || err "sysbox-mgr not active"
systemctl is-active --quiet sysbox-fs  || err "sysbox-fs not active"

# --- 3. containerd drop-in (write only when changed) -------------------------
# k0s's main containerd.toml imports this dir directly (imports =
# ["<dir>/containerd.d/*.toml"]) and watches it, restarting containerd to load
# changes. So there is NO separate merged-CRI file to grep, and writing only on
# change avoids bouncing containerd every time the DaemonSet pod restarts.
# k0s requires version = 3 (the containerd v2 plugin path), despite the public
# docs example showing version = 2.
log "ensuring $DROPIN"
mkdir -p "$DROPIN_DIR"
NEW_DROPIN="$(mktemp)"
cat > "$NEW_DROPIN" <<'EOF'
version = 3

[plugins."io.containerd.cri.v1.runtime".containerd.runtimes.sysbox-runc]
  runtime_type = "io.containerd.runc.v2"
  pod_annotations = ["nestybox.sysbox-runtime"]

[plugins."io.containerd.cri.v1.runtime".containerd.runtimes.sysbox-runc.options]
  BinaryName = "/usr/bin/sysbox-runc"
  SystemdCgroup = false
EOF
if cmp -s "$NEW_DROPIN" "$DROPIN" 2>/dev/null; then
  log "drop-in already current"
  rm -f "$NEW_DROPIN"
else
  mv "$NEW_DROPIN" "$DROPIN"
  log "drop-in written; k0s will reload containerd"
fi

# --- 4. Verify the sysbox runtime is wired into containerd -------------------
# Dump containerd's effective, import-merged config and confirm the runtime is
# present. This proves the drop-in parses and is imported; k0s's directory watch
# then (re)starts containerd so the runtime goes live before any sysbox pod is
# scheduled. (crictl isn't shipped with k0s, so we can't query the live CRI.)
log "verifying sysbox runtime in containerd effective config"
verify_runtime() {
  if [ -n "$CONTAINERD_BIN" ] && [ -x "$CONTAINERD_BIN" ]; then
    "$CONTAINERD_BIN" --config "$CONTAINERD_CONF" config dump 2>/dev/null | grep -qi sysbox-runc
  else
    # No containerd binary resolved; fall back to confirming the drop-in exists.
    grep -q 'runtimes\.sysbox-runc' "$DROPIN"
  fi
}
verified=false
for _ in $(seq 1 30); do
  if verify_runtime; then verified=true; break; fi
  sleep 2
done
[ "$verified" = true ] || err "sysbox runtime not found in containerd config after 60s; check k0s logs"

log "sysbox runtime ready on $(hostname)"
: > "$READY_FILE"

# Hold the pod open; a re-run on restart keeps the node configured.
exec sleep infinity
