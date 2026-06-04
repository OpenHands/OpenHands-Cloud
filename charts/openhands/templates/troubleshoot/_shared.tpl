{{- define "troubleshoot.collectors.shared" -}}
- clusterInfo: {}
- clusterResources: {}
{{- if .Values.externalDatabase.enabled }}
{{- with .Values.externalDatabase }}
{{- if .host }}
- postgresql:
    collectorName: external-postgresql
    uri: postgresql://{{ .username | default "postgres" }}:@{{ .host }}:{{ .port | default 5432 }}/{{ .database | default "openhands" }}?sslmode=disable
    tls:
      disabled: true
    password:
      secretName: {{ .existingSecret | default "postgres-password" }}
      secretKey: {{ .existingSecretPasswordKey | default "password" }}
- runPod:
    name: postgres-permissions-check
    namespace: {{ $.Release.Namespace }}
    podSpec:
      containers:
        - name: postgres-check
          image: postgres:14-alpine
          command:
            - /bin/sh
            - -c
            - >-
              psql -t -A -c
              "SELECT 'SCHEMA_CREATE: ' || has_schema_privilege(current_user, 'public', 'CREATE')::text
              UNION ALL
              SELECT 'SCHEMA_USAGE: ' || has_schema_privilege(current_user, 'public', 'USAGE')::text
              UNION ALL
              SELECT 'DB_CREATE: ' || has_database_privilege(current_user, current_database(), 'CREATE')::text"
          env:
            - name: PGPASSWORD
              valueFrom:
                secretKeyRef:
                  name: {{ .existingSecret | default "postgres-password" }}
                  key: {{ .existingSecretPasswordKey | default "password" }}
            - name: PGUSER
              value: {{ .username | default "postgres" }}
            - name: PGHOST
              value: {{ .host }}
            - name: PGPORT
              value: "{{ .port | default 5432 }}"
            - name: PGDATABASE
              value: {{ .database | default "openhands" }}
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
- textAnalyze:
    checkName: "External PostgreSQL Schema CREATE Permission"
    fileName: postgres-permissions-check/postgres-permissions-check.log
    regex: "SCHEMA_CREATE: true"
    outcomes:
      - pass:
          when: "true"
          message: "{{ .Values.externalDatabase.username | default "postgres" }} has CREATE on public schema ({{ .Values.externalDatabase.database | default "openhands" }})"
      - fail:
          when: "false"
          message: "{{ .Values.externalDatabase.username | default "postgres" }} missing CREATE on public schema ({{ .Values.externalDatabase.database | default "openhands" }})"
- textAnalyze:
    checkName: "External PostgreSQL Schema USAGE Permission"
    fileName: postgres-permissions-check/postgres-permissions-check.log
    regex: "SCHEMA_USAGE: true"
    outcomes:
      - pass:
          when: "true"
          message: "{{ .Values.externalDatabase.username | default "postgres" }} has USAGE on public schema ({{ .Values.externalDatabase.database | default "openhands" }})"
      - fail:
          when: "false"
          message: "{{ .Values.externalDatabase.username | default "postgres" }} missing USAGE on public schema ({{ .Values.externalDatabase.database | default "openhands" }})"
- textAnalyze:
    checkName: "External PostgreSQL Database CREATE Permission"
    fileName: postgres-permissions-check/postgres-permissions-check.log
    regex: "DB_CREATE: true"
    outcomes:
      - pass:
          when: "true"
          message: "{{ .Values.externalDatabase.username | default "postgres" }} has CREATE on database ({{ .Values.externalDatabase.database | default "openhands" }})"
      - fail:
          when: "false"
          message: "{{ .Values.externalDatabase.username | default "postgres" }} missing CREATE on database ({{ .Values.externalDatabase.database | default "openhands" }})"
{{- end }}
{{- end -}}
