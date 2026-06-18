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
chart so the parent chart provides the `Ingress`, hostname, and TLS
secret for the frontend (see
`templates/ingress-agent-canvas.yaml`). The `host` and `ingress.enabled`
flags on this chart are placeholders for stand-alone use.

## Usage as a subchart

```yaml
agent-canvas:
  enabled: true
  image:
    tag: sha-2ad6f84
  ingress:
    enabled: true
    host: canvas.openhands.dev
    tls:
      enabled: true
      secretName: agent-canvas-tls
```
