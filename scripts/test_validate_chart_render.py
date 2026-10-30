"""Configured CI rendering must not change the chart a customer installs."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/validate-chart-render.sh"


@pytest.mark.parametrize(
    ("name", "fixture"),
    [
        ("openhands", "charts/openhands/examples/enterprise-automations.yaml"),
        ("openhands-secrets", ".github/helm/automation-secrets-render-only.yaml"),
        ("image-loader", None),
    ],
)
def test_passes_config_only_to_validation_commands(tmp_path, name, fixture):
    chart = tmp_path / "chart"
    chart.mkdir()
    (chart / "Chart.yaml").write_text("apiVersion: v2\nname: fixture\nversion: 0.1.0\n")
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    helm = fakebin / "helm"
    helm.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$HELM_RENDER_TEST_LOG"\n')
    helm.chmod(0o755)
    log = tmp_path / "calls"
    env = dict(os.environ, PATH=str(fakebin) + os.pathsep + os.environ["PATH"],
               HELM_RENDER_TEST_LOG=str(log))
    subprocess.run(["bash", str(SCRIPT), str(chart), name], cwd=ROOT,
                   env=env, check=True, capture_output=True, text=True)
    calls = log.read_text().splitlines()
    assert len(calls) == 2
    assert calls[0].startswith("lint ") and calls[1].startswith("template ")
    assert all(("-f " + fixture in c) if fixture else "-f " not in c for c in calls)
    assert not any("package" in c for c in calls)


def test_library_chart_is_linted_but_not_templated(tmp_path):
    chart = tmp_path / "library"
    chart.mkdir()
    (chart / "Chart.yaml").write_text("type: library\n")
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    helm = fakebin / "helm"
    helm.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$HELM_RENDER_TEST_LOG"\n')
    helm.chmod(0o755)
    log = tmp_path / "calls"
    subprocess.run(["bash", str(SCRIPT), str(chart), "library"], cwd=ROOT,
                   env=dict(os.environ, PATH=str(fakebin) + os.pathsep + os.environ["PATH"],
                            HELM_RENDER_TEST_LOG=str(log)), check=True, capture_output=True)
    assert log.read_text().splitlines() == ["lint " + str(chart)]


def test_real_secrets_render_preserves_unconfigured_packaged_defaults(tmp_path):
    chart = ROOT / "charts/openhands-secrets"
    before = (chart / "values.yaml").read_bytes()
    result = subprocess.run(["bash", str(SCRIPT), str(chart), "openhands-secrets"],
                            cwd=ROOT, check=True, capture_output=True, text=True)
    assert "automation-service-key" in result.stdout
    assert (chart / "values.yaml").read_bytes() == before
    subprocess.run(["helm", "package", str(chart), "-d", str(tmp_path)],
                   cwd=ROOT, check=True, capture_output=True)
    with tarfile.open(next(tmp_path.glob("openhands-secrets-*.tgz"))) as archive:
        assert archive.extractfile("openhands-secrets/values.yaml").read() == before
        assert not any("render-only" in n for n in archive.getnames())
