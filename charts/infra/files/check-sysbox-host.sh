#!/usr/bin/env bash
set -uo pipefail

failed=0
missing=()
for tool in rsync modprobe iptables fusermount3; do
  command -v "$tool" >/dev/null 2>&1 || missing+=("$tool")
done
if ((${#missing[@]})); then
  echo "SYSBOX_TOOLS=FAIL"
  echo "Missing host commands: ${missing[*]}. On Debian/Ubuntu, install rsync, kmod, iptables and fuse3."
  failed=1
else
  echo "SYSBOX_TOOLS=OK"
fi

# Opening the device catches kernels without FUSE and unusable device nodes.
if [ -c /dev/fuse ] && (exec 3<>/dev/fuse) 2>/dev/null; then
  echo "SYSBOX_FUSE=OK"
else
  echo "SYSBOX_FUSE=FAIL"
  echo "Cannot open /dev/fuse. Enable FUSE on the host (sudo modprobe fuse); if unavailable, install a kernel with CONFIG_FUSE_FS support."
  failed=1
fi

echo "SYSBOX_CHECK_COMPLETE=OK"
exit "$failed"
