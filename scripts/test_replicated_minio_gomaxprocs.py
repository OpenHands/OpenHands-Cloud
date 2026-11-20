"""Replicated sets only MinIO's CPU limit; the chart derives GOMAXPROCS from it."""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def _spec() -> dict:
    path = REPO_ROOT / "replicated/openhands.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))["spec"]


def test_replicated_sets_a_minio_cpu_limit() -> None:
    assert _spec()["values"]["minio"]["resources"]["limits"]["cpu"]


def test_replicated_does_not_pin_gomaxprocs() -> None:
    spec = _spec()
    blocks = [spec["values"]] + [o["values"] for o in spec["optionalValues"]]

    for values in blocks:
        environment = values.get("minio", {}).get("environment") or {}
        assert "GOMAXPROCS" not in environment

