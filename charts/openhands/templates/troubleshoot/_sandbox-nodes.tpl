{{/*
Dedicated sandbox nodes preflight: when sandboxes are confined to nodes carrying
the `sandbox` role label, verify at least one such node is actually in the
cluster. With none, every sandbox pod has an unsatisfiable node selector and sits
Pending, so conversations never start.

Warn rather than fail: the operator may be enabling this in the same config pass
that precedes joining the node, and a hard gate would block that ordering.

Read from the default clusterResources collector's cluster-resources/nodes.json
via textAnalyze (no pod, no hostPath -> Pod Security Standards safe). textAnalyze
matches if ANY node carries the label, which is exactly the question here.

Gated in preflights.yaml on the runtime-api's RUNTIME_NODE_SELECTOR targeting the
sandbox label, which replicated/openhands.yaml sets from the config option.
*/}}

{{- define "troubleshoot.sandboxNodes.vars" -}}
{{- $rtApiEnv := (index .Values "runtime-api" | default dict).env | default dict -}}
selected: {{ contains "openhands.dev/sandbox" ($rtApiEnv.RUNTIME_NODE_SELECTOR | default "" | toString) }}
{{- end -}}

{{- define "troubleshoot.analyzers.sandboxNodes" -}}
- textAnalyze:
    checkName: "Sandbox nodes: at least one node carries the sandbox role"
    fileName: cluster-resources/nodes.json
    regex: '"openhands\.dev/sandbox":\s*"true"'
    outcomes:
      - pass:
          when: "true"
          message: "At least one node is joined with the sandbox role."
      - warn:
          when: "false"
          message: "No node carries the sandbox role, so sandboxes have nowhere to schedule. Add a node from Cluster Management and select the sandbox role, or turn off \"Run sandboxes on dedicated nodes\"."
{{- end -}}
