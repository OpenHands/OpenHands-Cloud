{{- /*
crd-check.hook — pre-install/pre-upgrade hook that waits for a list of CRDs
to reach the `Established` condition before the consuming chart's other
resources apply. Useful when a release depends on CRDs installed by a
separate release earlier in the same KOTS or Helm operation: `helm install
--wait` only waits for pods to become ready, not for CRD apiserver
registration, so a fast follow-on apply can race.

The named template renders nothing unless `.Values.crdCheck.enabled` is true.

Consumers must define a `crdCheck` block in their own values.yaml, e.g.:

  crdCheck:
    enabled: false
    timeout: 120s
    backoffLimit: 6
    crds: []
    image:
      repository: docker.io/rancher/kubectl
      tag: v1.33.0
      pullPolicy: IfNotPresent
    imagePullSecrets: []
    resources:
      requests: { cpu: 50m, memory: 64Mi }
      limits:   { memory: 128Mi }

Consume from a parent chart with a one-line template file:

  {{ include "crd-check.hook" . }}
*/}}
{{- define "crd-check.hook" -}}
{{- if .Values.crdCheck.enabled }}
{{- $saName := printf "%s-crd-check" .Release.Name }}
{{- $roleName := printf "%s-crd-check" .Release.Name }}
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {{ $saName }}
  namespace: {{ .Release.Namespace }}
  annotations:
    "helm.sh/hook": pre-install,pre-upgrade
    "helm.sh/hook-weight": "-10"
    "helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: {{ $roleName }}
  annotations:
    "helm.sh/hook": pre-install,pre-upgrade
    "helm.sh/hook-weight": "-9"
    "helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded
rules:
  - apiGroups: ["apiextensions.k8s.io"]
    resources: ["customresourcedefinitions"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: {{ $roleName }}
  annotations:
    "helm.sh/hook": pre-install,pre-upgrade
    "helm.sh/hook-weight": "-9"
    "helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: {{ $roleName }}
subjects:
  - kind: ServiceAccount
    name: {{ $saName }}
    namespace: {{ .Release.Namespace }}
---
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ .Release.Name }}-crd-check
  namespace: {{ .Release.Namespace }}
  annotations:
    "helm.sh/hook": pre-install,pre-upgrade
    "helm.sh/hook-weight": "-5"
    "helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded
spec:
  backoffLimit: {{ .Values.crdCheck.backoffLimit }}
  ttlSecondsAfterFinished: 300
  template:
    metadata:
      labels:
        app.kubernetes.io/name: {{ .Release.Name }}-crd-check
    spec:
      serviceAccountName: {{ $saName }}
      restartPolicy: OnFailure
      {{- with .Values.crdCheck.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      containers:
        - name: crd-check
          image: "{{ .Values.crdCheck.image.repository }}:{{ .Values.crdCheck.image.tag }}"
          imagePullPolicy: {{ .Values.crdCheck.image.pullPolicy }}
          command:
            - kubectl
            - wait
            - --for=condition=Established
            - --timeout={{ .Values.crdCheck.timeout }}
            {{- range .Values.crdCheck.crds }}
            - crd/{{ . }}
            {{- end }}
          resources:
            {{- toYaml .Values.crdCheck.resources | nindent 12 }}
{{- end }}
{{- end -}}
