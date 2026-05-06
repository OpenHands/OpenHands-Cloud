#!/usr/bin/env bash
set -euo pipefail

# Containerd v2.x + sysbox-runc on a k0s embedded-cluster host.
#
# Tested on:
#   OS:     Ubuntu 24.04.4 LTS
#   Kernel: 6.17.0-1012-aws
#
# Phase 1 installs the runtime + sysbox; Phase 2 only fires once EC is
# fully installed and flips kubelet over.
#
# Architecture:
#  - containerd-sysbox runs side-by-side with k0s's bundled containerd
#    rather than replacing it. The bundled one keeps running
#    (k0scontroller manages it as a child process); we just point kubelet
#    at ours. Every path is non-default to avoid collisions:
#    /run/containerd-sysbox state, /var/lib/containerd-sysbox root,
#    /run/containerd-sysbox/containerd.sock socket, and
#    /etc/systemd/system/containerd-sysbox.service unit.
#  - containerd 2.x ships no runc and no CNI plugins; we install both.
#    sysbox-runc is registered as a non-default named runtime; default
#    stays plain runc so k8s system pods aren't surprised.

CONTAINERD_VERSION="2.3.0"
RUNC_VERSION="1.4.2"
CNI_PLUGINS_VERSION="1.9.1"
SYSBOX_VERSION="0.7.0"

CONTAINERD_SOCKET_PATH="/run/containerd-sysbox/containerd.sock"
CONTAINERD_ENDPOINT="unix://${CONTAINERD_SOCKET_PATH}"
CONTAINERD_STATE_DIR="/run/containerd-sysbox"
CONTAINERD_ROOT_DIR="/var/lib/containerd-sysbox"
CONTAINERD_CONFIG="/etc/containerd/config.toml"
CONTAINERD_UNIT="/etc/systemd/system/containerd-sysbox.service"
DROPIN_PATH="/etc/systemd/system/k0scontroller.service.d/99-containerd-endpoint.conf"
K0S_CONTAINERD_BIN_PREFIX="/var/lib/embedded-cluster/k0s/bin/"

log() { printf '\n=== %s ===\n' "$*"; }
need_root() { [[ $EUID -eq 0 ]] || { echo "must run as root"; exit 1; }; }
need_root

# ---------- Phase 1: host prep ----------------------------------------------

install_containerd() {
  if [[ -x /usr/local/bin/containerd ]] \
     && /usr/local/bin/containerd --version 2>/dev/null \
        | grep -q "v${CONTAINERD_VERSION}"; then
    log "containerd v${CONTAINERD_VERSION} already installed"
    return
  fi
  log "Installing containerd v${CONTAINERD_VERSION}"
  # Official static tarball — Docker's apt repo lags on v2 (still ships 1.7.x),
  # so we go straight to upstream.
  local tar=/tmp/containerd.tar.gz
  curl -fsSL -o "$tar" \
    "https://github.com/containerd/containerd/releases/download/v${CONTAINERD_VERSION}/containerd-${CONTAINERD_VERSION}-linux-amd64.tar.gz"
  tar -C /usr/local -xzf "$tar"
  rm -f "$tar"
}

install_runc() {
  if [[ -x /usr/local/sbin/runc ]] \
     && /usr/local/sbin/runc --version 2>/dev/null \
        | grep -q "${RUNC_VERSION}"; then
    log "runc ${RUNC_VERSION} already installed"
    return
  fi
  log "Installing runc ${RUNC_VERSION}"
  # containerd 2.x doesn't ship runc; install separately. This is the DEFAULT
  # runtime — sysbox-runc is registered as a named non-default runtime.
  curl -fsSL -o /usr/local/sbin/runc \
    "https://github.com/opencontainers/runc/releases/download/v${RUNC_VERSION}/runc.amd64"
  chmod 755 /usr/local/sbin/runc
}

install_cni_plugins() {
  if [[ -x /opt/cni/bin/bridge ]]; then
    log "CNI plugins already present at /opt/cni/bin"
    return
  fi
  log "Installing CNI plugins v${CNI_PLUGINS_VERSION} to /opt/cni/bin"
  # k0s installs its CNI binaries under /var/lib/embedded-cluster/k0s/bin,
  # not /opt/cni/bin. We give containerd its own copy here rather than
  # teaching it about k0s's path — keeps this script self-contained and
  # avoids breaking if k0s reshuffles its layout.
  mkdir -p /opt/cni/bin
  local tar=/tmp/cni-plugins.tar.gz
  curl -fsSL -o "$tar" \
    "https://github.com/containernetworking/plugins/releases/download/v${CNI_PLUGINS_VERSION}/cni-plugins-linux-amd64-v${CNI_PLUGINS_VERSION}.tgz"
  tar -C /opt/cni/bin -xzf "$tar"
  rm -f "$tar"
}

