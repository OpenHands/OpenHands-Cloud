{{/*
License preflight: warn when no OpenHands Enterprise license is set, because an
upcoming release will enforce the license through Customer Center. A warning,
never a failure: installs without a license keep working until then.

Whether a license is set is known at render time, so a data collector writes
the answer to a file for textAnalyze to read. The file holds only true/false,
never the license itself.
*/}}

{{- define "troubleshoot.collectors.license" -}}
- data:
    name: openhands/license.txt
    data: "license-set: {{ ternary "true" "false" (not (empty (include "openhands.license.secretName" .))) }}"
{{- end -}}

{{- define "troubleshoot.analyzers.license" -}}
- textAnalyze:
    checkName: "OpenHands Enterprise license"
    fileName: openhands/license.txt
    regex: 'license-set: true'
    outcomes:
      - pass:
          when: "true"
          message: "An OpenHands Enterprise license is set."
      - warn:
          when: "false"
          message: "No OpenHands Enterprise license is set. An upcoming release will enforce the license through Customer Center. Before you upgrade to it, set your license key from Customer Center, or an offline license file if this install can't reach Customer Center."
{{- end -}}
