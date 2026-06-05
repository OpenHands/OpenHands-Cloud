{{/*
TLS SAN + DNS hostname preflight: checks the uploaded cert covers every hostname
OpenHands serves and that those names resolve. A runPod runs openssl + nslookup
and prints SAN_<svc>=OK|FAIL / DNS_<svc>=OK|FAIL tokens that the analyzers turn
into per-hostname warnings. Gated in preflights.yaml on the cert being present.
*/}}

{{/*
Resolve every input from existing chart values and emit them as a small YAML dict
the collector/analyzer templates re-parse with fromYaml. dig is avoided on .Values
(its type assertion rejects Helm's Values type); default dict / index traverse it
safely. Value paths:
  cert (decoded PEM) ..... keycloak.ingress.secrets[0].certificate (b64-encoded here)
  appHost ................ ingress.host
  authHost ............... keycloak.ingress.hostname
  llmHost ................ litellm-helm.ingress.hosts[0].host
  rtApiHost .............. runtime-api.ingress.host
  rtBaseHost ............. runtime-api.env.RUNTIME_BASE_URL
  analyticsHost .......... laminar.frontend.ingress.hostname
  routingMode ............ runtime-api.env.RUNTIME_ROUTING_MODE ("path" => path
                           routing; empty => subdomain, the default)
  analyticsEnabled ....... laminar.enabled
  probeImage ............. proxy base of image.repository + docker.io/alpine/openssl,
                           so the pull uses the proxy (and the pull secret KOTS
                           injects onto runPods) instead of Docker Hub directly.
*/}}
{{- define "troubleshoot.tlsHostname.vars" -}}
{{- $kcIng := (.Values.keycloak | default dict).ingress | default dict -}}
{{- $secrets := $kcIng.secrets | default list -}}
{{- $cert := "" -}}{{- if $secrets -}}{{- $cert = (index $secrets 0).certificate | toString -}}{{- end -}}
{{- $llmIng := (index .Values "litellm-helm" | default dict).ingress | default dict -}}
{{- $llmHosts := $llmIng.hosts | default list -}}
{{- $llmHost := "" -}}{{- if $llmHosts -}}{{- $llmHost = (index $llmHosts 0).host -}}{{- end -}}
{{- $rtApi := index .Values "runtime-api" | default dict -}}
{{- $rtApiEnv := $rtApi.env | default dict -}}
{{- $lam := .Values.laminar | default dict -}}
{{- $lamFrontIng := ($lam.frontend | default dict).ingress | default dict -}}
{{- $repo := (.Values.image | default dict).repository | default "" -}}
cert: {{ $cert | b64enc | quote }}
appHost: {{ (.Values.ingress | default dict).host | default "" | quote }}
authHost: {{ $kcIng.hostname | default "" | quote }}
llmHost: {{ $llmHost | quote }}
rtApiHost: {{ ($rtApi.ingress | default dict).host | default "" | quote }}
rtBaseHost: {{ $rtApiEnv.RUNTIME_BASE_URL | default "" | quote }}
analyticsHost: {{ $lamFrontIng.hostname | default "" | quote }}
routingMode: {{ $rtApiEnv.RUNTIME_ROUTING_MODE | default "" | quote }}
analyticsEnabled: {{ $lam.enabled | default false }}
probeImage: {{ printf "%s/docker.io/alpine/openssl:3.5.6" (trimSuffix "/ghcr.io/openhands/enterprise-server" $repo) | quote }}
{{- end -}}

