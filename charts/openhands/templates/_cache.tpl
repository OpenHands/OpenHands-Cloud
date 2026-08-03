{{/*
Bundled cache backend selection, in precedence order:

  redis    the bitnami redis subchart (redis.enabled)
  valkey   the official valkey subchart (valkey.enabled)

Redis wins when both are enabled, so enabling valkey by itself deploys it
without repointing the app.

Only the host differs between them. Both speak RESP on 6379 and read the same
credential out of the same Secret, so the app's REDIS_* variables keep their
names and values either way.
*/}}

{{/* "redis", "valkey", or "" when no bundled cache is deployed. */}}
{{- define "openhands.cache.backend" -}}
{{- if .Values.redis.enabled -}}
redis
{{- else if .Values.valkey.enabled -}}
valkey
{{- end -}}
{{- end -}}

{{/* In-cluster hostname of the active bundled cache. */}}
{{- define "openhands.cache.host" -}}
{{- if eq (include "openhands.cache.backend" .) "valkey" -}}
{{ printf "%s-valkey" .Release.Name }}
{{- else -}}
{{ printf "%s-redis-master" .Release.Name }}
{{- end -}}
{{- end -}}

{{/* Secret holding the cache password. Both backends point at the same one. */}}
{{- define "openhands.cache.secretName" -}}
{{- if eq (include "openhands.cache.backend" .) "valkey" -}}
{{ .Values.valkey.auth.usersExistingSecret }}
{{- else -}}
{{ .Values.redis.auth.existingSecret }}
{{- end -}}
{{- end -}}

{{- define "openhands.cache.passwordKey" -}}
{{- if eq (include "openhands.cache.backend" .) "valkey" -}}
{{ .Values.valkey.auth.aclUsers.default.passwordKey }}
{{- else -}}
redis-password
{{- end -}}
{{- end -}}
