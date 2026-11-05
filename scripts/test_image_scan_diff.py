#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["pytest", "PyYAML", "ruamel.yaml"]
# ///
"""Behavior checks for the PR image-scan comment."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from ruamel.yaml import YAML

import image_scan_diff as diff
from image_scan_diff import Finding, ImageChange

WORKFLOW = Path(".github/workflows/image-scan-comment.yml")
SCAN_WORKFLOW = Path(".github/workflows/scan-docker-images.yml")


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


def _git(*args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True, capture_output=True,
    )


def _repo_with_agent_server_bump(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _git("init", "-q")
    for agent_server in ("2.0.0", "2.1.0"):
        for file, content in values_files(agent_server=agent_server).items():
            path = tmp_path / file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(yaml.safe_dump(content))
        _git("add", "-A")
        _git("commit", "-q", "-m", agent_server)


def test_load_refs_reads_the_values_committed_at_a_revision(tmp_path, monkeypatch):
    _repo_with_agent_server_bump(tmp_path, monkeypatch)
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


def test_a_cve_in_another_package_is_new():
    base = [finding("CVE-1", package="libfoo")]
    head = [finding("CVE-1", package="libbar")]
    assert diff.new_findings(base, head, ignore_unfixed=True) == head


def test_a_version_bump_of_an_already_vulnerable_package_is_not_new():
    base = [finding("CVE-1", installed="1.0")]
    head = [finding("CVE-1", installed="1.0.1")]
    assert diff.new_findings(base, head, ignore_unfixed=True) == []


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


def test_comment_fits_githubs_body_limit_when_every_image_is_truncated():
    changes = [
        ImageChange(
            role, "ghcr.io/o/a:1", "ghcr.io/o/a:2",
            [
                finding(f"CVE-2026-{i}", package=f"libpackage-{i}", installed="1.2.3-r0",
                        fixed="1.2.4-r0")
                for i in range(diff.MAX_ROWS_PER_IMAGE + 5)
            ],
        )
        for role in diff.IMAGE_SOURCES
    ]
    body, _ = diff.render_comment(changes, [], "HIGH", True)
    assert len(body) < 65_536


def test_missing_refs_and_fixes_render_as_dashes():
    change = ImageChange("agent-server", None, "ghcr.io/o/a:2", [finding("CVE-1", fixed="")])
    body, _ = diff.render_comment([change], [], "HIGH", False)
    assert "| `agent-server` | `—` | `ghcr.io/o/a:2` |" in body
    assert "| `libfoo` | `1.0` | `—` |" in body


def test_scan_gives_trivy_the_severities_and_the_ignorefile(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    trivy = bin_dir / "trivy"
    trivy.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "args = sys.argv[1:]\n"
        f"json.dump(args, open({str(tmp_path / 'argv.json')!r}, 'w'))\n"
        "report = {'Results': [{'Vulnerabilities': [{'VulnerabilityID': 'CVE-1',\n"
        "    'PkgName': 'libfoo', 'InstalledVersion': '1.0', 'FixedVersion': '1.1',\n"
        "    'Severity': 'HIGH'}]}]}\n"
        "json.dump(report, open(args[args.index('--output') + 1], 'w'))\n"
    )
    trivy.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    found = diff.scan("ghcr.io/o/a:2", ["HIGH", "CRITICAL"], "accepted.yaml")

    argv = json.loads((tmp_path / "argv.json").read_text())
    assert found == [finding("CVE-1")]
    assert argv[argv.index("--severity") + 1] == "HIGH,CRITICAL"
    assert argv[argv.index("--ignorefile") + 1] == "accepted.yaml"
    assert argv[-1] == "ghcr.io/o/a:2"


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


def test_image_sources_match_the_images_scan_docker_images_discovers():
    discover = YAML(typ="safe").load(SCAN_WORKFLOW.read_text())["jobs"]["discover_images"]
    script = next(s for s in discover["steps"] if s.get("id") == "extract")["run"]
    discovered = {
        (file, tuple(query.removesuffix(".tag").strip(".").split(".")))
        for query, file in re.findall(r"=\$\(yq '([^']+)' (\S+)\)", script)
    }
    assert discovered
    assert set(diff.IMAGE_SOURCES.values()) == discovered


def _workflow():
    return YAML(typ="safe").load(WORKFLOW.read_text())


def test_workflow_is_advisory_and_limited_to_same_repo_prs():
    workflow = _workflow()
    job = workflow["jobs"]["image-scan-comment"]
    assert "head.repo.full_name == github.repository" in job["if"]
    assert "github.actor != 'dependabot[bot]'" in job["if"]
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


def _run_main(tmp_path, monkeypatch, head_findings, *extra_args):
    _repo_with_agent_server_bump(tmp_path, monkeypatch)
    github_output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))
    monkeypatch.setattr(diff, "wait_for_images", lambda refs, **_: None)
    monkeypatch.setattr(
        diff, "scan",
        lambda ref, severities, ignorefile: head_findings if ref.endswith(":2.1.0") else [],
    )
    comment = tmp_path / "comment.md"
    argv = ["--base-rev", "HEAD^1", "--head-rev", "HEAD", "--output", str(comment), *extra_args]
    assert diff.main(argv) == 0
    return github_output.read_text(), comment.read_text()


@pytest.mark.parametrize(
    "head_findings,expected",
    [([finding("CVE-9")], "true"), ([], "false")],
)
def test_main_tells_the_workflow_whether_there_are_findings(
    tmp_path, monkeypatch, head_findings, expected
):
    output, comment = _run_main(tmp_path, monkeypatch, head_findings)
    assert output == f"has_findings={expected}\n"
    assert comment.startswith(diff.MARKER)


@pytest.mark.parametrize("extra_args,expected", [([], "false"), (["--include-unfixed"], "true")])
def test_main_ignores_unfixed_findings_unless_asked_not_to(
    tmp_path, monkeypatch, extra_args, expected
):
    output, _ = _run_main(tmp_path, monkeypatch, [finding("CVE-9", fixed="")], *extra_args)
    assert output == f"has_findings={expected}\n"


def _comment_step():
    steps = _workflow()["jobs"]["image-scan-comment"]["steps"]
    return next(s for s in steps if s["name"].startswith("Create, update"))


def test_workflow_hands_the_diff_step_output_to_the_comment_step():
    assert _comment_step()["env"]["HAS_FINDINGS"] == "${{ steps.diff.outputs.has_findings }}"


GH_STUB = """\
import json, os, sys
a = sys.argv[1:]
log = open(os.environ["GH_LOG"], "a")
if a[:3] == ["api", "--paginate", "--slurp"]:
    print(os.environ["GH_COMMENTS"])