install_sysbox() {
  if [[ -x /usr/bin/sysbox-runc ]]; then
    log "sysbox already installed: $(sysbox-runc --version | head -1)"
    return
  fi
  log "Installing sysbox-ce ${SYSBOX_VERSION}"
  # 0.7.0 (released 2025-03-02) is the first release with documented
  # containerd v2.0.5+ support — see CHANGELOG.md upstream.
  local deb=/tmp/sysbox-ce.deb
  curl -fsSL -o "$deb" \
    "https://downloads.nestybox.com/sysbox/releases/v${SYSBOX_VERSION}/sysbox-ce_${SYSBOX_VERSION}-0.linux_amd64.deb"
  # NEEDRESTART_MODE=l → just list services that should restart, don't
  # restart them. Without this, Ubuntu 24.04's needrestart hook restarts
  # k0scontroller mid-script and interrupts the EC cluster mid-run.
  # We restart k0s ourselves later in restart_k0s_if_needed.
  DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get install -y "$deb"
  rm -f "$deb"
}

write_containerd_config() {
  if [[ -f "$CONTAINERD_CONFIG" ]] \
     && grep -q 'sysbox-runc' "$CONTAINERD_CONFIG" 2>/dev/null; then
    log "containerd config already has sysbox-runc registered"
    return
  fi
  log "Writing $CONTAINERD_CONFIG (v3 schema, sysbox-runc registered)"
  mkdir -p "$(dirname "$CONTAINERD_CONFIG")"
  # version = 3 is the containerd 2.x schema. The plugin path
  # io.containerd.cri.v1.runtime is the v3 location for what used to be
  # io.containerd.grpc.v1.cri in the v2 schema.
  #
  # SystemdCgroup=true matches k0s's default cgroup driver. If you flip k0s
  # to cgroupfs you'll need to flip this too.
  cat > "$CONTAINERD_CONFIG" <<EOF
version = 3

# Non-default state/root/socket paths so we don't collide with k0s's bundled
# containerd, which uses /run/containerd, /var/lib/containerd, /run/k0s/containerd.sock.
root  = "${CONTAINERD_ROOT_DIR}"
state = "${CONTAINERD_STATE_DIR}"

[grpc]
  address = "${CONTAINERD_SOCKET_PATH}"

[plugins."io.containerd.cri.v1.runtime".cni]
  bin_dir  = "/opt/cni/bin"
  conf_dir = "/etc/cni/net.d"

[plugins."io.containerd.cri.v1.runtime".containerd]
  default_runtime_name = "runc"

  [plugins."io.containerd.cri.v1.runtime".containerd.runtimes.runc]
    runtime_type = "io.containerd.runc.v2"
    [plugins."io.containerd.cri.v1.runtime".containerd.runtimes.runc.options]
      BinaryName    = "/usr/local/sbin/runc"
      SystemdCgroup = true

  # sysbox-runc must use the v3 schema path. The old v2-schema path
  # (io.containerd.grpc.v1.cri.containerd.runtimes) does not work under
  # containerd 2.x — see nestybox/sysbox#997.
  [plugins."io.containerd.cri.v1.runtime".containerd.runtimes.sysbox-runc]
    runtime_type = "io.containerd.runc.v2"
    [plugins."io.containerd.cri.v1.runtime".containerd.runtimes.sysbox-runc.options]
      BinaryName    = "/usr/bin/sysbox-runc"
      SystemdCgroup = true
EOF
}

write_containerd_unit() {
  if [[ -f "$CONTAINERD_UNIT" ]]; then
    log "containerd-sysbox.service already in place"
    return
  fi
  log "Writing $CONTAINERD_UNIT"
  # Hand-rolled unit (rather than the upstream containerd.service) so the
  # ExecStart points at our config and the description disambiguates this
  # process from k0s's bundled containerd in journalctl.
  cat > "$CONTAINERD_UNIT" <<EOF
[Unit]
Description=containerd container runtime (sysbox sidecar)
Documentation=https://containerd.io
After=network.target local-fs.target

[Service]
ExecStartPre=-/sbin/modprobe overlay
ExecStart=/usr/local/bin/containerd -c ${CONTAINERD_CONFIG}
Type=notify
Delegate=yes
KillMode=process
Restart=always
RestartSec=5
LimitNPROC=infinity
LimitCORE=infinity
LimitNOFILE=infinity
TasksMax=infinity
OOMScoreAdjust=-999

[Install]
WantedBy=multi-user.target
EOF
}

