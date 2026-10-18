# KinD-specific gotchas

## `Init:Error` after an image swap (`Can't locate revision`)
Cause: the `migrate-db` init container is still on the chart-default appVersion
tag while the main container is on a newer/older SHA, and its alembic tree does not
know the DB's current revision. Fix: set **all** enterprise-server containers to
the same image (`openhands`, `migrate-db`, `keycloak-config`, `litellm-config`) —
see SKILL.md step 2. Inspect init failures with:
```bash
kubectl --context "$KCTX" get pod -n <ns> <pod> \
  -o jsonpath='{range .status.initContainerStatuses[*]}{.name}: {.state}{"\n"}{end}'
kubectl --context "$KCTX" logs -n <ns> <pod> -c migrate-db | tail -20
```

## Node memory (single-node cluster)
Each runtime sandbox reserves ~3Gi and the cluster is one node. If new sandboxes
fail to schedule, free memory by scaling down stale runtime deployments:
```bash
kubectl --context "$KCTX" describe node <node> | sed -n '/Allocated resources/,/ephemeral/p' | grep memory
kubectl --context "$KCTX" get deploy -n <ns> | grep '^runtime-'   # scale stale ones to 0
```

## Version skew across app / mcp / integrations
`openhands`, `openhands-mcp`, and `openhands-integrations` all run the
enterprise-server image and share the DB. If only one is bumped to a build with a
newer migration, the others' `migrate-db` can fail. Keep the three aligned when the
DB schema moves.

## Reading effective/decrypted state
SaaS secret columns are encrypted; read the resolved values via an in-pod probe
using the app's stores (`SaasSettingsStore.load(resolve_agent_profile=True)`,
`ApiKeyStore`, `OrgStore`) rather than raw SQL.

## Stale local runtime / tmux
Local runtime startup can fail with `duplicate session: test-session`; clear it:
`tmux -S /tmp/tmux-$(id -u)/default kill-session -t test-session`.

## kubectl heredocs
`kubectl exec ... <<'PY'` frequently mangles multi-line Python. Always write the
script to a `/tmp/*.py` file, `kubectl cp` it into the pod, then
`kubectl exec ... python /tmp/script.py`.
