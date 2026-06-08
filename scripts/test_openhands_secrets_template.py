"""Tests for OpenHands secrets chart rendering."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
OPENHANDS_SECRETS_CHART = REPO_ROOT / "charts" / "openhands-secrets"
OPENHANDS_ENV_SECRETS_TEMPLATE = "templates/openhands-env-secrets.yaml"


def render_openhands_env_secrets(custom_model: str) -> str:
    result = subprocess.run(
        [
            "helm",
            "template",
            "openhands-secrets",
            str(OPENHANDS_SECRETS_CHART),
            "--show-only",
            OPENHANDS_ENV_SECRETS_TEMPLATE,
            "--set",
            "config.llm_provider=custom",
            "--set-string",
            f"config.custom_model={custom_model}",
        ],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return result.stdout


def rendered_llm_model(custom_model: str) -> str:
    manifest = render_openhands_env_secrets(custom_model)
    match = re.search(r"(?m)^\s+LLM_MODEL:\s+'([^']+)'$", manifest)
    assert match, f"Rendered LLM_MODEL not found in manifest:\n{manifest}"
    return match.group(1)


@pytest.mark.parametrize(
    ("custom_model", "expected_llm_model"),
    [
        ("llama-3.1-70b-instruct", "openai/llama-3.1-70b-instruct"),
        ("anthropic/claude-opus-4-7", "anthropic/claude-opus-4-7"),
    ],
)
def test_custom_llm_model_rendering(
    custom_model: str, expected_llm_model: str
) -> None:
    assert rendered_llm_model(custom_model) == expected_llm_model
