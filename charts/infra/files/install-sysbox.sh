#!/usr/bin/env bash
# install-sysbox.sh — runs on each node, installed by the infra chart's sysbox
# DaemonSet. The DaemonSet stages the image-baked Sysbox artifacts onto the host
# under $SYSBOX_ARTIFACTS_DIR, then execs this script on the HOST via
# `nsenter --target 1`, so systemctl and the containerd drop-in all act on the
# node itself.
#
# It installs Sysbox from those staged artifacts (no download, no apt/dpkg) and
# registers the sysbox containerd runtime through a k0s containerd drop-in, so
# pods with runtimeClassName: sysbox-runc get VM-like (user-namespace) isolation.
# Idempotent: safe to re-run on every pod restart.
#
# Because the binaries are portable Go builds and k0s ships its own containerd,
# this works on any systemd host with kernel >= 6.3 — not just Debian/Ubuntu.
# (Kernel >= 6.3 is enforced by the openhands chart's Sysbox preflight.)
#
# Order matters: Sysbox is installed BEFORE the containerd drop-in is written.
# That way, when k0s reloads containerd to pick up the drop-in, containerd's
# first registration of the sysbox runtime queries the binary's `features`
# subcommand and reports userns support to kubelet — no second containerd bounce.

set -euo pipefail

SYSBOX_VERSION="${SYSBOX_VERSION:-0.7.0}"
SYSBOX_ARTIFACTS_DIR="${SYSBOX_ARTIFACTS_DIR:-/run/sysbox-install}"
READY_FILE=/run/sysbox-installer.ready

err() { echo "ERROR: $*" >&2; exit 1; }
log() { echo "==> $*"; }

# Re-gate readiness until this run finishes configuring the node.
rm -f "$READY_FILE"

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

# --- 1. Install Sysbox from the staged, image-baked artifacts ----------------
# The DaemonSet copied the Sysbox binaries and systemd units out of the nestybox
# image into $SYSBOX_ARTIFACTS_DIR (bin/ + systemd/). We install them the same
# way nestybox's own installer does for a containerd node, minus the CRI-O,
# shiftfs and apt steps: shiftfs is unneeded for kernel >= 6.3, and the binaries
# have no package dependencies for our validated Docker-in-Docker path.
#
# We deliberately do NOT touch /etc/subuid or /etc/subgid. nestybox's installer
# only configures those on its CRI-O path; on containerd with `hostUsers: false`
# (which runtime-api sets for sysbox sandboxes) the kubelet owns user-namespace
# ID allocation, so a "containers" subid range is never consulted — and blindly
# appending one would overlap the host's default user range.
SYSBOX_BIN_DIR="$SYSBOX_ARTIFACTS_DIR/bin"
SYSBOX_UNIT_DIR="$SYSBOX_ARTIFACTS_DIR/systemd"
[ -f "$SYSBOX_BIN_DIR/sysbox-runc" ] \
  || err "staged sysbox binaries not found in $SYSBOX_BIN_DIR (did the DaemonSet stage them?)"

need_install=true
if command -v sysbox-runc >/dev/null 2>&1; then
  cur="$(sysbox-runc --version 2>/dev/null | awk '/^[[:space:]]*version:/ {print $2}')"
  if [ "$cur" = "$SYSBOX_VERSION" ] \
     && systemctl is-active --quiet sysbox-mgr \
     && systemctl is-active --quiet sysbox-fs; then
    log "sysbox-runc $SYSBOX_VERSION already installed and active; skipping reinstall"
    need_install=false
  else
    log "sysbox-runc ${cur:-unknown} present; (re)installing $SYSBOX_VERSION"
  fi
fi

if $need_install; then
  # Stop Sysbox (if running) so the binaries can be replaced without a
  # "text file busy". mgr/fs are PartOf sysbox.service, so this stops all three;
  # tolerate absence on first install.
  systemctl stop sysbox 2>/dev/null || true

  log "installing sysbox binaries into /usr/bin"
  install -m 0755 "$SYSBOX_BIN_DIR/sysbox-mgr"  /usr/bin/sysbox-mgr
  install -m 0755 "$SYSBOX_BIN_DIR/sysbox-fs"   /usr/bin/sysbox-fs
  install -m 0755 "$SYSBOX_BIN_DIR/sysbox-runc" /usr/bin/sysbox-runc

  # Kernel sysctls + modules Sysbox needs. `sysctl -e` ignores keys absent on
  # this kernel (e.g. kernel.unprivileged_userns_clone on non-Debian kernels),
  # so a single missing key can't abort the install.
  log "applying sysbox sysctls and kernel modules"
  install -D -m 0644 "$SYSBOX_UNIT_DIR/99-sysbox-sysctl.conf" /etc/sysctl.d/99-sysbox-sysctl.conf
  install -D -m 0644 "$SYSBOX_UNIT_DIR/50-sysbox-mod.conf"    /etc/modules-load.d/50-sysbox-mod.conf
  sysctl -e -p /etc/sysctl.d/99-sysbox-sysctl.conf || true
  modprobe configfs 2>/dev/null || true

  log "installing sysbox systemd units"
  install -m 0644 "$SYSBOX_UNIT_DIR/sysbox.service"     /etc/systemd/system/sysbox.service
  install -m 0644 "$SYSBOX_UNIT_DIR/sysbox-mgr.service" /etc/systemd/system/sysbox-mgr.service
  install -m 0644 "$SYSBOX_UNIT_DIR/sysbox-fs.service"  /etc/systemd/system/sysbox-fs.service
  systemctl daemon-reload
  systemctl enable sysbox.service sysbox-mgr.service sysbox-fs.service

  log "starting sysbox"
  systemctl restart sysbox
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
