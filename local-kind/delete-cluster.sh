#!/usr/bin/env bash
# Tear down the local KinD cluster. Removes every listener and every
# in-cluster secret (including LLM API keys). DNS records, certificates,
# the GitHub App, and the Replicated license all survive for the next run.
set -euo pipefail
kind delete cluster --name "${KIND_CLUSTER:-openhands-local-kind}"
