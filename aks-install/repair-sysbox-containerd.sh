#!/usr/bin/env bash
# Evaluation escape hatch; requires approval before restarting node containerd.
# Run inside the official sysbox-deploy-k8s container on the sandbox node.
# Host paths and systemd mounts are supplied by that installer DaemonSet.
set -euo pipefail
config=/mnt/host/etc/containerd/config.toml
backup=${config}.before-aks-sysbox-repair
candidate=$(mktemp /mnt/host/etc/containerd/config.toml.aks-repair.XXXXXX)
trap 'rm -f "$candidate"' EXIT
old='plugins."io.containerd.grpc.v1.cri".containerd.runtimes.sysbox-runc'
new='plugins."io.containerd.cri.v1.runtime".containerd.runtimes.sysbox-runc'

grep -Fq '[plugins."io.containerd.cri.v1.runtime"]' "$config"
test "$(grep -Fc "$old" "$config")" -eq 2
if grep -Fq "$new" "$config"; then
  echo 'Correct Sysbox registration already exists; inspect before changing.' >&2
  exit 1
fi
if test -e "$backup"; then
  cmp -s "$config" "$backup"
else
  cp -p "$config" "$backup"
fi
sed 's/plugins\."io\.containerd\.grpc\.v1\.cri"\.containerd\.runtimes\.sysbox-runc/plugins."io.containerd.cri.v1.runtime".containerd.runtimes.sysbox-runc/g' "$config" > "$candidate"
test "$(grep -Fc "$new" "$candidate")" -eq 2
# TOML structure is validated locally before invoking this script. Verify the
# candidate differs only by the two intended header replacements.
sed 's/plugins\."io\.containerd\.cri\.v1\.runtime"\.containerd\.runtimes\.sysbox-runc/plugins."io.containerd.grpc.v1.cri".containerd.runtimes.sysbox-runc/g' "$candidate" | cmp -s - "$config"
cp "$candidate" "$config"
if ! systemctl restart containerd; then
  cp -p "$backup" "$config"
  systemctl restart containerd
  echo 'Restart failed; restored original configuration.' >&2
  exit 1
fi
systemctl is-active containerd
echo "Original configuration retained at $backup"
echo 'Moved only the two Sysbox table headers; existing AKS runtime settings retained.'
