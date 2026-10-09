#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.12"
# ///
"""Copa-patch the images listed in a patch manifest, every platform each one ships.

A manifest (``patches/<release>.json``) lists ``{"source", "target"}`` pairs.
Patched images are pushed as ``<target>:<source tag>-patched``, keeping the
source's platform set. When ``target`` differs from the source repository (a
third-party image), the source is first mirrored to ``<target>:<source tag>``
so Copa can push next to it.

Patch one manifest entry (one CI matrix job):

    uv run scripts/copa_patch_images.py patch patches/0.71.2.json --index 0 --out result --push

Without ``--push`` it only scans, reporting what would be patched. An entry
whose ``-patched`` tag already exists is skipped (re-runs only retry what is
missing or failed); ``--force`` re-patches it. Then merge
the per-entry results into the lock file and a Markdown summary:

    uv run scripts/copa_patch_images.py merge patches/0.71.2.json results/ > summary.md

Requires ``trivy``, ``copa`` and ``docker buildx`` (builder named by
``--builder``); private sources read TRIVY_USERNAME/TRIVY_PASSWORD.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

SEVERITIES = ("CRITICAL", "HIGH")
# Copa rebuilds these in place; Go binaries and jars need their source and fail the platform.
COPA_LIBRARY_TYPES = {"python-pkg", "node-pkg"}


def run(cmd: list[str], **kwargs) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kwargs).stdout


def split_ref(ref: str) -> tuple[str, str]:
    name = ref.split("@")[0]
    repo, sep, tag = name.rpartition(":")
    if not sep or "/" in tag:
        return name, "latest"
    return repo, tag


def platforms_from_index(raw: dict) -> list[str]:
    """Platforms of a manifest list; empty for a single-platform manifest."""
    found = []
    for m in raw.get("manifests") or []:
        p = m.get("platform") or {}
        if p.get("os") in (None, "unknown"):
            continue  # attestation manifests
        found.append("/".join(x for x in (p["os"], p["architecture"], p.get("variant")) if x))
    return found


def copa_report(report: dict, library: bool = True) -> dict:
    """Keep only the results Copa can patch: OS packages, plus Python/Node libraries if ``library``."""
    results = [r for r in report.get("Results") or []
               if r.get("Class") == "os-pkgs" or (library and r.get("Type") in COPA_LIBRARY_TYPES)]
    return {**report, "Results": results}


def counts(report: dict) -> dict:
    vulns = [v for r in report.get("Results") or [] for v in r.get("Vulnerabilities") or []]
    result = {}
    for sev in SEVERITIES:
        hits = [v for v in vulns if v["Severity"] == sev]
        result[sev.lower()] = len(hits)
        result[f"{sev.lower()}_fixable"] = sum(1 for v in hits if v.get("FixedVersion"))
    return result


def _cell(before: dict, after: dict, sev: str) -> str:
    def fmt(c: dict) -> str:
        return f"{c[sev]} ({c[sev + '_fixable']} fixable)" if sev in c else "—"
    return f"{fmt(before)} → {fmt(after)}"


def trivy(ref: str, platform: str | None, *extra: str) -> dict:
    platform_args = ["--platform", platform] if platform else []
    out = run(["trivy", "image", "-q", "--image-src", "remote", "--format", "json",
               *platform_args, *extra, ref])
    return json.loads(out)


def remote_digest(ref: str) -> str | None:
    """Digest of ``ref`` in its registry, or None when the tag does not exist."""
    proc = subprocess.run(["docker", "buildx", "imagetools", "inspect", ref, "--format", "{{json .Manifest}}"],
                          capture_output=True, text=True)
    return json.loads(proc.stdout)["digest"] if proc.returncode == 0 else None


def patch(entry: dict, out: Path, push: bool, builder: str, force: bool = False) -> dict:
    source, target = entry["source"], entry["target"]
    source_repo, tag = split_ref(source)
    patched = f"{target}:{tag}-patched"
    existing = None if force else remote_digest(patched)
    if existing:
        return {"source": source, "image": patched, "status": "existing", "digest": existing,
                "platforms": [], "before": {}, "after": {}}
    raw = json.loads(run(["docker", "buildx", "imagetools", "inspect", "--raw", source]))
    platforms = platforms_from_index(raw) or [None]

    reports, os_reports = out / "reports", out / "reports-os"
    reports.mkdir(parents=True, exist_ok=True)
    os_reports.mkdir(parents=True, exist_ok=True)
    result = {"source": source, "image": patched, "status": "planned",
              "platforms": [p or "single" for p in platforms], "before": {}, "after": {}}
    for p in platforms:
        name = (p or "single").replace("/", "-")
        report = trivy(source, p, "--pkg-types", "os,library", "--ignore-unfixed")
        (reports / f"{name}.json").write_text(json.dumps(copa_report(report)))
        (os_reports / f"{name}.json").write_text(json.dumps(copa_report(report, library=False)))
        result["before"][p or "single"] = counts(trivy(source, p, "--scanners", "vuln",
                                                       "--severity", ",".join(SEVERITIES)))
    if not push:
        return result

    patch_input = source
    if target != source_repo:
        patch_input = f"{target}:{tag}"
        if not remote_digest(patch_input):
            run(["docker", "buildx", "imagetools", "create", "--tag", patch_input, source])
    def copa(report_dir: Path, pkg_types: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["copa", "patch", "--image", patch_input, "--report", str(report_dir), "--tag", f"{tag}-patched",
             "--pkg-types", pkg_types, "--library-patch-level", "minor", "--ignore-errors",
             "--push", "--addr", f"buildx://{builder}", "--timeout", "45m"],
            env={**os.environ, "COPA_EXPERIMENTAL": "1"}, capture_output=True, text=True,
        )

    proc = copa(reports, "os,library")
    log = proc.stdout + proc.stderr
    if proc.returncode != 0:
        # Library patching can fail a whole platform (e.g. npm without a lockfile); OS-only is the reliable mode.
        result["fallback"] = "os-only"
        proc = copa(os_reports, "os")
        log += "\n--- retry with --pkg-types os ---\n" + proc.stdout + proc.stderr
    (out / "copa.log").write_text(log)
    # Single-platform runs log "Patch Summary"; multi-platform runs print one status row per platform.
    result["copa"] = [" ".join(line.split("msg=")[-1].strip('"').split())
                      for line in (proc.stdout + proc.stderr).splitlines()
                      if "Patch Summary" in line or line.startswith(("✓", "⊘", "✗"))]
    if proc.returncode != 0:
        result["status"] = "failed"
        result["error"] = (proc.stderr.strip().splitlines() or ["copa failed"])[-1]
        return result

    result["status"] = "patched"
    result["digest"] = remote_digest(patched)
    for p in platforms:
        result["after"][p or "single"] = counts(trivy(patched, p, "--scanners", "vuln",
                                                      "--severity", ",".join(SEVERITIES)))
    return result


def summary(results: list[dict]) -> str:
    lines = [
        "| Image | Platform | Critical (before → after) | High (before → after) | Copa |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        if r.get("status") == "existing":
            lines.append(f"| `{r['image']}` | — | — | — | already patched ({r['digest'][:19]}…), skipped |")
            continue
        note = r.get("error") or "; ".join(r.get("copa") or []) or "plan only"
        if not r["before"]:
            lines.append(f"| `{r['image']}` | — | — | — | {note} |")
        for p, before in r["before"].items():
            after = r["after"].get(p, {})
            lines.append(f"| `{r['image']}` | {p} | {_cell(before, after, 'critical')} | "
                         f"{_cell(before, after, 'high')} | {note} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p_patch = sub.add_parser("patch")
    p_patch.add_argument("manifest", type=Path)
    p_patch.add_argument("--index", type=int, required=True)
    p_patch.add_argument("--out", type=Path, required=True)
    p_patch.add_argument("--push", action="store_true")
    p_patch.add_argument("--builder", default="copa")
    p_patch.add_argument("--force", action="store_true", help="re-patch even if the -patched tag exists")
    p_merge = sub.add_parser("merge")
    p_merge.add_argument("manifest", type=Path)
    p_merge.add_argument("results", type=Path)
    args = parser.parse_args()

    entries = json.loads(args.manifest.read_text())["images"]
    if args.command == "patch":
        args.out.mkdir(parents=True, exist_ok=True)
        try:
            result = patch(entries[args.index], args.out, args.push, args.builder, args.force)
        except subprocess.CalledProcessError as error:
            result = {"source": entries[args.index]["source"], "image": entries[args.index]["target"],
                      "status": "failed", "platforms": [], "before": {}, "after": {},
                      "error": (error.stderr or str(error)).strip().splitlines()[-1]}
        (args.out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
        return 1 if "error" in result else 0

    results = []
    for i, entry in enumerate(entries):
        path = args.results / f"result-{i}" / "result.json"
        results.append(json.loads(path.read_text()) if path.exists()
                       else {"source": entry["source"], "image": entry["target"], "status": "failed",
                             "platforms": [], "before": {}, "after": {}, "error": "no result"})
    args.manifest.with_suffix(".lock.json").write_text(json.dumps({"images": results}, indent=2) + "\n")
    print(summary(results))
    return 1 if any("error" in r for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