{{- define "troubleshoot.collectors.tlsHostname" -}}
{{- $p := include "troubleshoot.tlsHostname.vars" . | fromYaml -}}
- runPod:
    name: tls-hostname-check
    # 'default' exists at the pre-install gate (the release namespace may not
    # yet); KOTS injects the proxy pull secret onto the pod regardless of ns.
    namespace: default
    timeout: 90s
    podSpec:
      restartPolicy: Never
      # Satisfy restricted Pod Security Standards (also satisfies baseline); the
      # probe reads the cert from an env var, so no writable filesystem.
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        seccompProfile:
          type: RuntimeDefault
      containers:
        - name: probe
          # alpine/openssl ships openssl + busybox (sh, base64, nslookup). Its
          # ENTRYPOINT is `openssl`, so we override command with /bin/sh -c.
          image: {{ $p.probeImage | quote }}
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          command: ["/bin/sh", "-c"]
          args:
            - |
              set -u

              # The cert env is base64 of the decoded PEM. Decode it and pull the
              # leaf cert's DNS SANs into a newline list. openssl x509 reads only
              # the first PEM block, so a full chain still yields the leaf's SANs.
              CERT_PEM=$(printf '%s' "${CERT_B64:-}" | base64 -d 2>/dev/null)
              SANS=$(printf '%s' "$CERT_PEM" | openssl x509 -noout -ext subjectAltName 2>/dev/null \
                | tr ',' '\n' | sed -n 's/.*DNS:\([^,[:space:]]*\).*/\1/p')

              # Exact literal SAN present (used for the "*.{base}" requirement).
              has_literal() { printf '%s\n' "$SANS" | grep -qxF "$1"; }

              # Does any SAN cover $1? Exact match, or a "*.parent" wildcard
              # matching exactly one left-most label (RFC 6125: *.example.com
              # matches a.example.com, not a.b.example.com and not example.com).
              covers() {
                _host="$1"
                printf '%s\n' "$SANS" | while IFS= read -r _s; do
                  [ -z "$_s" ] && continue
                  if [ "$_s" = "$_host" ]; then echo M; break; fi
                  case "$_s" in
                    \*.*)
                      _suf=${_s#\*}
                      _rest=${_host%"$_suf"}
                      if [ "$_rest" != "$_host" ] && [ -n "$_rest" ]; then
                        case "$_rest" in
                          *.*) ;;
                          *) echo M; break ;;
                        esac
                      fi
                      ;;
                  esac
                done | grep -q M
              }

              # Resolve $1. busybox nslookup exit codes are unreliable, so parse
              # stdout: answers follow the "Name:" line (the first Address line
              # is the resolver itself).
              dns_ok() {
                printf '%s\n' "$(nslookup "$1" 2>/dev/null)" | sed -n '/^Name:/,$p' | grep -qE '^Address'
              }

              check_san() { if covers "$2"; then echo "SAN_$1=OK"; else echo "SAN_$1=FAIL"; fi; }
              check_dns() { if dns_ok "$2"; then echo "DNS_$1=OK"; else echo "DNS_$1=FAIL"; fi; }

              check_san APP   "$H_APP";   check_dns APP   "$H_APP"
              check_san AUTH  "$H_AUTH";  check_dns AUTH  "$H_AUTH"
              check_san LLM   "$H_LLM";   check_dns LLM   "$H_LLM"
              check_san RTAPI "$H_RTAPI"; check_dns RTAPI "$H_RTAPI"

              # Runtime base: path routing serves {base}/{id}, needing the exact
              # "{base}" SAN and "{base}" to resolve. Subdomain routing (default)
              # serves {id}.{base}, needing a literal "*.{base}" SAN and a
              # wildcard DNS record (probed via a synthetic subdomain).
              if [ "$ROUTING" = "path" ]; then
                check_san RTBASE "$H_RTBASE"
                check_dns RTBASE "$H_RTBASE"
              else
                if has_literal "*.$H_RTBASE"; then echo "SAN_RTBASE=OK"; else echo "SAN_RTBASE=FAIL"; fi
                check_dns RTBASE "dns-preflight-probe.$H_RTBASE"
              fi

              # Analytics ingress only exists when analytics is enabled; the
              # matching analyzers are rendered only in that case.
              if [ "$ANALYTICS_ON" = "1" ]; then
                check_san ANALYTICS "$H_ANALYTICS"; check_dns ANALYTICS "$H_ANALYTICS"
              fi
          env:
            - name: CERT_B64
              value: {{ $p.cert | quote }}
            - name: ROUTING
              value: {{ $p.routingMode | quote }}
            - name: ANALYTICS_ON
              value: {{ if $p.analyticsEnabled }}"1"{{ else }}"0"{{ end }}
            - name: H_APP
              value: {{ $p.appHost | quote }}
            - name: H_AUTH
              value: {{ $p.authHost | quote }}
            - name: H_LLM
              value: {{ $p.llmHost | quote }}
            - name: H_RTAPI
              value: {{ $p.rtApiHost | quote }}
            - name: H_RTBASE
              value: {{ $p.rtBaseHost | quote }}
            - name: H_ANALYTICS
              value: {{ $p.analyticsHost | quote }}
{{- end -}}

