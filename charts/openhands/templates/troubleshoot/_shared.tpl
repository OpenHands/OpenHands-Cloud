{{- define "troubleshoot.collectors.shared" -}}
- clusterInfo: {}
- clusterResources: {}
{{- if .Values.externalDatabase.enabled }}
{{- with .Values.externalDatabase }}
{{- if .host }}
- postgresql:
    collectorName: external-postgresql
    uri: postgresql://{{ .username | default "postgres" }}:@{{ .host }}:{{ .port | default 5432 }}/{{ .database | default "openhands" }}?sslmode={{ .sslMode | default "prefer" }}
    password:
      secretName: {{ .existingSecret | default "postgres-password" }}
      secretKey: {{ .existingSecretPasswordKey | default "password" }}
{{- end }}
{{- end }}
{{- end }}
{{- end -}}

{{- define "troubleshoot.analyzers.shared" -}}
- clusterVersion:
    outcomes:
      - fail:
          when: "< 1.19.0"
          message: "Kubernetes version 1.19.0 or later is required for OpenHands"
      - warn:
          when: "< 1.26.0"
          message: "Kubernetes version 1.26.0 or later is recommended"
      - pass:
          message: "Kubernetes version is supported"
- nodeResources:
    checkName: "Node Resources for OpenHands"
    outcomes:
      - fail:
          when: "count() < 1"
          message: "At least 1 node is required"
      - warn:
          when: "min(memoryCapacity) < 8Gi"
          message: "At least 8GB of memory per node is recommended for OpenHands with dependencies"
      - warn:
          when: "min(memoryCapacity) < 16Gi"
          message: "At least 16GB of memory per node is recommended for optimal performance"
      - warn:
          when: "min(cpuCapacity) < 4"
          message: "At least 4 CPU cores per node is recommended for OpenHands"
      - pass:
          message: "Node resources are sufficient"
- storageClass:
    checkName: "Default Storage Class"
    storageClassName: ""
    outcomes:
      - fail:
          when: "== false"
          message: "No default storage class found - required for PostgreSQL, Redis, and file storage"
      - pass:
          message: "Default storage class is available"
{{- if .Values.externalDatabase.enabled }}
- postgresql:
    checkName: "External PostgreSQL Database Health"
    collectorName: external-postgresql
    outcomes:
      - fail:
          when: "connected == false"
          message: "Cannot connect to external PostgreSQL database - check host, credentials, and network connectivity"
      - fail:
          when: "version == \"\""
          message: "External PostgreSQL version could not be determined"
      - warn:
          when: "version < 12.0.0"
          message: "External PostgreSQL version is older than recommended (12.0.0+)"
      - pass:
          when: "connected == true"
          message: "External PostgreSQL database is healthy"
{{- end }}
{{- end -}}
