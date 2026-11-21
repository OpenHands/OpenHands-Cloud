#!/usr/bin/env bash
# Validate a configured chart without changing its packaged default values.
# Run from the repository root, after fetching chart dependencies.
set -euo pipefail

chart_path=${1:?chart path is required}
chart_name=${2:?chart name is required}
chart_args=("$chart_path")
case "$chart_name" in
  openhands)
    chart_args+=(-f charts/openhands/examples/enterprise-automations.yaml)
    ;;
  openhands-secrets)
    chart_args+=(-f .github/helm/automation-secrets-render-only.yaml)
    ;;
esac

helm lint "${chart_args[@]}"
if grep -q '^type: library' "$chart_path/Chart.yaml"; then
  echo "Skipping helm template for library chart $chart_name"
else
  helm template "${chart_args[@]}" --debug
fi