enable_services() {
  log "Enabling containerd-sysbox + sysbox on boot"
  systemctl daemon-reload
  systemctl enable --now containerd-sysbox sysbox sysbox-fs sysbox-mgr

  # Bail loudly if our socket didn't come up — Phase 2 depends on it.
  for i in 1 2 3 4 5 6 7 8 9 10; do
    [[ -S "$CONTAINERD_SOCKET_PATH" ]] && return
    sleep 1
  done
  echo "${CONTAINERD_SOCKET_PATH} did not appear — check 'journalctl -u containerd-sysbox'"
  exit 1
}

# ---------- Phase 2: post-EC-install ----------------------------------------

ec_installed() {
  # Don't pipe to `grep -q`: with set -o pipefail, grep -q's early exit
  # gives systemctl SIGPIPE (141) and the whole pipeline returns non-zero.
  local out
  out=$(systemctl list-unit-files 2>/dev/null) || return 1
  [[ "$out" == *$'\n'k0scontroller.service* || "$out" == k0scontroller.service* ]]
}

write_kubelet_dropin() {
  if [[ -f "$DROPIN_PATH" ]]; then
    log "kubelet containerd drop-in already in place: $DROPIN_PATH"
    return 1
  fi

  log "Writing kubelet → containerd-sysbox systemd drop-in"

  # Read the live ExecStart from the unit file. We need the exact args EC
  # generated for THIS host (node-ip, hostname, profile, labels) — copying a
  # static template would drift across EC versions.
  local unit=/etc/systemd/system/k0scontroller.service
  local exec_line
  exec_line=$(grep -m1 '^ExecStart=/usr/local/bin/k0s controller' "$unit") \
    || { echo "no ExecStart=... k0s controller line in $unit"; exit 1; }

  # Append our flag inside the value of --kubelet-extra-args. The systemd
  # escape for a literal space inside an argument value is \x20.
  if [[ "$exec_line" == *--container-runtime-endpoint=* ]]; then
    log "ExecStart already references --container-runtime-endpoint, skipping rewrite"
    return 1
  fi
  if [[ "$exec_line" != *--kubelet-extra-args=* ]]; then
    echo "k0scontroller ExecStart has no --kubelet-extra-args; can't safely append"
    exit 1
  fi

  local new_exec
  new_exec=$(printf '%s' "$exec_line" \
    | sed -E "s|(--kubelet-extra-args=[^[:space:]]*)|\1\\\\x20--container-runtime-endpoint=${CONTAINERD_ENDPOINT//\//\\/}|")

  mkdir -p "$(dirname "$DROPIN_PATH")"
  cat > "$DROPIN_PATH" <<EOF
# Force kubelet to talk to containerd-sysbox instead of the bundled containerd.
# Required because k0s overwrites containerRuntimeEndpoint in the rendered
# kubelet config regardless of what workerProfiles[].values say.
# Generated by sysbox-containerd2-setup.sh from EC's live ExecStart.
[Service]
ExecStart=
${new_exec}
EOF
  return 0
}

