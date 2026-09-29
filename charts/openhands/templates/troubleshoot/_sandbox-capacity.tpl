{{/*
Sandbox sizing data: kubelet usage per node, plus a short-lived read-only probe
pod per node for cgroup history (peak memory, OOM kills, throttling) and
workspace sizes. Not an exec collector: those run in only the first matched pod.
*/}}

{{- define "troubleshoot.collectors.sandboxCapacity" -}}
{{- $cfg := .Values.replicated.sandboxCapacity -}}
{{- if $cfg.enabled }}
- nodeMetrics: {}
- runDaemonSet:
    name: sandbox-capacity-node-probe
    namespace: {{ .Release.Namespace }}
    timeout: {{ $cfg.timeout }}
    podSpec:
      {{- with .Values.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      tolerations:
        - operator: Exists
      containers:
        - name: probe
          image: "{{ $cfg.image.repository | default .Values.image.repository }}:{{ $cfg.image.tag | default .Values.image.tag }}"
          command: ["python3", "-c"]
          args:
            - |
              import json, os, subprocess, time
              CG, VOL = "/host/cgroup", "/host/local-volumes"
              def read(p):
                  try:
                      with open(p) as f:
                          return f.read()
                  except OSError:
                      return ""
              def num(p):
                  t = read(p).strip()
                  return int(t) if t.isdigit() else (t or None)
              def kv(p):
                  out = {}
                  for line in read(p).splitlines():
                      f = line.split()
                      if len(f) == 2 and f[1].isdigit():
                          out[f[0]] = int(f[1])
                  return out
              def psi(p):
                  out = {}
                  for line in read(p).splitlines():
                      f = line.split()
                      out[f[0]] = {k: float(v) for k, v in (x.split("=") for x in f[1:])}
                  return out
              def cgroup(d):
                  m, e, c = kv(d + "/memory.stat"), kv(d + "/memory.events"), kv(d + "/cpu.stat")
                  limit = num(d + "/memory.max")
                  # Limits may sit only on container cgroups; fold those in.
                  fold_cpu, fold_mem = not c.get("nr_periods"), not isinstance(limit, int)
                  for k in os.scandir(d) if fold_cpu or fold_mem else []:
                      if not k.is_dir():
                          continue
                      if fold_cpu:
                          kc = kv(k.path + "/cpu.stat")
                          for f in ("nr_periods", "nr_throttled", "throttled_usec"):
                              c[f] = c.get(f, 0) + kc.get(f, 0)
                      kl = num(k.path + "/memory.max")
                      if fold_mem and isinstance(kl, int) and not (isinstance(limit, int) and limit <= kl):
                          limit = kl
                  return {
                      "memory_current": num(d + "/memory.current"),
                      "memory_peak": num(d + "/memory.peak"),
                      "memory_max": limit,
                      "anon": m.get("anon"), "file": m.get("file"),
                      "oom": e.get("oom"), "oom_kill": e.get("oom_kill"),
                      "cpu_usage_usec": c.get("usage_usec"),
                      "nr_periods": c.get("nr_periods"), "nr_throttled": c.get("nr_throttled"),
                      "throttled_usec": c.get("throttled_usec"),
                      "memory_pressure": psi(d + "/memory.pressure"),
                      "cpu_pressure": psi(d + "/cpu.pressure"),
                  }
              pods = {}
              # cgroupfs driver: kubepods/<qos>/pod<uid>; systemd: kubepods-<qos>-pod<uid>.slice
              for root, dirs, _ in os.walk(CG):
                  keep = []
                  for d in dirs:
                      if d.startswith("pod") or (d.endswith(".slice") and "-pod" in d):
                          uid = d.rsplit("pod", 1)[1].removesuffix(".slice").replace("_", "-")
                          if len(uid) == 36:
                              pods[uid] = cgroup(os.path.join(root, d))
                      elif d.startswith(("kubepods", "burstable", "besteffort")):
                          keep.append(d)
                  dirs[:] = keep
              volumes, deadline = {}, time.time() + {{ $cfg.volumeScanSeconds }}
              if os.path.isdir(VOL):
                  for name in sorted(os.listdir(VOL)):
                      left = int(deadline - time.time())
                      if left <= 0:
                          volumes[name] = "skipped"
                          continue
                      try:
                          out = subprocess.run(["du", "-sxk", os.path.join(VOL, name)], capture_output=True, text=True, timeout=left).stdout
                          volumes[name] = int(out.split()[0]) if out else None
                      except subprocess.TimeoutExpired:
                          volumes[name] = "timeout"
              meminfo = {l.split(":")[0]: int(l.split()[1]) for l in read("/proc/meminfo").splitlines() if l.split()[0] in ("MemTotal:", "MemAvailable:")}
              node_psi = {r: psi("/proc/pressure/" + r) for r in ("cpu", "memory", "io")}
              # Integer summary keys for the bundle's textAnalyze outcomes.
              summary = {
                  "oom_kill_pods": sum(1 for p in pods.values() if (p["oom_kill"] or 0) > 0),
                  "memory_pressure_avg300_pct": int(node_psi["memory"].get("some", {}).get("avg300", 0)),
                  "cpu_pressure_avg300_pct": int(node_psi["cpu"].get("some", {}).get("avg300", 0)),
              }
              print(json.dumps({
                  "node": os.environ.get("NODE_NAME"), "collected_at": int(time.time()),
                  "uptime_s": int(float(read("/proc/uptime").split()[0] or 0)),
                  "cpus": os.cpu_count(), "meminfo_kb": meminfo, "loadavg": read("/proc/loadavg").split()[:3],
                  "pressure": node_psi, "summary": summary, "pods": pods, "local_volumes_kb": volumes,
              }))
          env:
            - name: NODE_NAME
              valueFrom:
                fieldRef:
                  fieldPath: spec.nodeName
          securityContext:
            runAsUser: 0
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
              # du must enter workspace dirs owned by the sandbox user.
              add: ["DAC_READ_SEARCH"]
          # No ephemeral request, so the probe still schedules on a node whose
          # sandboxes have reserved all of it -- the case this data is for.
          resources:
            requests:
              cpu: 10m
              memory: 32Mi
            limits:
              memory: 256Mi
          volumeMounts:
            - name: cgroup
              mountPath: /host/cgroup
              readOnly: true
            {{- if $cfg.localVolumePath }}
            - name: local-volumes
              mountPath: /host/local-volumes
              readOnly: true
            {{- end }}
      volumes:
        - name: cgroup
          hostPath:
            path: /sys/fs/cgroup
            type: Directory
        {{- if $cfg.localVolumePath }}
        - name: local-volumes
          hostPath:
            path: {{ $cfg.localVolumePath }}
            type: Directory
        {{- end }}
{{- end }}
{{- end -}}

{{- define "troubleshoot.analyzers.sandboxCapacity" -}}
{{- if .Values.replicated.sandboxCapacity.enabled }}
- event:
    checkName: "Sandbox scheduling: node resources exhausted"
    collectorName: cluster-resources
    namespace: {{ .Release.Namespace }}
    reason: FailedScheduling
    regex: 'Insufficient (cpu|memory|ephemeral-storage)'
    outcomes:
      - fail:
          when: "true"
          message: "Pods could not be scheduled because a node ran out of reservable CPU, memory or ephemeral storage. New conversations wait until capacity frees up. Add a node, or lower the per-sandbox reservations in Sandbox Configuration."
      - pass:
          when: "false"
          message: "No recent scheduling failures from exhausted node resources."
- event:
    checkName: "Pod evictions from node pressure"
    collectorName: cluster-resources
    namespace: {{ .Release.Namespace }}
    reason: Evicted
    outcomes:
      - warn:
          when: "true"
          message: "Pods were evicted because a node ran low on memory or disk. Check node usage in node-metrics and sandbox-capacity-node-probe."
      - pass:
          when: "false"
          message: "No recent pod evictions."
- textAnalyze:
    checkName: "Out-of-memory kills"
    fileName: sandbox-capacity-node-probe/*.log
    regexGroups: '"oom_kill_pods": (?P<OomPods>\d+)'
    ignoreIfNoFiles: true
    outcomes:
      - warn:
          when: "OomPods > 0"
          message: "At least one pod has had a process killed for exceeding its memory limit. For sandboxes, consider raising the Memory Limit in Sandbox Configuration."
      - pass:
          message: "No out-of-memory kills recorded."
- textAnalyze:
    checkName: "Node memory pressure"
    fileName: sandbox-capacity-node-probe/*.log
    regexGroups: '"memory_pressure_avg300_pct": (?P<MemPsi>\d+)'
    ignoreIfNoFiles: true
    outcomes:
      - warn:
          when: "MemPsi >= 10"
          message: "Work on a node was stalled waiting for memory over the last 5 minutes. The node is short on memory for its current load."
      - pass:
          message: "No sustained memory pressure on the node."
- textAnalyze:
    checkName: "Node CPU pressure"
    fileName: sandbox-capacity-node-probe/*.log
    regexGroups: '"cpu_pressure_avg300_pct": (?P<CpuPsi>\d+)'
    ignoreIfNoFiles: true
    outcomes:
      - warn:
          when: "CpuPsi >= 50"
          message: "Work on a node was waiting for CPU much of the last 5 minutes. Sandboxes run slower; add CPU if this persists during normal load."
      - pass:
          message: "No sustained CPU pressure on the node."
{{- end }}
{{- end -}}
