"""Static registry credentials must not overwrite an externally rotated secret."""

import base64
import json
from pathlib import Path
import subprocess
import tempfile

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def render(config):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml") as values:
        yaml.safe_dump({"config": config}, values)
        values.flush()
        return subprocess.run(
            [
                "helm",
                "template",
                "secrets",
                str(ROOT / "charts/openhands-secrets"),
                "--namespace",
                "openhands",
                "-f",
                values.name,
            ],
            capture_output=True,
            text=True,
        )


def test_static_credentials_still_render():
    result = render(
        {
            "custom_sandbox_image_registry_server": "registry.example.test",
            "custom_sandbox_image_registry_username": "AWS",
            "custom_sandbox_image_registry_password": "test-token",
        }
    )
    assert result.returncode == 0, result.stderr
    secret = next(
        doc
        for doc in yaml.safe_load_all(result.stdout)
        if doc and doc.get("metadata", {}).get("name") == "sandbox-image-pull-secret"
    )
    dockerconfig = json.loads(base64.b64decode(secret["data"][".dockerconfigjson"]))
    assert dockerconfig["auths"]["registry.example.test"]["password"] == "test-token"


@pytest.mark.parametrize("external", ["", "rotated-ecr-credentials"])
def test_no_credentials_creates_no_sandbox_secret(external):
    result = render({"custom_sandbox_image_pull_secret_name": external})
    assert result.returncode == 0, result.stderr
    assert "name: sandbox-image-pull-secret" not in result.stdout
    assert "name: rotated-ecr-credentials" not in result.stdout


def test_external_secret_wins_over_stale_static_credentials():
    result = render(
        {
            "custom_sandbox_image_pull_secret_name": "rotated-ecr-credentials",
            "custom_sandbox_image_registry_server": "registry.example.test",
            "custom_sandbox_image_registry_username": "AWS",
            "custom_sandbox_image_registry_password": "expired-token",
        }
    )
    assert result.returncode == 0, result.stderr
    assert "name: sandbox-image-pull-secret" not in result.stdout
    assert "name: rotated-ecr-credentials" not in result.stdout


def test_refuses_adopting_the_chart_managed_name():
    result = render(
        {"custom_sandbox_image_pull_secret_name": "sandbox-image-pull-secret"}
    )
    assert result.returncode != 0
    assert "must use a distinct name" in result.stderr


@pytest.mark.parametrize(
    ("enabled", "external", "server", "expected"),
    [
        ("0", "rotated-ecr-credentials", "registry.example.test", "kots-pull-secret"),
        (
            "1",
            "rotated-ecr-credentials",
            "registry.example.test",
            "rotated-ecr-credentials",
        ),
        ("1", "", "registry.example.test", "sandbox-image-pull-secret"),
        ("1", "", "", "kots-pull-secret"),
    ],
)
def test_replicated_runtime_selects_the_intended_secret(
    enabled, external, server, expected
):
    from test_replicated_llm_provider_routes import _helm_render

    spec = yaml.safe_load((ROOT / "replicated/openhands.yaml").read_text())["spec"]
    template = spec["values"]["runtime-api"]["env"]["RUNTIME_IMAGE_PULL_SECRETS"]
    template = template.replace("ImagePullSecretName", '"kots-pull-secret"')
    cfg = {
        "custom_sandbox_image_enabled": enabled,
        "custom_sandbox_image_pull_secret_name": external,
        "custom_sandbox_image_registry_server": server,
        "custom_sandbox_image_registry_username": "AWS",
        "custom_sandbox_image_registry_password": "test-token",
    }
    assert _helm_render({"selected": template}, cfg)["selected"] == expected
