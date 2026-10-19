{{/*
Direct-model preflight: warns, in the Admin Console, about saved LLM settings
that use a provider-direct model (azure/, bedrock/, vertex_ai/) with no API key
of their own. Those settings authenticated from provider credentials that used
to be injected into sandboxes; the credentials now stay in the LiteLLM proxy.

A runPod runs the app image's read-only scan (run_direct_model_preflight) against
the live database and the analyzers turn its summary lines into per-provider
warnings. Non-blocking: every outcome is pass/warn. Gated in preflights.yaml on
directModelPreflight.enabled.

The pod is created in the release namespace because it needs that namespace's
database and JWT secrets. On a fresh install the namespace and secrets do not
exist yet, the pod cannot start, and the analyzers skip (ignoreIfNoFiles).
*/}}

{{/* Only what the scan needs from the app env: database coordinates and the key used to decrypt stored profiles. */}}
{{- define "troubleshoot.directModel.env" -}}
{{- $wanted := list "DB_HOST" "DB_PORT" "DB_USER" "DB_NAME" "DB_PASS" "DB_SSL_MODE" "PGSSLMODE" "JWT_SECRET" -}}
{{- $env := list -}}
{{- range (include "openhands.env" . | fromYamlArray) -}}
{{- if has .name $wanted -}}{{- $env = append $env . -}}{{- end -}}
{{- end -}}
{{- $env = append $env (dict "name" "OPENHANDS_SUPPRESS_BANNER" "value" "1") -}}
{{- toYaml $env -}}
{{- end -}}

{{- define "troubleshoot.collectors.directModel" -}}
- runPod:
    name: direct-model-preflight
    namespace: {{ .Release.Namespace }}
    timeout: 120s
    podSpec:
      restartPolicy: Never
      {{- with .Values.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      containers:
        - name: scan
          image: "{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}"
          workingDir: /app
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
          command: ["/bin/sh", "-c"]
          args:
            - python -m run_direct_model_preflight
          env:
            {{- include "troubleshoot.directModel.env" . | nindent 12 }}
{{- end -}}

{{- /*
  Every analyzer must match one of its outcomes: an unmatched textAnalyze still
  returns an empty result row. The all-clear row is the only one that can say
  "pass" about the scan itself, and it requires the scan's own all-clear line, so
  a log from an image without the scan (a traceback) or a scan that errored out
  can never read as clean. The runPod log is keyed <collectorName>/<podName>.log
  (pod name == collector name); with no log (fresh install) both rows are skipped.
*/ -}}
{{- define "troubleshoot.analyzers.directModel" -}}
- textAnalyze:
    checkName: "Saved LLM settings and sandbox credentials"
    fileName: direct-model-preflight/direct-model-preflight.log
    ignoreIfNoFiles: true
    regex: '(?m)^Direct-model preflight: no LLM settings depend on sandbox provider credentials\.'
    outcomes:
      - pass:
          when: "true"
          message: "No saved LLM settings depend on provider credentials that sandboxes no longer receive."
      - warn:
          when: "false"
          message: "The check did not report an all-clear. It either found saved LLM settings that will stop authenticating (see the next result), or it could not run or could not read all of its data. The upgrade is not blocked. If you use azure/..., bedrock/... or vertex_ai/... model names, switch them to the managed openhands/<model> names listed in the LLM configuration, then rotate the provider credentials, because sandboxes created before this upgrade still hold the old values."
- textAnalyze:
    checkName: "Saved LLM settings that use provider-direct models"
    fileName: direct-model-preflight/direct-model-preflight.log
    ignoreIfNoFiles: true
    regex: '(?m)^  (?:azure|bedrock|vertex_ai)/\S+  ->'
    outcomes:
      - warn:
          when: "true"
          message: "Saved LLM settings use provider-direct models (azure/<deployment>, bedrock/<model id> or vertex_ai/<model>) with no API key. Provider credentials no longer reach sandboxes, so these will fail to authenticate after this upgrade. Switch each to openhands/azure-<deployment> or openhands/<model>, and make sure it is listed in the LLM configuration (Azure OpenAI Deployments, Bedrock Model IDs or Vertex AI Models). Then rotate the provider credentials, because sandboxes created before this upgrade still hold the old values."
      - pass:
          when: "false"
          message: "No provider-direct model names appear in the scan output."
{{- end -}}

