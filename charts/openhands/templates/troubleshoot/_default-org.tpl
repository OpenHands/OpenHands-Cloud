{{/*
Default-organization rename guard.

The KOTS default-org bootstrap identifies the organization BY NAME: changing
the "Organization Name" config after an org was bootstrapped creates a second
organization rather than renaming the first. These preflight pieces warn when
the configured name matches no existing org while team orgs already exist —
the signature of an accidental rename. Gated on the default-org feature being
enabled (values.env.OPENHANDS_DEFAULT_ORG_ENABLED).

The check mirrors the app's own DB wiring from _env.yaml (bundled vs external
postgres, password from postgresql.auth.existingSecret) and reuses the
waitForDb postgres-client image, which KOTS already overrides to the
Replicated proxy. Personal workspaces (org id == user id) are excluded from
the "team orgs exist" test. Any psql failure (fresh install, DB not yet
provisioned) downgrades to DEFAULT_ORG_CHECK_SKIPPED, which passes.
*/}}

{{- define "troubleshoot.defaultOrg.enabled" -}}
{{- if eq (toString (.Values.env).OPENHANDS_DEFAULT_ORG_ENABLED) "true" -}}
true
{{- end -}}
{{- end -}}

{{- define "troubleshoot.collectors.defaultOrg" -}}
- runPod:
    collectorName: default-org-name-check
    name: default-org-name-check
    namespace: {{ .Release.Namespace }}
    timeout: 90s
    podSpec:
      restartPolicy: Never
      {{- with .Values.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      containers:
        - name: default-org-name-check
          image: {{ .Values.keycloak.waitForDb.image | quote }}
          env:
            {{- if .Values.postgresql.enabled }}
            - name: DB_HOST
              value: "{{ .Release.Name }}-postgresql"
            - name: DB_PORT
              value: "{{ .Values.postgresql.primary.service.ports.postgresql | default 5432 }}"
            - name: DB_USER
              value: "{{ .Values.postgresql.auth.username }}"
            - name: DB_NAME
              value: "{{ .Values.postgresql.auth.database }}"
            {{- else }}
            - name: DB_HOST
              value: "{{ .Values.externalDatabase.host }}"
            - name: DB_PORT
              value: "{{ .Values.externalDatabase.port | default 5432 }}"
            - name: DB_USER
              value: "{{ .Values.externalDatabase.username | default "postgres" }}"
            - name: DB_NAME
              value: "{{ .Values.externalDatabase.database | default "openhands" }}"
            {{- end }}
            - name: PGSSLMODE
              value: "{{ if not .Values.postgresql.enabled }}{{ .Values.externalDatabase.sslMode | default "prefer" }}{{ else }}prefer{{ end }}"
            - name: DB_PASS
              valueFrom:
                secretKeyRef:
                  name: {{ .Values.postgresql.auth.existingSecret }}
                  key: password
            - name: DEFAULT_ORG_NAME
              value: {{ (.Values.env).OPENHANDS_DEFAULT_ORG_NAME | default "" | quote }}
          command: ['sh', '-c']
          args:
            - |
              PGPASSWORD="$DB_PASS" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -t -A \
                -v name="$DEFAULT_ORG_NAME" \
                -c "SELECT CASE WHEN EXISTS (SELECT 1 FROM org WHERE name = :'name') THEN 'DEFAULT_ORG_OK' WHEN EXISTS (SELECT 1 FROM org o LEFT JOIN \"user\" u ON u.id = o.id WHERE u.id IS NULL) THEN 'DEFAULT_ORG_RENAME_RISK' ELSE 'DEFAULT_ORG_FRESH' END" \
                2>/dev/null || echo 'DEFAULT_ORG_CHECK_SKIPPED'
{{- end -}}

{{- define "troubleshoot.analyzers.defaultOrg" -}}
- textAnalyze:
    checkName: Default Organization Name
    fileName: default-org-name-check/default-org-name-check.log
    regex: 'DEFAULT_ORG_RENAME_RISK'
    outcomes:
      - warn:
          when: "true"
          message: "No organization matches the configured Default Organization name, but team organizations already exist. Deploying will create a NEW organization with this name on the next owner sign-in; existing organizations and their data remain, and auto-added members are moved to the new organization on their next sign-in. If you meant to rename an existing organization, rename it in the app instead (organization settings) and set this field to match."
      - pass:
          when: "false"
          message: "The Default Organization name is consistent with existing organizations."
{{- end -}}
