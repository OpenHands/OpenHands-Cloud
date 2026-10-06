"""The openhands chart refuses Kubernetes < 1.27, where CronJob spec.timeZone is not stable."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
OPENHANDS_CHART = REPO_ROOT / "charts" / "openhands"


def render_at(kube_version: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["helm", "template", "openhands", str(OPENHANDS_CHART), "--kube-version", kube_version],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )


def test_refuses_kubernetes_below_1_27() -> None:
    result = render_at("1.26.9")

    assert result.returncode != 0
    assert "chart requires kubeVersion" in result.stderr


# Vendor builds report pre-release-style versions; without the "-0" in the
# constraint, semver excludes them and GKE would be refused.
@pytest.mark.parametrize("kube_version", ["1.27.0", "1.30.5-gke.1014000", "v1.36.0+k0s"])
def test_renders_on_supported_kubernetes(kube_version: str) -> None:
    result = render_at(kube_version)

    assert result.returncode == 0, result.stderr
