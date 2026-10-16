#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# dependencies = ["PyYAML"]
# ///
"""Render the published OpenHands chart, Trivy-scan every image it deploys, and
write a vulnerability report.

Outputs under ``--out``:

  * ``summary.md``    -- per-image table, fixable findings grouped by package,
                         and per-image CVE detail (rendered as the run summary).
  * ``findings.json`` -- one flat record per finding, for jq or an agent.
  * ``raw/``          -- Trivy's JSON per image.

Requires ``helm`` and ``trivy`` on PATH. Run it from the chart repo root:

    uv run scripts/scan_chart_images.py --version 0.78.0 --out scan-out
"""

import argparse
import datetime
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import yaml

CHART = "oci://ghcr.io/openhands/helm-charts/openhands"
# Every optional component, so the scan covers any image a customer could run.
DEFAULT_SETS = [
    f"{component}.enabled=true"
    for component in (
        "keycloak", "laminar", "litellm-helm", "minio", "rustfs", "valkey", "device-plugin",
        "plugin-directory", "automation", "agent-canvas", "integrations-hub",
    )
]
SEVERITIES = ("CRITICAL", "HIGH")
# GitHub caps a step summary at 1 MiB.
SUMMARY_LIMIT = 1_000_000
TOP_PACKAGES = 30


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def find_images(node) -> set[str]:
    found = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "image" and isinstance(value, str):
                found.add(value)
            else:
                found |= find_images(value)
    elif isinstance(node, list):
        for value in node:
            found |= find_images(value)
    return found


def normalize(ref: str) -> str:
    name, _, digest = ref.partition("@")
    if ":" not in name.rsplit("/", 1)[-1]:
        name += ":latest"
    return f"{name}@{digest}" if digest else name


def split_ref(ref: str) -> tuple[str, str]:
    name = ref.split("@")[0]
    repo, _, tag = name.rpartition(":")
    return repo, tag


def chart_images(chart: str, version: str | None, sets: list[str]) -> tuple[str, list[str]]:
    version_args = ["--version", version] if version else []
    resolved = yaml.safe_load(run(["helm", "show", "chart", chart, *version_args]))["version"]
    set_args = [arg for s in sets for arg in ("--set", s)]
    rendered = run(["helm", "template", "oh", chart, "--version", resolved, *set_args])
    images = set()
    for doc in yaml.safe_load_all(rendered):
        images |= find_images(doc)
    # Sandbox pods are created by runtime-api at runtime, so this image is only in values.
    sandbox = yaml.safe_load(run(["helm", "show", "values", chart, "--version", resolved]))
    agent = sandbox["global"]["agentServerImage"]
    images.add(f"{agent['repository']}:{agent['tag']}")
    return resolved, sorted({normalize(i) for i in images})