{{- define "troubleshoot.analyzers.tlsHostname" -}}
{{- $p := include "troubleshoot.tlsHostname.vars" . | fromYaml -}}
# The runPod log is keyed by <collectorName>/<podName>.log (pod name == collector
# name). All outcomes are pass/warn — non-blocking.
# --- Application ---
- textAnalyze:
    checkName: "TLS certificate covers the application hostname"
    fileName: tls-hostname-check/tls-hostname-check.log
    regex: '(?m)^SAN_APP=OK$'
    outcomes:
      - pass:
          when: "true"
          message: "The application hostname is included in the certificate SANs."
      - warn:
          when: "false"
          message: 'The TLS certificate has no SAN covering {{ $p.appHost }}. HTTPS to the OpenHands app will fail until the certificate includes this name (or a matching wildcard).'
- textAnalyze:
    checkName: "Application hostname resolves in DNS"
    fileName: tls-hostname-check/tls-hostname-check.log
    regex: '(?m)^DNS_APP=OK$'
    outcomes:
      - pass:
          when: "true"
          message: "The application hostname resolves."
      - warn:
          when: "false"
          message: '{{ $p.appHost }} did not resolve from inside the cluster. Create a DNS record pointing it at the ingress before users connect.'
# --- Authentication (Keycloak) ---
- textAnalyze:
    checkName: "TLS certificate covers the authentication hostname"
    fileName: tls-hostname-check/tls-hostname-check.log
    regex: '(?m)^SAN_AUTH=OK$'
    outcomes:
      - pass:
          when: "true"
          message: "The authentication hostname is included in the certificate SANs."
      - warn:
          when: "false"
          message: 'The TLS certificate has no SAN covering {{ $p.authHost }}. Keycloak sign-in will fail until the certificate includes this name (or a matching wildcard).'
- textAnalyze:
    checkName: "Authentication hostname resolves in DNS"
    fileName: tls-hostname-check/tls-hostname-check.log
    regex: '(?m)^DNS_AUTH=OK$'
    outcomes:
      - pass:
          when: "true"
          message: "The authentication hostname resolves."
      - warn:
          when: "false"
          message: '{{ $p.authHost }} did not resolve from inside the cluster. Create a DNS record pointing it at the ingress before users connect.'
# --- LiteLLM proxy ---
- textAnalyze:
    checkName: "TLS certificate covers the LLM proxy hostname"
    fileName: tls-hostname-check/tls-hostname-check.log
    regex: '(?m)^SAN_LLM=OK$'
    outcomes:
      - pass:
          when: "true"
          message: "The LLM proxy hostname is included in the certificate SANs."
      - warn:
          when: "false"
          message: 'The TLS certificate has no SAN covering {{ $p.llmHost }}. The LiteLLM proxy ingress will fail TLS until the certificate includes this name (or a matching wildcard).'
- textAnalyze:
    checkName: "LLM proxy hostname resolves in DNS"
    fileName: tls-hostname-check/tls-hostname-check.log
    regex: '(?m)^DNS_LLM=OK$'
    outcomes:
      - pass:
          when: "true"
          message: "The LLM proxy hostname resolves."
      - warn:
          when: "false"
          message: '{{ $p.llmHost }} did not resolve from inside the cluster. Create a DNS record pointing it at the ingress.'
