{{/*
The CloudNativePG Cluster manifest.

Rendered into two places, which must agree byte for byte, because who creates
this object depends on whether a migration is in progress:

  templates/postgres-cnpg.yaml   the ordinary tracked resource
  the migration hook's ConfigMap applied with kubectl by the pre-upgrade Job,
                                 which needs the cluster to exist before Helm
                                 applies the release's own resources

The ownership label and both annotations are written out explicitly so that the
object the hook creates is one Helm can adopt later. Helm refuses to take over an
object missing any of the three.

Bootstrap is always a plain initdb. The Cluster spec is therefore identical on a
fresh install and on a migrated one, and data movement stays entirely in the
migration hook rather than being a property of this object.

Note for anyone tempted to make this a Helm hook resource so that both paths
collapse into one: don't. Helm's default hook-delete-policy is
before-hook-creation, so the second upgrade deletes the Cluster and recreates it
— and CloudNativePG garbage-collects the PVCs of a deleted Cluster. That path
destroys the database on an upgrade that looks like a no-op.
*/}}
{{- define "openhands.cnpg.cluster" -}}
{{- $cnpg := .Values.cnpg -}}
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: {{ include "openhands.cnpg.clusterName" . }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- /*
    The shared labels carry app.kubernetes.io/managed-by from .Release.Service,
    which is what lets Helm adopt this object once the manifest renders it. Helm
    refuses an object missing that label or either annotation below, so keep all
    three whatever else changes here.
    */}}
    {{- include "openhands.labels" . | nindent 4 }}
    app.kubernetes.io/component: database
  annotations:
    meta.helm.sh/release-name: {{ .Release.Name }}
    meta.helm.sh/release-namespace: {{ .Release.Namespace }}
    # Never let Helm delete this object. It holds the database, and
    # CloudNativePG garbage-collects the PVCs of a deleted Cluster, so a delete
    # is unrecoverable. Without this, an upgrade that stops rendering the
    # Cluster — switching the migration back on, or uninstalling the release —
    # would take the data with it.
    helm.sh/resource-policy: keep
spec:
  instances: {{ $cnpg.instances }}
  imageName: {{ printf "%s:%s" $cnpg.image.repository (toString $cnpg.image.tag) | quote }}
  imagePullPolicy: {{ $cnpg.image.pullPolicy | default "IfNotPresent" }}
  {{- with $cnpg.imagePullSecrets }}
  imagePullSecrets:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  # Every OpenHands service connects as the postgres superuser and creates its
  # own database on startup, so superuser access must be on and its password
  # must come from a Secret we control rather than one CloudNativePG generates.
  enableSuperuserAccess: {{ $cnpg.enableSuperuserAccess }}
  {{- if $cnpg.enableSuperuserAccess }}
  superuserSecret:
    name: {{ $cnpg.superuserSecret.name }}
  {{- end }}
  # A PodDisruptionBudget on a single-instance cluster has no availability to
  # protect and blocks node drains, which stalls Kubernetes upgrades.
  enablePDB: {{ $cnpg.enablePDB }}
  primaryUpdateStrategy: {{ $cnpg.primaryUpdateStrategy }}
  primaryUpdateMethod: {{ $cnpg.primaryUpdateMethod }}
  bootstrap:
    initdb:
      encoding: {{ $cnpg.initdb.encoding | quote }}
      {{- with $cnpg.initdb.localeCollate }}
      localeCollate: {{ . | quote }}
      {{- end }}
      {{- with $cnpg.initdb.localeCType }}
      localeCType: {{ . | quote }}
      {{- end }}
  storage:
    size: {{ $cnpg.storage.size | quote }}
    {{- with $cnpg.storage.storageClass }}
    storageClass: {{ . | quote }}
    {{- end }}
  {{- with $cnpg.walStorage }}
  {{- if .enabled }}
  walStorage:
    size: {{ .size | quote }}
    {{- with .storageClass }}
    storageClass: {{ . | quote }}
    {{- end }}
  {{- end }}
  {{- end }}
  {{- with $cnpg.resources }}
  resources:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  {{- with $cnpg.parameters }}
  postgresql:
    parameters:
      {{- toYaml . | nindent 6 }}
  {{- end }}
  {{- with $cnpg.affinity }}
  affinity:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  {{- with $cnpg.nodeSelector }}
  nodeSelector:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  {{- with $cnpg.tolerations }}
  tolerations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
{{- end -}}