wait_for_helm_releases_deployed() {
  # Wait for every helm release in the cluster to reach status=deployed —
  # not just for individual deployments to be Available. Helm marks a
  # release `deployed` only after every resource is applied AND every
  # post-install hook has completed. The deployment-Available signal is
  # weaker: it can flip true while helm is still running post-install
  # hooks under the bundled containerd, which the runtime switch would
  # then kill mid-flight.
  local kubectl=/usr/local/bin/k0s
  local kubeconfig=/var/lib/embedded-cluster/k0s/pki/admin.conf
  local deadline=$(( $(date +%s) + 1200 ))  # 20 min

  log "Waiting up to 20 min for helm releases to reach deployed state"
  while [[ "$(date +%s)" -lt "$deadline" ]]; do
    local stuck
    stuck=$(KUBECONFIG="$kubeconfig" "$kubectl" kubectl get secret -A \
              -l 'owner=helm' \
              -o jsonpath='{range .items[*]}{.metadata.namespace}|{.metadata.labels.name}|{.metadata.labels.version}|{.metadata.labels.status}{"\n"}{end}' \
              2>/dev/null \
            | awk -F'|' '
                $0 != "" {
                  k = $1 "/" $2
                  if ($3+0 > v[k]) { v[k] = $3+0; s[k] = $4 }
                }
                END {
                  for (k in s) if (s[k] != "deployed") print k "=" s[k]
                }')
    if [[ -n "$stuck" ]]; then
      sleep 10
      continue
    fi
    log "All helm releases are deployed"
    return
  done
  echo "WARN: helm releases not deployed within 20 min — proceeding anyway:"
  echo "$stuck"
}

kubelet_runtime_version() {
  # Read containerRuntimeVersion that THIS node reports. If we're on the new
  # containerd, it'll say "containerd://2.x"; on the bundled one, "1.7.x".
  # crictl isn't on EC hosts so we go through the k0s-bundled kubectl.
  local kubeconfig=/var/lib/embedded-cluster/k0s/pki/admin.conf
  KUBECONFIG="$kubeconfig" /usr/local/bin/k0s kubectl get nodes \
    -o jsonpath='{.items[0].status.nodeInfo.containerRuntimeVersion}' 2>/dev/null
}

kubelet_on_new_runtime() {
  local v
  v=$(kubelet_runtime_version) || return 1
  [[ "$v" == "containerd://${CONTAINERD_VERSION}" ]]
}

restart_k0s_if_needed() {
  log "Reloading systemd and restarting k0scontroller"
  systemctl daemon-reload
  systemctl restart k0scontroller

  # Wait for kubelet to flip to the new runtime. node.status takes a few
  # seconds to refresh after restart. EC hosts don't ship crictl, so we
  # read what kubelet itself reports about its runtime via the bundled
  # k0s kubectl.
  for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    if kubelet_on_new_runtime; then
      log "kubelet talking to containerd-sysbox — node reports $(kubelet_runtime_version)"
      return
    fi
    sleep 4
  done
  echo "WARN: kubelet still on $(kubelet_runtime_version) after 60s — check 'journalctl -u k0scontroller'"
}

cleanup_orphan_containerd_containers() {
  # When kubelet flips from k0s's bundled containerd to ours, every
  # container that was running under containerd becomes an orphan: k0s's
  # containerd keeps its shims alive, so two copies of every workload
  # run side-by-side and contend for ports, files, and locks.
  #
  # Both containerds spawn processes literally named
  # 'containerd-shim-runc-v2'. We must kill only the BUNDLED ones
  # (children of k0s's containerd at ${K0S_CONTAINERD_BIN_PREFIX}), not
  # our own. `pgrep -f` matches against /proc/<pid>/cmdline (full binary
  # path), so it both scopes to k0s and avoids the TASK_COMM_LEN
  # 15-char truncation that would defeat `pgrep -x` against the basename.
  local pids
  pids=$(pgrep -f "^${K0S_CONTAINERD_BIN_PREFIX}containerd-shim-runc-v2 " 2>/dev/null || true)
  if [[ -z "$pids" ]]; then
    log "no orphan k0s-bundled containerd-shim processes to clean up"
    return
  fi
  local count
  count=$(printf '%s\n' "$pids" | wc -l | tr -d ' ')
  log "Cleaning up $count orphan k0s-bundled containerd-shim-runc-v2 processes"
  # shellcheck disable=SC2086
  kill -TERM $pids 2>/dev/null || true
  for i in 1 2 3 4 5 6 7 8 9 10; do
    pids=$(pgrep -f "^${K0S_CONTAINERD_BIN_PREFIX}containerd-shim-runc-v2 " 2>/dev/null || true)
    [[ -z "$pids" ]] && return
    sleep 1
  done
  log "Some k0s-bundled shims still alive after SIGTERM, sending SIGKILL"
  # shellcheck disable=SC2086
  kill -KILL $pids 2>/dev/null || true
}

apply_runtimeclass() {
  log "Applying sysbox-runc RuntimeClass"
  local kubeconfig=/var/lib/embedded-cluster/k0s/pki/admin.conf
  if [[ ! -f "$kubeconfig" ]]; then
    echo "WARN: no kubeconfig at $kubeconfig — skipping RuntimeClass apply"
    return
  fi
  KUBECONFIG="$kubeconfig" /usr/local/bin/k0s kubectl apply -f - <<'EOF'
apiVersion: node.k8s.io/v1
kind: RuntimeClass
metadata:
  name: sysbox-runc
handler: sysbox-runc
EOF
}

# ---------- Driver -----------------------------------------------------------

log "Phase 1: host prep"
install_containerd
install_runc
install_cni_plugins
install_sysbox
write_containerd_config
write_containerd_unit
enable_services

if ec_installed; then
  log "Phase 2: EC detected, applying kubelet redirect + RuntimeClass"
  # Gate restart/cleanup on whether kubelet has actually moved to the new
  # runtime, not on whether write_kubelet_dropin wrote the drop-in. The
  # drop-in being on disk doesn't mean k0s has reloaded its ExecStart, so
  # the truthy signal is what kubelet itself reports.
  write_kubelet_dropin || true
  if kubelet_on_new_runtime; then
    log "kubelet already reports $(kubelet_runtime_version), skipping restart + orphan cleanup"
  else
    wait_for_helm_releases_deployed
    restart_k0s_if_needed
    cleanup_orphan_containerd_containers
  fi
  apply_runtimeclass
else
  log "EC not installed yet — re-run this script after EC install completes"
fi

log "DONE"
