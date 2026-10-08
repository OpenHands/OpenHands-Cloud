#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest"]
# ///
"""Behavior checks for the Copa image patching script."""

import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "copa_patch_images", Path(__file__).with_name("copa_patch_images.py")
)
copa = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(copa)


@pytest.mark.parametrize("ref, expected", [
    ("docker.io/bitnamilegacy/keycloak:26.3.0-debian-12-r0", ("docker.io/bitnamilegacy/keycloak", "26.3.0-debian-12-r0")),
    ("ghcr.io/openhands/ohe-minio:RELEASE.2023-05-18T00-05-36Z-amd64@sha256:abc",
     ("ghcr.io/openhands/ohe-minio", "RELEASE.2023-05-18T00-05-36Z-amd64")),
    ("localhost:5000/app", ("localhost:5000/app", "latest")),
])
def test_split_ref(ref, expected):
    assert copa.split_ref(ref) == expected


def test_platforms_from_index_skips_attestation_manifests():
    raw = {"manifests": [
        {"platform": {"os": "linux", "architecture": "amd64"}},
        {"platform": {"os": "linux", "architecture": "arm64", "variant": "v8"}},
        {"platform": {"os": "unknown", "architecture": "unknown"}},
    ]}
    assert copa.platforms_from_index(raw) == ["linux/amd64", "linux/arm64/v8"]


def test_single_platform_manifest_has_no_index_platforms():
    assert copa.platforms_from_index({"config": {}, "layers": []}) == []


def test_copa_report_drops_results_copa_cannot_patch():
    report = {"Metadata": {"OS": "x"}, "Results": [
        {"Class": "os-pkgs", "Type": "debian"},
        {"Class": "lang-pkgs", "Type": "python-pkg"},
        {"Class": "lang-pkgs", "Type": "node-pkg"},
        {"Class": "lang-pkgs", "Type": "gobinary"},
        {"Class": "lang-pkgs", "Type": "jar"},
    ]}
    kept = copa.copa_report(report)
    assert [r["Type"] for r in kept["Results"]] == ["debian", "python-pkg", "node-pkg"]
    assert kept["Metadata"] == {"OS": "x"}


def test_counts_only_critical_and_high():
    report = {"Results": [{"Vulnerabilities": [
        {"Severity": "CRITICAL"}, {"Severity": "HIGH"}, {"Severity": "HIGH"}, {"Severity": "MEDIUM"},
    ]}, {"Vulnerabilities": None}]}
    assert copa.counts(report) == {"critical": 1, "high": 2}


def test_existing_patched_tag_is_skipped_without_scanning(tmp_path, monkeypatch):
    monkeypatch.setattr(copa, "remote_digest", lambda ref: "sha256:" + "c" * 64)
    monkeypatch.setattr(copa, "run", lambda *a, **k: pytest.fail("must not scan or patch"))

    result = copa.patch({"source": "docker.io/x/db:2", "target": "ghcr.io/o/patched/db"}, tmp_path, push=True, builder="b")

    assert result["status"] == "existing"
    assert result["image"] == "ghcr.io/o/patched/db:2-patched"
    assert "already patched" in copa.summary([result])


def test_force_ignores_an_existing_patched_tag(tmp_path, monkeypatch):
    checked = []
    monkeypatch.setattr(copa, "remote_digest", lambda ref: checked.append(ref) or "sha256:" + "c" * 64)
    monkeypatch.setattr(copa, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("scan started")))

    with pytest.raises(RuntimeError, match="scan started"):
        copa.patch({"source": "docker.io/x/db:2", "target": "ghcr.io/o/patched/db"}, tmp_path, push=True,
                   builder="b", force=True)
    assert "ghcr.io/o/patched/db:2-patched" not in checked


def test_merge_writes_lock_and_flags_missing_results(tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "patches.json"
    manifest.write_text(json.dumps({"images": [
        {"source": "a:1", "target": "ghcr.io/o/a"},
        {"source": "b:2", "target": "ghcr.io/o/b"},
    ]}))
    results = tmp_path / "results" / "result-0"
    results.mkdir(parents=True)
    (results / "result.json").write_text(json.dumps({
        "source": "a:1", "image": "ghcr.io/o/a:1-patched", "platforms": ["linux/amd64"],
        "before": {"linux/amd64": {"critical": 5, "high": 9}},
        "after": {"linux/amd64": {"critical": 1, "high": 4}},
        "copa": ["Patch Summary: 10 total, 10 patched, 0 skipped"], "digest": "sha256:" + "a" * 64,
    }))
    monkeypatch.setattr("sys.argv", ["copa_patch_images.py", "merge", str(manifest), str(tmp_path / "results")])

    assert copa.main() == 1
    lock = json.loads((tmp_path / "patches.lock.json").read_text())["images"]
    assert lock[0]["digest"].startswith("sha256:")
    assert lock[1]["error"] == "no result"
    assert "| `ghcr.io/o/a:1-patched` | linux/amd64 | 5 → 1 | 9 → 4 |" in capsys.readouterr().out


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