def scan(ref: str, raw_dir: Path) -> tuple[dict | None, str]:
    path = raw_dir / (re.sub(r"[^A-Za-z0-9._-]+", "_", ref) + ".json")
    proc = subprocess.run(
        ["trivy", "image", "-q", "--image-src", "remote", "--scanners", "vuln", "--severity", ",".join(SEVERITIES),
         "--format", "json", "--output", str(path), ref],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None, proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else "trivy failed"
    return json.loads(path.read_text()), ""


def layer_commands(metadata: dict) -> dict[str, str]:
    history = [h for h in (metadata.get("ImageConfig") or {}).get("history") or []
               if not h.get("empty_layer")]
    diff_ids = metadata.get("DiffIDs") or []
    if len(history) != len(diff_ids):
        return {}
    return {d: h.get("created_by", "") for d, h in zip(diff_ids, history)}


def cvss(v: dict) -> dict | None:
    scores = v.get("CVSS") or {}
    source = next((s for s in (v.get("SeveritySource"), "nvd") if s in scores), next(iter(scores), None))
    if source is None:
        return None
    entry = scores[source]
    return {
        "score": entry.get("V40Score") or entry.get("V3Score") or entry.get("V2Score"),
        "vector": entry.get("V40Vector") or entry.get("V3Vector") or entry.get("V2Vector"),
        "source": source,
    }


def flatten(ref: str, report: dict) -> list[dict]:
    commands = layer_commands(report.get("Metadata") or {})
    rows = []
    for result in report.get("Results") or []:
        # Source package (e.g. libssl3 -> openssl) is only on the package list.
        sources = {p["Identifier"]["UID"]: p["SrcName"] for p in result.get("Packages") or []
                   if p.get("SrcName") and (p.get("Identifier") or {}).get("UID")}
        for v in result.get("Vulnerabilities") or []:
            ident = v.get("PkgIdentifier") or {}
            rows.append({
                "image": ref,
                "severity": v["Severity"],
                "cve": v["VulnerabilityID"],
                "aliases": v.get("VendorIDs") or [],
                "cvss": cvss(v),
                "cwe": v.get("CweIDs") or [],
                "pkg": v["PkgName"],
                "src": sources.get(ident.get("UID")) or v["PkgName"],
                "type": result.get("Type", ""),
                "purl": ident.get("PURL", ""),
                "installed": v.get("InstalledVersion", ""),
                "fixed": v.get("FixedVersion", ""),
                "status": v.get("Status", ""),
                "pkg_path": v.get("PkgPath", ""),
                "target": result.get("Target", ""),
                "introduced_by": commands.get((v.get("Layer") or {}).get("DiffID", ""), ""),
                "title": v.get("Title", ""),
                "description": v.get("Description", ""),
                "url": v.get("PrimaryURL", ""),
                "references": v.get("References") or [],
                "published": (v.get("PublishedDate") or "")[:10],
            })
    return rows


def image_table(images: list[str], findings: list[dict], failures: dict[str, str]) -> list[str]:
    stats = {}
    for ref in images:
        mine = [f for f in findings if f["image"] == ref]
        count = lambda sev, fixed: sum(1 for f in mine if f["severity"] == sev and bool(f["fixed"]) == fixed)
        stats[ref] = (
            count("CRITICAL", True) + count("CRITICAL", False),
            count("HIGH", True) + count("HIGH", False),
            count("CRITICAL", True), count("HIGH", True),
            count("CRITICAL", False), count("HIGH", False),
        )
    lines = [
        "| Image | Version | Critical | High | Fix available (C / H) | No fix yet (C / H) |",
        "|---|---|---:|---:|---|---|",
    ]
    for ref in sorted(images, key=lambda r: (-stats[r][0], -stats[r][1], r)):
        repo, tag = split_ref(ref)
        if ref in failures:
            lines.append(f"| {repo} | {tag} | ⚠️ | ⚠️ | scan failed: {failures[ref]} | |")
            continue
        c, h, fc, fh, nc, nh = stats[ref]
        lines.append(f"| {repo} | {tag} | {c} | {h} | {fc} / {fh} | {nc} / {nh} |")
    totals = [sum(s[i] for s in stats.values()) for i in range(6)]
    lines.append(
        f"| **Total** | | **{totals[0]}** | **{totals[1]}** | {totals[2]} / {totals[3]} | {totals[4]} / {totals[5]} |"
    )
    return lines


def _short(values: set[str], limit: int = 4) -> str:
    ordered = sorted(values)
    more = f", +{len(ordered) - limit} more" if len(ordered) > limit else ""
    return ", ".join(ordered[:limit]) + more


def package_table(findings: list[dict]) -> list[str]:
    groups = defaultdict(lambda: {"cves": {s: set() for s in SEVERITIES}, "images": set(),
                                  "installed": set(), "fixed": set()})
    for f in findings:
        if not f["fixed"]:
            continue
        g = groups[(f["type"], f["src"])]
        g["cves"][f["severity"]].add(f["cve"])
        g["images"].add(split_ref(f["image"])[0].rsplit("/", 1)[-1])
        g["installed"].add(f["installed"])
        g["fixed"].update(x.strip() for x in f["fixed"].split(","))
    ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]["cves"]["CRITICAL"]),
                                                    -len(kv[1]["cves"]["HIGH"]), kv[0]))
    lines = [
        "| Source package | Type | Critical CVEs | High CVEs | Installed | Fixed in | Images |",
        "|---|---|---:|---:|---|---|---|",
    ]
    for (ptype, pkg), g in ranked[:TOP_PACKAGES]:
        lines.append(
            f"| `{pkg}` | {ptype} | {len(g['cves']['CRITICAL'])} | {len(g['cves']['HIGH'])} | "
            f"{_short(g['installed'])} | {_short(g['fixed'])} | "
            f"{', '.join(sorted(g['images']))} |"
        )
    if len(ranked) > TOP_PACKAGES:
        lines.append(f"\n_{len(ranked) - TOP_PACKAGES} more packages in `findings.json`._")
    return lines


