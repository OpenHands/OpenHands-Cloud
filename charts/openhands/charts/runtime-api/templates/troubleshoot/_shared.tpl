{{- define "runtime.troubleshoot.collectors.shared" -}}
- clusterInfo: {}
- clusterResources: {}
- configMap:
    name: {{ .Release.Name }}-config
    namespace: {{ .Release.Namespace }}
{{- end -}}

{{- define "runtime.troubleshoot.analyzers.shared" -}}
- clusterVersion:
    outcomes:
      - fail:
          when: "< 1.26.0"
          message: "Kubernetes version 1.26.0 or later is required"
      - pass:
          message: "Kubernetes version is supported"
- nodeResources:
    checkName: "Node Resources"
    outcomes:
      - fail:
          when: "count() < 1"
          message: "At least 1 node is required"
      - warn:
          when: "min(memoryCapacity) < 2Gi"
          message: "At least 2GB of memory per node is recommended"
      - warn:
          when: "min(cpuCapacity) < 1"
          message: "At least 1 CPU core per node is recommended"
      - pass:
          message: "Node resources are sufficient"
- storageClass:
    checkName: "Default Storage Class"
    storageClassName: ""
    outcomes:
      - fail:
          when: "== false"
          message: "No default storage class found"
      - pass:
          message: "Default storage class is available"
{{- end -}}
