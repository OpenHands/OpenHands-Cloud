#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["PyYAML"]
# ///
"""Report vulnerabilities a PR adds to the images the charts ship.

For each image whose ref changed between two git revisions, scan the old and new
image with Trivy and write a PR comment body listing findings at or above a minimum
severity that only the new image has. The workflow decides whether to post it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml

from wait_for_image_manifest import wait_for_images

SEVERITIES = ["UNKNOWN", "LOW", "MEDIUM", "HIGH", "CRITICAL"]

# Keep in sync with the discover_images job in scan-docker-images.yml.
IMAGE_SOURCES: dict[str, tuple[str, tuple[str, ...]]] = {
    "runtime-api": ("charts/openhands/charts/runtime-api/values.yaml", ("image",)),
    "enterprise-server": ("charts/openhands/values.yaml", ("image",)),
    "agent-server": ("charts/openhands/values.yaml", ("global", "agentServerImage")),
}

MARKER = "<!-- image-scan-comment -->"
MAX_ROWS_PER_IMAGE = 40
CVE_URL = "https://avd.aquasec.com/nvd/{}"


@dataclass(frozen=True)
class Finding:
    id: str
    package: str
    installed: str
    fixed: str
    severity: str


@dataclass(frozen=True)
class ImageChange:
    role: str
    base_ref: str | None
    head_ref: str
    new: list[Finding]


def severities_at_or_above(minimum: str) -> list[str]:
    if minimum not in SEVERITIES:
        raise ValueError(f"unknown severity {minimum!r}; expected one of {SEVERITIES}")
    return SEVERITIES[SEVERITIES.index(minimum):]


def image_refs(values_by_file: dict[str, dict]) -> dict[str, str]:
    refs = {}
    for role, (file, path) in IMAGE_SOURCES.items():
        node = values_by_file[file]
        for key in path:
            node = node[key]
        refs[role] = f"{node['repository']}:{node['tag']}"
    return refs


def load_refs(rev: str) -> dict[str, str]:
    values = {}
    for file in {file for file, _ in IMAGE_SOURCES.values()}:
        text = subprocess.run(
            ["git", "show", f"{rev}:{file}"], check=True, capture_output=True, text=True
        ).stdout
        values[file] = yaml.safe_load(text)
    return image_refs(values)


def parse_findings(trivy_report: dict) -> list[Finding]:
    findings = {}
    for result in trivy_report.get("Results") or []:
        for vuln in result.get("Vulnerabilities") or []:
            finding = Finding(
                id=vuln["VulnerabilityID"],
                package=vuln["PkgName"],
                installed=vuln.get("InstalledVersion", ""),
                fixed=vuln.get("FixedVersion", ""),
                severity=vuln["Severity"],
            )
            findings[(finding.id, finding.package, finding.installed)] = finding
    return list(findings.values())


def new_findings(
    base: list[Finding] | None, head: list[Finding], *, ignore_unfixed: bool
) -> list[Finding]:
    """Head findings absent from base.

    Base is matched without the unfixed filter: a CVE that was unfixed in base and
    has a fix now was not introduced by this PR."""
    known = {(f.id, f.package) for f in base or []}
    added = [
        f
        for f in head
        if (f.id, f.package) not in known and not (ignore_unfixed and not f.fixed)
    ]
    return sorted(added, key=lambda f: (-SEVERITIES.index(f.severity), f.package, f.id))


def render_comment(
    changes: list[ImageChange], unchanged: list[str], minimum: str, ignore_unfixed: bool
) -> tuple[str, bool]:
    levels = severities_at_or_above(minimum)
    label = "/".join(reversed(levels))
    has_findings = any(change.new for change in changes)
    lines = [MARKER]

    if not has_findings:
        lines.append(f"### ✅ No new {label} image vulnerabilities")
        lines.append("")
        lines.append("This PR no longer adds findings at or above the threshold.")
        return "\n".join(lines) + "\n", False

    lines += [
        f"### ⚠️ This PR adds {label} vulnerabilities to shipped images",
        "",
        "Fix them here, or accept them in `.trivyignore.yaml` (with a statement and an "
        "expiry date).",
        "",
        "| Image | Before | After | New findings |",
        "| --- | --- | --- | --- |",
    ]
    for change in changes:
        counts = ", ".join(
            f"{sum(f.severity == level for f in change.new)} {level}"
            for level in reversed(levels)
            if any(f.severity == level for f in change.new)
        )
        lines.append(
            f"| `{change.role}` | `{change.base_ref or '—'}` | `{change.head_ref}` | "
            f"{counts or 'none'} |"
        )
    for change in changes:
        if not change.new:
            continue
        shown = change.new[:MAX_ROWS_PER_IMAGE]
        lines += [
            "",
            f"<details><summary><code>{change.role}</code>: "
            f"{len(change.new)} new</summary>",
            "",
            "| Severity | CVE | Package | Installed | Fixed in |",
            "| --- | --- | --- | --- | --- |",
        ]
        for f in shown:
            lines.append(
                f"| {f.severity} | [{f.id}]({CVE_URL.format(f.id.lower())}) | "
                f"`{f.package}` | `{f.installed}` | `{f.fixed or '—'}` |"
            )
        if len(change.new) > len(shown):
            lines.append(f"\n…and {len(change.new) - len(shown)} more.")
        lines += ["", "</details>"]

    notes = [f"Minimum severity: **{minimum}**."]
    if ignore_unfixed:
        notes.append("Vulnerabilities with no fix available are ignored.")
    if unchanged:
        notes.append(f"Unchanged, not scanned: {', '.join(f'`{r}`' for r in unchanged)}.")
    lines += [
        "",
        " ".join(notes) + " Set the repository variable `IMAGE_SCAN_MIN_SEVERITY` to change the threshold.",
        "",
        "_Posted automatically by an AI-assisted workflow on behalf of the OpenHands-Cloud maintainers._",
    ]
    return "\n".join(lines) + "\n", True


def scan(image_ref: str, severities: list[str], ignorefile: str) -> list[Finding]:
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.json"
        subprocess.run(
            [
                "trivy", "image", "--quiet", "--scanners", "vuln",
                "--severity", ",".join(severities),
                "--ignorefile", ignorefile,
                "--timeout", "10m",
                "--format", "json", "--output", str(report),
                image_ref,
            ],
            check=True,
        )
        return parse_findings(json.loads(report.read_text()))


def collect_changes(
    base_refs: dict[str, str], head_refs: dict[str, str], severities: list[str],
    ignorefile: str, ignore_unfixed: bool,
) -> tuple[list[ImageChange], list[str]]:
    changed = [role for role in head_refs if head_refs[role] != base_refs.get(role)]
    unchanged = [role for role in head_refs if role not in changed]
    if changed:
        wait_for_images(
            [head_refs[role] for role in changed], timeout_seconds=900, interval_seconds=15
        )
    changes = []
    for role in changed:
        base_ref = base_refs.get(role)
        base = scan(base_ref, severities, ignorefile) if base_ref else None
        head = scan(head_refs[role], severities, ignorefile)
        changes.append(
            ImageChange(role, base_ref, head_refs[role],
                        new_findings(base, head, ignore_unfixed=ignore_unfixed))
        )
    return changes, unchanged


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-rev", required=True)
    parser.add_argument("--head-rev", required=True)
    parser.add_argument("--min-severity", default="HIGH", choices=SEVERITIES)
    parser.add_argument("--include-unfixed", action="store_true")
    parser.add_argument("--ignorefile", default=".trivyignore.yaml")
    parser.add_argument("--output", required=True, help="Where to write the comment body.")
    args = parser.parse_args(argv)

    ignore_unfixed = not args.include_unfixed
    changes, unchanged = collect_changes(
        load_refs(args.base_rev), load_refs(args.head_rev),
        severities_at_or_above(args.min_severity), args.ignorefile, ignore_unfixed,
    )
    body, has_findings = render_comment(changes, unchanged, args.min_severity, ignore_unfixed)
    Path(args.output).write_text(body)
    print(body)
    if output_file := os.environ.get("GITHUB_OUTPUT"):
        with open(output_file, "a") as fh:
            fh.write(f"has_findings={str(has_findings).lower()}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