def image_details(images: list[str], findings: list[dict]) -> list[str]:
    order = {s: i for i, s in enumerate(SEVERITIES)}
    lines = []
    for ref in images:
        mine = sorted((f for f in findings if f["image"] == ref),
                      key=lambda f: (order[f["severity"]], f["pkg"], f["cve"]))
        if not mine:
            continue
        crit = sum(1 for f in mine if f["severity"] == "CRITICAL")
        lines += [
            f"<details><summary><code>{ref}</code> — {crit} critical / {len(mine) - crit} high</summary>",
            "",
            "| Severity | CVE | Package | Installed | Fixed in |",
            "|---|---|---|---|---|",
        ]
        for f in mine:
            cve = f"[{f['cve']}]({f['url']})" if f["url"] else f["cve"]
            lines.append(f"| {f['severity']} | {cve} | `{f['pkg']}` | {f['installed']} | {f['fixed'] or '—'} |")
        lines += ["", "</details>", ""]
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--chart", default=CHART)
    parser.add_argument("--version", default=None, help="chart version; latest published when omitted")
    parser.add_argument("--set", dest="sets", action="append", help="helm --set; replaces the default profile")
    parser.add_argument("--out", type=Path, default=Path("scan-out"))
    args = parser.parse_args()
    sets = args.sets or DEFAULT_SETS

    raw_dir = args.out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    version, images = chart_images(args.chart, args.version or None, sets)

    findings, failures = [], {}
    for ref in images:
        print(f"scanning {ref}", file=sys.stderr, flush=True)
        report, error = scan(ref, raw_dir)
        if report is None:
            failures[ref] = error
            continue
        findings += flatten(ref, report)

    db = json.loads(run(["trivy", "version", "--format", "json"]))
    db_updated = (db.get("VulnerabilityDB") or {}).get("UpdatedAt", "unknown")
    (args.out / "findings.json").write_text(json.dumps(findings, indent=1))

    header = [
        f"# Chart image scan: openhands {version}",
        "",
        f"Profile: `{' '.join(sets)}` · Trivy {db.get('Version', '?')} · DB updated {db_updated} · "
        f"scanned {datetime.datetime.now(datetime.UTC):%Y-%m-%d %H:%M} UTC",
        "",
    ]
    body = image_table(images, findings, failures) + [
        "",
        "## Fixable, grouped by package",
        "",
        "One row per source package. In images we build, a row is one dependency bump; "
        "in third-party images, the fix is a newer upstream tag.",
        "",
    ] + package_table(findings) + ["", "## Findings per image", ""]
    details = image_details(images, findings)
    summary = "\n".join(header + body + details)
    if len(summary.encode()) > SUMMARY_LIMIT:
        summary = "\n".join(header + body + ["_Too large for the run summary; see `findings.json`._"])
    (args.out / "summary.md").write_text(summary + "\n")

    for ref, error in failures.items():
        print(f"FAILED {ref}: {error}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
