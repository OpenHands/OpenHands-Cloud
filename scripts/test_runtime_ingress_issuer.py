"""Replicated runtime ingresses use the configured wildcard certificate."""

import subprocess
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts/openhands/charts/runtime-api"


def render(values: dict) -> list[dict]:
    result = subprocess.run(
        ["helm", "template", "runtime", str(CHART), "--values", "-"],
        input=yaml.safe_dump(values), check=True, capture_output=True, text=True,
    )
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def ingress_base(docs: list[dict]) -> dict:
    config = next(doc for doc in docs if doc["metadata"]["name"].endswith("-ingress-base"))
    return yaml.safe_load(config["data"]["base-ingress.yaml"])


def test_shared_chart_keeps_default_issuer() -> None:
    ingress = ingress_base(render({"ingressBase": {"enabled": True}}))
    assert ingress["metadata"]["annotations"]["cert-manager.io/cluster-issuer"] == "letsencrypt-prod"


def test_replicated_runtime_ingress_does_not_request_acme_certificate() -> None:
    replicated = yaml.safe_load((ROOT / "replicated/openhands.yaml").read_text())
    values = replicated["spec"]["values"]["runtime-api"]["ingressBase"]
    ingress = ingress_base(render({"ingressBase": values}))
    assert "cert-manager.io/cluster-issuer" not in ingress["metadata"]["annotations"]
    assert ingress["metadata"]["annotations"]["kubernetes.io/ingress.class"] == "traefik"


def test_path_routing_still_disables_ingress_base() -> None:
    replicated = yaml.safe_load((ROOT / "replicated/openhands.yaml").read_text())
    option = next(
        item for item in replicated["spec"]["optionalValues"]
        if 'ConfigOptionEquals "runtime_routing_mode" "path"' in item["when"]
    )
    docs = render({"ingressBase": option["values"]["runtime-api"]["ingressBase"]})
    assert not any(doc["metadata"]["name"].endswith("-ingress-base") for doc in docs)
