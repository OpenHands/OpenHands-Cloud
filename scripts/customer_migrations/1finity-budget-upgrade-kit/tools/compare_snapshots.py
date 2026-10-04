#!/usr/bin/env python3

import argparse
import json
from pathlib import Path

FIELDS = (
    "team_max_budget",
    "team_spend",
    "models",
    "blocked",
    "members",
    "member_caps",
    "keys",
)


def load(path: Path) -> dict:
    with path.open() as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"{path} is not a JSON object")
    return value


def compare(before_dir: Path, after_dir: Path, orgs: list[str]) -> dict:
    results = []
    for org_id in orgs:
        before = load(before_dir / f"{org_id}.json")
        after = load(after_dir / f"{org_id}.json")
        changed_fields = [
            field for field in FIELDS if before.get(field) != after.get(field)
        ]
        results.append(
            {
                "org_id": org_id,
                "unchanged": not changed_fields,
                "changed_fields": changed_fields,
                "models_before": before.get("models"),
                "models_expected": before.get("models"),
                "models_after": after.get("models"),
                "blocked": after.get("blocked"),
            }
        )
    mismatched = [result["org_id"] for result in results if not result["unchanged"]]
    blocked = [result["org_id"] for result in results if result["blocked"] is not False]
    return {
        "orgs": results,
        "mismatched_org_ids": mismatched,
        "blocked_org_ids": blocked,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--orgs", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--mismatched", type=Path, required=True)
    args = parser.parse_args()

    orgs = [line.strip() for line in args.orgs.read_text().splitlines() if line.strip()]
    report = compare(args.before, args.after, orgs)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    args.mismatched.write_text(
        "".join(f"{org_id}\n" for org_id in report["mismatched_org_ids"])
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if report["mismatched_org_ids"] or report["blocked_org_ids"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
