# End-to-end access on a KinD Helm install

## base_url routing gotcha (do this before any conversation test)
Managed `openhands/…` models resolve their proxy from
`OPENHANDS_PROVIDER_BASE_URL`. If it is **unset**, the managed key is sent to the
**public** cloud proxy (`llm-proxy.app.all-hands.dev`), which never holds this
deployment's key → every call fails `token_not_found` (deterministic, unrelated to
any rotation race). Point it at the in-cluster LiteLLM:
```bash
kubectl --context "$KCTX" set env deploy/openhands -n <ns> \
  OPENHANDS_PROVIDER_BASE_URL=http://openhands-litellm.<ns>.svc.cluster.local:4000
```
Confirm via `GET /api/v1/users/me` that the resolved `llm_base_url` is the
in-cluster URL. (This env is a live override, not persisted in Helm values — it
reverts on a chart reinstall.)

## Mint an API key for the test user
API keys are stored **plaintext**, so a key can be minted legitimately in the app
pod and used as a Bearer token:
```python
# /tmp/mkkey.py
import asyncio
from storage.api_key_store import ApiKeyStore
async def main():
    print('APIKEY=' + await ApiKeyStore().create_api_key('<USER_ID>', name='e2e-test'))
asyncio.run(main())
```
```bash
kubectl --context "$KCTX" cp /tmp/mkkey.py <ns>/<app-pod>:/tmp/mkkey.py -c openhands
kubectl --context "$KCTX" exec -n <ns> <app-pod> -c openhands -- python /tmp/mkkey.py
```

## Drive a conversation via the V1 API
```bash
kubectl --context "$KCTX" port-forward -n <ns> svc/openhands-service 3000:3000 &
KEY=sk-oh-...
curl -s -X POST http://localhost:3000/api/v1/app-conversations \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"initial_message":{"content":[{"type":"text","text":"Reply with exactly E2E_OK"}]},"title":"e2e"}'
# poll GET /api/v1/app-conversations/start-tasks?ids=<id> until READY -> app_conversation_id
# then GET /api/v1/conversation/<app_conversation_id>/events/search?limit=50
```
Verify the agent `MessageEvent` reply, LiteLLM `chat/completions` 200, and zero
`token_not_found` in the app + litellm logs.

## Browser access
KinD maps host `:80`→traefik NodePort 30080 and `:443`→30443. Hostnames follow
`*.oh.<base-domain>` (e.g. `app.oh.example.com`, `auth.oh.example.com`,
`*-runtime.oh.example.com`).
- Check DNS: `dig +short app.oh.<base-domain>` — a public wildcard `*.oh.<domain>`
  → `127.0.0.1` means no `/etc/hosts` edits are needed.
- traefik holds the TLS cert (e.g. a real Let's Encrypt wildcard for
  `oh.<domain>`), so the browser trusts `https://app.oh.<domain>` with no warning.
- Probe from the host:
  `curl -sk --resolve app.oh.<domain>:443:127.0.0.1 -o /dev/null -w '%{http_code}\n' https://app.oh.<domain>/`.
Then log in via Keycloak (`auth.oh.<domain>`) as the test user and start a
conversation.
