#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest", "PyYAML"]
# ///
"""Behavior checks for the chart image scan report."""

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "scan_chart_images", Path(__file__).with_name("scan_chart_images.py")
)
scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan)


def _finding(image, severity, cve, pkg, src=None, fixed="1.1", ptype="debian"):
    return {"image": image, "severity": severity, "cve": cve, "pkg": pkg, "src": src or pkg,
            "type": ptype, "installed": "1.0", "fixed": fixed, "title": "", "url": "", "target": ""}


def test_find_images_walks_nested_pod_specs():
    doc = {"spec": {"template": {"spec": {
        "initContainers": [{"image": "busybox"}],
        "containers": [{"image": "ghcr.io/a/b:1"}, {"image": {"repository": "not-a-ref"}}],
    }}}}
    assert scan.find_images(doc) == {"busybox", "ghcr.io/a/b:1"}


@pytest.mark.parametrize("ref, expected", [
    ("busybox", "busybox:latest"),
    ("localhost:5000/app", "localhost:5000/app:latest"),
    ("ghcr.io/x/y:1.0@sha256:abc", "ghcr.io/x/y:1.0@sha256:abc"),
    ("ghcr.io/x/y@sha256:abc", "ghcr.io/x/y:latest@sha256:abc"),
])
def test_normalize_defaults_missing_tag_to_latest(ref, expected):
    assert scan.normalize(ref) == expected


def test_image_table_counts_fixable_and_unfixed_separately_and_flags_failures():
    findings = [
        _finding("a:1", "CRITICAL", "CVE-1", "openssl"),
        _finding("a:1", "HIGH", "CVE-2", "openssl", fixed=""),
        _finding("a:1", "HIGH", "CVE-3", "zlib"),
    ]
    lines = scan.image_table(["a:1", "b:2"], findings, {"b:2": "pull denied"})
    assert "| a | 1 | 1 | 2 | 1 / 1 | 0 / 1 |" in lines
    assert any("b | 2 |" in line and "scan failed: pull denied" in line for line in lines)


def test_package_table_groups_binary_packages_by_source_and_skips_unfixed():
    findings = [
        _finding("ghcr.io/o/app:1", "CRITICAL", "CVE-1", "libssl3", src="openssl", fixed="3.0.2"),
        _finding("docker.io/x/db:2", "CRITICAL", "CVE-1", "openssl", src="openssl", fixed="3.0.2, 3.1.1"),
        _finding("ghcr.io/o/app:1", "HIGH", "CVE-9", "zlib", fixed=""),
    ]
    rows = [line for line in scan.package_table(findings) if line.startswith("| `")]
    assert len(rows) == 1
    assert rows[0].startswith("| `openssl` | debian | 1 | 0 |")
    assert "3.0.2, 3.1.1" in rows[0] and "app, db" in rows[0]


def test_flatten_resolves_source_package_from_package_list():
    report = {"Results": [{
        "Type": "debian",
        "Packages": [{"Name": "libssl3", "SrcName": "openssl", "Identifier": {"UID": "u1"}}],
        "Vulnerabilities": [
            {"VulnerabilityID": "CVE-1", "Severity": "HIGH", "PkgName": "libssl3", "PkgIdentifier": {"UID": "u1"}},
            {"VulnerabilityID": "CVE-2", "Severity": "HIGH", "PkgName": "zlib1g", "PkgIdentifier": {"UID": "u2"}},
        ],
    }]}
    assert [f["src"] for f in scan.flatten("a:1", report)] == ["openssl", "zlib1g"]


def test_flatten_carries_drilldown_fields_and_the_layer_command_that_added_the_package():
    report = {
        "Metadata": {
            "DiffIDs": ["sha256:base", "sha256:venv"],
            "ImageConfig": {"history": [
                {"created_by": "FROM debian"},
                {"created_by": "ENV X=1", "empty_layer": True},
                {"created_by": "COPY /app/.venv /app/.venv"},
            ]},
        },
        "Results": [{"Type": "python-pkg", "Target": "Python", "Vulnerabilities": [{
            "VulnerabilityID": "CVE-1", "VendorIDs": ["GHSA-x"], "Severity": "CRITICAL",
            "PkgName": "PyJWT", "PkgIdentifier": {"PURL": "pkg:pypi/pyjwt@2.13.0"},
            "PkgPath": "app/.venv/lib/pyjwt.dist-info/METADATA", "Layer": {"DiffID": "sha256:venv"},
            "InstalledVersion": "2.13.0", "FixedVersion": "2.14.0", "Status": "fixed",
            "SeveritySource": "ghsa", "CVSS": {"nvd": {"V3Score": 7.5}, "ghsa": {"V3Score": 9.1, "V3Vector": "AV:N"}},
            "CweIDs": ["CWE-347"], "References": ["https://example/fix"], "PublishedDate": "2026-09-28T21:17:14Z",
        }]}],
    }
    [f] = scan.flatten("ghcr.io/o/app:1", report)
    assert f["introduced_by"] == "COPY /app/.venv /app/.venv"
    assert f["cvss"] == {"score": 9.1, "vector": "AV:N", "source": "ghsa"}
    assert (f["purl"], f["pkg_path"], f["aliases"], f["cwe"], f["published"]) == (
        "pkg:pypi/pyjwt@2.13.0", "app/.venv/lib/pyjwt.dist-info/METADATA", ["GHSA-x"], ["CWE-347"], "2026-09-28")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