elif a[:3] == ["api", "--method", "PATCH"]:
    body = None
    for flag, val in zip(a, a[1:]):
        if flag == "-F" and val.startswith("body=@"):
            body = open(val[len("body=@"):]).read()
        elif flag == "-f" and val.startswith("body="):
            body = val[len("body="):]
    log.write(json.dumps({"call": "patch", "url": a[3], "body": body}) + "\\n")
elif a[:2] == ["pr", "comment"]:
    body = open(a[a.index("--body-file") + 1]).read()
    log.write(json.dumps({"call": "create", "pr": a[2], "body": body}) + "\\n")
else:
    sys.exit(f"unexpected gh call: {a}")
"""


@pytest.mark.parametrize(
    "existing,has_findings,expected",
    [
        (True, "true", ["patch"]),
        (True, "false", ["patch"]),
        (False, "true", ["create"]),
        (False, "false", []),
    ],
)
def test_comment_step_creates_updates_or_skips(tmp_path, existing, has_findings, expected):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!{sys.executable}\n{GH_STUB}")
    gh.chmod(0o755)
    body = f"{diff.MARKER}\nbody\n"
    (tmp_path / "comment.md").write_text(body)
    log = tmp_path / "gh.log"
    comments = [{"id": 11, "body": "unrelated"}]
    if existing:
        comments.append({"id": 22, "body": f"{diff.MARKER}\nold"})
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "REPO": "o/r",
        "PR_NUMBER": "7",
        "HAS_FINDINGS": has_findings,
        "GH_LOG": str(log),
        "GH_COMMENTS": json.dumps([comments]),
    }
    subprocess.run(
        ["bash", "-e", "-c", _comment_step()["run"]],
        check=True, env=env, cwd=tmp_path, capture_output=True,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    assert [c["call"] for c in calls] == expected
    for call in calls:
        assert call["body"] == body
    if existing:
        assert calls[0]["url"] == "repos/o/r/issues/comments/22"
    elif has_findings == "true":
        assert calls[0]["pr"] == "7"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