# --- Runtime API ---
- textAnalyze:
    checkName: "TLS certificate covers the runtime API hostname"
    fileName: tls-hostname-check/tls-hostname-check.log
    regex: '(?m)^SAN_RTAPI=OK$'
    outcomes:
      - pass:
          when: "true"
          message: "The runtime API hostname is included in the certificate SANs."
      - warn:
          when: "false"
          message: 'The TLS certificate has no SAN covering {{ $p.rtApiHost }}. The runtime API ingress will fail TLS until the certificate includes this name (or a matching wildcard).'
- textAnalyze:
    checkName: "Runtime API hostname resolves in DNS"
    fileName: tls-hostname-check/tls-hostname-check.log
    regex: '(?m)^DNS_RTAPI=OK$'
    outcomes:
      - pass:
          when: "true"
          message: "The runtime API hostname resolves."
      - warn:
          when: "false"
          message: '{{ $p.rtApiHost }} did not resolve from inside the cluster. Create a DNS record pointing it at the ingress.'
# --- Sandbox runtime domain (wildcard for subdomain routing, exact for path) ---
- textAnalyze:
    checkName: "TLS certificate covers the sandbox runtime domain"
    fileName: tls-hostname-check/tls-hostname-check.log
    regex: '(?m)^SAN_RTBASE=OK$'
    outcomes:
      - pass:
          when: "true"
          message: "The sandbox runtime domain is covered by the certificate."
      - warn:
          when: "false"
          {{- if eq $p.routingMode "path" }}
          message: 'The TLS certificate is missing the SAN required for sandbox runtimes: an exact "{{ $p.rtBaseHost }}" entry — path routing serves sandboxes under {{ $p.rtBaseHost }}/{id}. Sandboxes will fail to start until the certificate includes it.'
          {{- else }}
          message: 'The TLS certificate is missing the SAN required for sandbox runtimes: a wildcard "*.{{ $p.rtBaseHost }}" — subdomain routing serves each sandbox at {id}.{{ $p.rtBaseHost }}. Sandboxes will fail to start until the certificate includes it.'
          {{- end }}
- textAnalyze:
    checkName: "Sandbox runtime domain resolves in DNS"
    fileName: tls-hostname-check/tls-hostname-check.log
    regex: '(?m)^DNS_RTBASE=OK$'
    outcomes:
      - pass:
          when: "true"
          message: "The sandbox runtime domain resolves."
      - warn:
          when: "false"
          {{- if eq $p.routingMode "path" }}
          message: '{{ $p.rtBaseHost }} did not resolve from inside the cluster. Create a DNS record pointing it at the ingress.'
          {{- else }}
          message: 'A wildcard DNS record "*.{{ $p.rtBaseHost }}" did not resolve (tested via a synthetic subdomain). Create a wildcard A/AAAA record so each {id}.{{ $p.rtBaseHost }} sandbox resolves.'
          {{- end }}
{{- if $p.analyticsEnabled }}
# --- Analytics (Laminar) — only present/checked when analytics is enabled ---
- textAnalyze:
    checkName: "TLS certificate covers the analytics hostname"
    fileName: tls-hostname-check/tls-hostname-check.log
    regex: '(?m)^SAN_ANALYTICS=OK$'
    outcomes:
      - pass:
          when: "true"
          message: "The analytics hostname is included in the certificate SANs."
      - warn:
          when: "false"
          message: 'The TLS certificate has no SAN covering {{ $p.analyticsHost }}. The analytics ingress will fail TLS until the certificate includes this name (or a matching wildcard).'
- textAnalyze:
    checkName: "Analytics hostname resolves in DNS"
    fileName: tls-hostname-check/tls-hostname-check.log
    regex: '(?m)^DNS_ANALYTICS=OK$'
    outcomes:
      - pass:
          when: "true"
          message: "The analytics hostname resolves."
      - warn:
          when: "false"
          message: '{{ $p.analyticsHost }} did not resolve from inside the cluster. Create a DNS record pointing it at the ingress.'
{{- end }}
{{- end -}}
