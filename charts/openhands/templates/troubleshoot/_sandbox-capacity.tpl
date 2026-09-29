{{/*
Sandbox capacity signals from the clusterResources events. Per-pod usage comes
from kotsadm's default nodeMetrics collector.
*/}}

{{- define "troubleshoot.analyzers.sandboxCapacity" -}}
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
          message: "Pods were evicted because a node ran low on memory or disk. Check node usage in node-metrics."
      - pass:
          when: "false"
          message: "No recent pod evictions."
{{- end -}}
