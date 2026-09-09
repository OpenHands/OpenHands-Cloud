import subprocess
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
OPENHANDS_CHART = REPO_ROOT / "charts" / "openhands"


def render_litellm_ingress(*set_args: str) -> dict:
    command = [
        "helm",
        "template",
        "openhands",
        str(OPENHANDS_CHART),
        "--show-only",
        "charts/litellm-helm/templates/ingress.yaml",
        "--set",
        "litellm-helm.enabled=true",
        "--set",
        "litellm-helm.ingress.enabled=true",
        "--set",
        "litellm-helm.ingress.hosts[0].host=llm.example.com",
        "--set",
        "litellm-helm.ingress.hosts[0].paths[0].path=/",
        "--set",
        "litellm-helm.ingress.hosts[0].paths[0].pathType=Prefix",
    ]
    for arg in set_args:
        command.extend(["--set", arg])

    result = subprocess.run(
        command,
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return yaml.safe_load(result.stdout)


def test_litellm_ingress_does_not_request_an_issuer_by_default() -> None:
    ingress = render_litellm_ingress()

    assert "cert-manager.io/cluster-issuer" not in ingress["metadata"].get(
        "annotations", {}
    )


def test_litellm_ingress_preserves_an_explicit_issuer() -> None:
    ingress = render_litellm_ingress(
        r"litellm-helm.ingress.annotations.cert-manager\.io/cluster-issuer=customer-issuer"
    )

    assert (
        ingress["metadata"]["annotations"]["cert-manager.io/cluster-issuer"]
        == "customer-issuer"
    )
