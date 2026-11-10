#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["ruamel.yaml", "pytest"]
# ///
"""Contract tests for the fusey image release check workflow."""

from pathlib import Path

import pytest
from ruamel.yaml import YAML

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECK = REPO_ROOT / ".github/workflows/check-fusey-image.yml"
SCAN = REPO_ROOT / ".github/workflows/scan-docker-images.yml"


def load(path: Path) -> dict:
    return YAML(typ="safe").load(path.read_text(encoding="utf-8"))


def step(job: dict, name: str) -> dict:
    return next(s for s in job["steps"] if s.get("name", "").startswith(name))


def test_the_release_check_runs_on_release_please_prs_and_manually_only():
    job = load(CHECK)["jobs"]["check-fusey-image"]
    assert "startsWith(github.head_ref, 'release-please--')" in job["if"]
    assert "workflow_dispatch" in job["if"]
    assert "fusey_image.py --wait" in step(job, "Check the image")["run"]


def test_the_release_check_only_reads_and_cannot_publish():
    workflow = load(CHECK)
    assert workflow["permissions"] == {"contents": "read"}
    assert "pull_request_target" not in workflow["on"]


def test_the_scan_includes_the_fusey_image_only_on_push():
    discover = step(load(SCAN)["jobs"]["discover_images"], "Extract image references")["run"]
    assert "agent-server-fusey" in discover
    assert 'if $event == "push" then [$fusey]' in discover


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
