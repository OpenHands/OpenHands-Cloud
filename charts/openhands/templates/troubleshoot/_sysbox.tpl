{{/*
Sysbox precondition preflight: when "Sandbox Isolation" is set to Sysbox, verify
the host can actually run Sysbox sandboxes before install. Sysbox sandboxes run
with hostUsers: false (native Kubernetes user namespaces), which requires a
recent kernel. If the kernel is too old the deploy would come up broken
(sandboxes can't mount sysfs), so the kernel analyzer fails and tells the
operator to switch Sandbox Isolation to "runc".

The Sysbox runtime is installed on every node from binaries baked into the
installer image (no download, and not tied to Debian/Ubuntu), so there is no
egress or package-manager precondition to check — the kernel floor is the only
hard gate.

Preconditions checked (kernel requirement is from the Kubernetes user-namespaces
docs: kernel 6.3 is where tmpfs gained idmap-mount support; containerd 2.0+ ships
with this embedded-cluster's k8s 1.36, so it is not re-checked here):
  - Node kernel >= 6.3 ...... required for hostUsers: false userns        (fail)
  - Node OS is Debian/Ubuntu  the only combination validated end-to-end   (warn)

Both are read from the default clusterResources collector's
cluster-resources/nodes.json via textAnalyze (no pod, no hostPath -> Pod Security
Standards safe). textAnalyze matches if ANY node satisfies the check; on the
single-node (or homogeneous multi-node) embedded-cluster installs this feature
targets that is exact.

Gated in preflights.yaml on Sysbox being the selected isolation (runtime-api's
RUNTIME_CLASS == "sysbox-runc", set from the config option in replicated/openhands.yaml).
*/}}

{{- define "troubleshoot.sysbox.vars" -}}
{{- $rtApiEnv := (index .Values "runtime-api" | default dict).env | default dict -}}
selected: {{ eq ($rtApiEnv.RUNTIME_CLASS | toString) "sysbox-runc" }}
{{- end -}}

{{- define "troubleshoot.analyzers.sysbox" -}}
- textAnalyze:
    checkName: "Sysbox: node kernel is 6.3 or newer"
    fileName: cluster-resources/nodes.json
    # Matches kernelVersion >= 6.3: "6." with minor >= 3 (6.3-6.9 or two-digit
    # 6.10+), or any major >= 7. Anchored to the value so a 5.15 kernel's "15"
    # can't masquerade as a major version.
    regex: '"kernelVersion":\s*"(6\.([3-9]|[1-9][0-9]+)|([7-9]|[1-9][0-9]+)\.)'
    outcomes:
      - pass:
          when: "true"
          message: "Kernel is 6.3 or newer."
      - fail:
          when: "false"
          message: "Sysbox requires Linux kernel 6.3+ (e.g. Ubuntu 24.04). Upgrade the kernel, or set Sandbox Isolation to runc."
- textAnalyze:
    checkName: "Sysbox: node OS is Debian/Ubuntu"
    fileName: cluster-resources/nodes.json
    regex: '"osImage":\s*"(Ubuntu|Debian)'
    outcomes:
      - pass:
          when: "true"
          message: "Node OS is Debian/Ubuntu."
      - warn:
          when: "false"
          message: "Sysbox is validated only on Debian/Ubuntu. If sandboxes fail to start, set Sandbox Isolation to runc."
{{- end -}}
