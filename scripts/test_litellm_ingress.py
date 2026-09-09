import subprocess
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
OPENHANDS_CHART = REPO_ROOT / "charts" / "openhands"
REPLICATED_MANIFEST = REPO_ROOT / "replicated" / "openhands.yaml"


def render_litellm_ingress(*set_args: str, values: dict | None = None) -> dict:
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
    if values is not None:
        command.extend(["--values", "-"])

    result = subprocess.run(
        command,
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        input=yaml.safe_dump(values) if values is not None else None,
    )
    return yaml.safe_load(result.stdout)


def test_litellm_ingress_keeps_chart_default_issuer() -> None:
    ingress = render_litellm_ingress()

    assert (
        ingress["metadata"]["annotations"]["cert-manager.io/cluster-issuer"]
        == "letsencrypt-production"
    )


def test_replicated_removes_litellm_issuer_annotation() -> None:
    replicated = yaml.safe_load(REPLICATED_MANIFEST.read_text())
    annotations = replicated["spec"]["values"]["litellm-helm"]["ingress"][
        "annotations"
    ]

    assert annotations is None

    ingress = render_litellm_ingress(
        values={"litellm-helm": {"ingress": {"annotations": annotations}}}
    )

    assert "annotations" not in ingress["metadata"]


def test_litellm_ingress_preserves_an_explicit_issuer() -> None:
    ingress = render_litellm_ingress(
        r"litellm-helm.ingress.annotations.cert-manager\.io/cluster-issuer=customer-issuer"
    )

    assert (
        ingress["metadata"]["annotations"]["cert-manager.io/cluster-issuer"]
        == "customer-issuer"
    )
