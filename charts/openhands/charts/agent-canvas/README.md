# agent-canvas

Frontend-only deployment of [OpenHands Agent
Canvas](https://github.com/OpenHands/agent-canvas).

This chart runs the pre-built static frontend bundled in the
`ghcr.io/openhands/agent-canvas` image, served by
`scripts/static-server.mjs` in `--auth-required` mode. All backend paths
(`/api`, `/sockets`, etc.) are rejected with `503` so the UI is forced
into the in-app "Manage Backends" workflow — users supply their own
agent-server URL and API key from the browser.

It is intended to be deployed as a subchart of the umbrella `openhands`
chart so the parent chart mounts the frontend under `/canvas` on the host
configured in `agent-canvas.ingress.host` (see
`templates/ingress-agent-canvas.yaml`).

## Usage as a subchart

```yaml
agent-canvas:
  enabled: true
  image:
    tag: sha-2ad6f84
  ingress:
    enabled: true
    host: app.example.com
    path: /canvas
    tls:
      enabled: true
      secretName: app-example-com-tls
  staticServer:
    basePath: /canvas
```

### Locking the UI to a single OpenHands Cloud host

Set `staticServer.lockToCloud` to pass `--lock-to-cloud <url>` to
`static-server.mjs`. The UI then locks backend setup to a single
OpenHands Cloud host (skipping the "Manage Backends" flow), e.g. for the
hosted `/canvas` deployment:

```yaml
agent-canvas:
  enabled: true
  staticServer:
    lockToCloud: https://app.all-hands.dev
    basePath: /canvas
  ingress:
    enabled: true
    host: app.all-hands.dev
    path: /canvas
    tls:
      enabled: true
      secretName: app-all-hands-dev-tls
```

### Disabling telemetry

`staticServer.disableTelemetry` (default `true`) sets
`AGENT_CANVAS_DISABLE_TELEMETRY=1` on the container. `static-server.mjs`
reads it and injects `window.__AGENT_CANVAS_DO_NOT_TRACK__` so the
pre-built frontend disables all PostHog product telemetry at runtime
(including the anonymous install event). The default is off because this
chart targets self-hosted deployments. The image must include the runtime
opt-out (OpenHands/OpenHands#16908); older images ignore the variable and
keep their built-in default.

A deployment that wants telemetry on (e.g. SaaS) overrides it:

```yaml
agent-canvas:
  staticServer:
    disableTelemetry: false
```
