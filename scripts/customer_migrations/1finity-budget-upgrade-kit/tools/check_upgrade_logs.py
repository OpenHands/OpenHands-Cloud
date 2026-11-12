#!/usr/bin/env python3

import argparse
import json
from pathlib import Path


def artifacts(path: Path) -> list[dict]:
    found = []
    for line in path.read_text().splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if value.get("artifact_type") == "org_budget_preflight":
            found.append(value)
    return found


def inspect(path: Path, expected_org_ids: set[str] | None = None) -> dict:
    expected_org_ids = expected_org_ids or set()
    found = artifacts(path)
    if not found:
        return {
            "file": str(path),
            "valid": False,
            "error": "no org_budget_preflight artifact found",
        }
    summaries = []
    valid = True
    for artifact in found:
        orgs = artifact.get("orgs", [])
        org_ids = sorted(org.get("org_id") for org in orgs)
        reported_count = artifact.get("summary", {}).get("orgs")
        summary = artifact.get("summary", {})
        litellm = artifact.get("litellm", {})
        invalid_orgs = []
        for org in orgs:
            findings = org.get("findings") or []
            last_sync = org.get("last_sync") or {}
            observed = org.get("litellm") or {}
            org_valid = (
                org.get("blocking") is False
                and not (org.get("cap_drift") or [])
                and not any(
                    finding.get("severity") == "blocking" for finding in findings
                )
                and last_sync.get("status") == "success"
                and last_sync.get("error") is None
                and observed.get("status") == "live"
                and observed.get("error") is None
            )
            if not org_valid:
                invalid_orgs.append(org.get("org_id"))
        artifact_valid = (
            set(org_ids) == expected_org_ids
            and reported_count == len(expected_org_ids)
            and artifact.get("error") is None
            and litellm.get("reachable") is True
            and not (litellm.get("unreachable_orgs") or [])
            and summary.get("blocking") is False
            and summary.get("blocking_orgs") == 0
            and not (summary.get("blocking_org_ids") or [])
            and not invalid_orgs
        )
        valid = valid and artifact_valid
        summaries.append(
            {
                "phase": artifact.get("phase"),
                "mode": artifact.get("mode"),
                "org_ids": org_ids,
                "expected_org_ids": sorted(expected_org_ids),
                "reported_org_count": reported_count,
                "invalid_org_ids": sorted(invalid_orgs),
                "error": artifact.get("error"),
                "valid": artifact_valid,
            }
        )
    return {"file": str(path), "valid": valid, "artifacts": summaries}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("logs", nargs="+")
    parser.add_argument("--expected-orgs", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    expected_org_ids = set()
    if args.expected_orgs:
        expected_org_ids = {
            line.strip()
            for line in args.expected_orgs.read_text().splitlines()
            if line.strip()
        }
    report = {
        "expected_org_ids": sorted(expected_org_ids),
        "logs": [inspect(Path(path), expected_org_ids) for path in args.logs],
    }
    report["valid"] = all(item["valid"] for item in report["logs"])
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
