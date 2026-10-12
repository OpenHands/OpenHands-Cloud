#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest", "PyYAML", "ruamel.yaml"]
# ///
"""Behavior checks for the PR image-scan comment."""

import subprocess
from pathlib import Path

import pytest
import yaml
from ruamel.yaml import YAML

import image_scan_diff as diff
from image_scan_diff import Finding, ImageChange

WORKFLOW = Path(".github/workflows/image-scan-comment.yml")


def finding(id, package="libfoo", severity="HIGH", installed="1.0", fixed="1.1"):
    return Finding(id, package, installed, fixed, severity)


def values_files(runtime_api="0.1.0", enterprise="1.0.0", agent_server="2.0.0"):
    return {
        "charts/openhands/charts/runtime-api/values.yaml": {
            "image": {"repository": "ghcr.io/o/runtime-api", "tag": runtime_api}
        },
        "charts/openhands/values.yaml": {
            "image": {"repository": "ghcr.io/o/enterprise-server", "tag": enterprise},
            "global": {
                "agentServerImage": {"repository": "ghcr.io/o/agent-server", "tag": agent_server}
            },
        },
    }


@pytest.mark.parametrize(
    "minimum,expected",
    [
        ("CRITICAL", ["CRITICAL"]),
        ("HIGH", ["HIGH", "CRITICAL"]),
        ("MEDIUM", ["MEDIUM", "HIGH", "CRITICAL"]),
    ],
)
def test_severities_at_or_above_the_minimum(minimum, expected):
    assert diff.severities_at_or_above(minimum) == expected


def test_unknown_minimum_severity_is_rejected():
    with pytest.raises(ValueError):
        diff.severities_at_or_above("SEVERE")


def test_image_refs_read_repository_and_tag_from_the_values_files():
    assert diff.image_refs(values_files()) == {
        "runtime-api": "ghcr.io/o/runtime-api:0.1.0",
        "enterprise-server": "ghcr.io/o/enterprise-server:1.0.0",
        "agent-server": "ghcr.io/o/agent-server:2.0.0",
    }


def test_load_refs_reads_the_values_committed_at_a_revision(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def git(*args):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            check=True, capture_output=True,
        )

    git("init", "-q")
    for agent_server in ("2.0.0", "2.1.0"):
        for file, content in values_files(agent_server=agent_server).items():
            path = tmp_path / file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(yaml.safe_dump(content))
        git("add", "-A")
        git("commit", "-q", "-m", agent_server)
    assert diff.load_refs("HEAD^1")["agent-server"] == "ghcr.io/o/agent-server:2.0.0"
    assert diff.load_refs("HEAD")["agent-server"] == "ghcr.io/o/agent-server:2.1.0"


def test_parse_findings_dedupes_the_same_package_across_targets():
    vuln = {
        "VulnerabilityID": "CVE-1", "PkgName": "libfoo", "InstalledVersion": "1.0",
        "FixedVersion": "1.1", "Severity": "HIGH",
    }
    report = {"Results": [{"Vulnerabilities": [vuln]}, {"Vulnerabilities": [vuln]}, {}]}
    assert diff.parse_findings(report) == [finding("CVE-1")]


def test_only_findings_missing_from_base_are_new():
    base = [finding("CVE-1"), finding("CVE-2", package="libbar")]
    head = [finding("CVE-1"), finding("CVE-3"), finding("CVE-4", severity="CRITICAL")]
    added = diff.new_findings(base, head, ignore_unfixed=True)
    assert [f.id for f in added] == ["CVE-4", "CVE-3"]


def test_unfixed_findings_are_ignored_for_head_but_not_matched_against_base():
    base = [finding("CVE-1", fixed="")]
    head = [finding("CVE-1", fixed="1.1"), finding("CVE-2", fixed="")]
    assert diff.new_findings(base, head, ignore_unfixed=True) == []
    assert [f.id for f in diff.new_findings(base, head, ignore_unfixed=False)] == ["CVE-2"]


def test_an_image_with_no_base_reports_all_its_findings_as_new():
    assert diff.new_findings(None, [finding("CVE-1")], ignore_unfixed=True) == [finding("CVE-1")]


def test_comment_is_a_warning_listing_new_findings_when_there_are_some():
    change = ImageChange(
        "agent-server", "ghcr.io/o/a:1", "ghcr.io/o/a:2",
        [finding("CVE-2026-1", severity="CRITICAL"), finding("CVE-2026-2")],
    )
    body, has_findings = diff.render_comment([change], ["runtime-api"], "HIGH", True)
    assert has_findings
    assert body.startswith(diff.MARKER)
    assert "adds CRITICAL/HIGH vulnerabilities" in body
    assert "1 CRITICAL, 1 HIGH" in body
    assert "[CVE-2026-1](https://avd.aquasec.com/nvd/cve-2026-1)" in body
    assert "`runtime-api`" in body and "IMAGE_SCAN_MIN_SEVERITY" in body


def test_comment_reports_resolution_when_nothing_new_remains():
    change = ImageChange("agent-server", "ghcr.io/o/a:1", "ghcr.io/o/a:2", [])
    body, has_findings = diff.render_comment([change], [], "HIGH", True)
    assert not has_findings
    assert body.startswith(diff.MARKER) and "No new CRITICAL/HIGH" in body


def test_long_finding_lists_are_truncated():
    many = [finding(f"CVE-{i}", package=f"p{i}") for i in range(diff.MAX_ROWS_PER_IMAGE + 5)]
    change = ImageChange("agent-server", None, "ghcr.io/o/a:2", many)
    body, _ = diff.render_comment([change], [], "HIGH", True)
    assert "…and 5 more." in body
    assert len(body) < 60_000


def test_collect_changes_scans_only_images_whose_ref_changed(monkeypatch):
    scanned = []
    monkeypatch.setattr(diff, "wait_for_images", lambda refs, **_: scanned.append(("wait", refs)))
    monkeypatch.setattr(
        diff, "scan",
        lambda ref, severities, ignorefile: scanned.append(("scan", ref)) or [finding(ref)],
    )
    base = diff.image_refs(values_files())
    head = diff.image_refs(values_files(agent_server="2.1.0"))
    changes, unchanged = diff.collect_changes(base, head, ["HIGH"], ".trivyignore.yaml", True)
    assert unchanged == ["runtime-api", "enterprise-server"]
    assert [c.role for c in changes] == ["agent-server"]
    assert scanned == [
        ("wait", ["ghcr.io/o/agent-server:2.1.0"]),
        ("scan", "ghcr.io/o/agent-server:2.0.0"),
        ("scan", "ghcr.io/o/agent-server:2.1.0"),
    ]
    assert [f.id for f in changes[0].new] == ["ghcr.io/o/agent-server:2.1.0"]


def _workflow():
    return YAML(typ="safe").load(WORKFLOW.read_text())


def test_workflow_is_advisory_and_limited_to_same_repo_prs():
    workflow = _workflow()
    job = workflow["jobs"]["image-scan-comment"]
    assert "head.repo.full_name == github.repository" in job["if"]
    assert job["permissions"] == {"contents": "read", "pull-requests": "write"}
    assert workflow["permissions"] == {"contents": "read"}
    assert "continue-on-error" not in job


def test_workflow_compares_the_merge_result_with_its_base_and_pins_trivy():
    steps = _workflow()["jobs"]["image-scan-comment"]["steps"]
    assert steps[0]["with"]["fetch-depth"] == 2
    run = next(s for s in steps if s.get("id") == "diff")["run"]
    assert "--base-rev HEAD^1" in run and "--head-rev HEAD" in run
    trivy = next(s for s in steps if s.get("uses", "").startswith("aquasecurity/setup-trivy"))
    ref = trivy["uses"].split("@", 1)[1].split()[0]
    assert len(ref) == 40 and set(ref) <= set("0123456789abcdef")
    assert "version" in trivy["with"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
