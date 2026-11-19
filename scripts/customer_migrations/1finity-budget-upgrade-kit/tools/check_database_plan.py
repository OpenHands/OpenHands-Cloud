#!/usr/bin/env python3

import argparse
import json
from pathlib import Path
from typing import Any


def normalized_number(value: Any) -> float | None:
    return None if value is None else float(value)


def check(plan: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    expected_orgs = {}
    expected_overrides = []
    for org in plan.get("orgs") or []:
        org_id = org["org_id"]
        expected_orgs[org_id] = {
            "org_id": org_id,
            "enabled": True,
            "monthly_limit": normalized_number(org["monthly_limit"]),
            "reset_day": int(org["reset_day"]),
            "default_user_monthly_limit": None,
            "cycle_start_at": org["cycle_start_at"],
            "cycle_start_spend": normalized_number(org["cycle_start_spend"]),
            "user_cycle_start_spend": {
                user_id: normalized_number(value)
                for user_id, value in sorted(org["user_cycle_start_spend"].items())
            },
            "litellm_last_sync_status": "success",
            "litellm_last_sync_error": None,
        }
        for override in org["overrides"]:
            expected_overrides.append(
                {
                    "org_id": org_id,
                    "user_id": override["user_id"],
                    "monthly_limit": normalized_number(override["monthly_limit"]),
                    "is_disabled": False,
                }
            )

    actual_orgs = {}
    for org in state.get("orgs") or []:
        org_id = org["org_id"]
        normalized = dict(org)
        normalized["monthly_limit"] = normalized_number(org.get("monthly_limit"))
        normalized["cycle_start_spend"] = normalized_number(
            org.get("cycle_start_spend")
        )
        normalized["user_cycle_start_spend"] = {
            user_id: normalized_number(value)
            for user_id, value in sorted(
                (org.get("user_cycle_start_spend") or {}).items()
            )
        }
        actual_orgs[org_id] = normalized

    actual_overrides = []
    for override in state.get("overrides") or []:
        normalized = dict(override)
        normalized["monthly_limit"] = normalized_number(override.get("monthly_limit"))
        actual_overrides.append(normalized)

    org_mismatches = []
    for org_id in sorted(set(expected_orgs) | set(actual_orgs)):
        if expected_orgs.get(org_id) != actual_orgs.get(org_id):
            org_mismatches.append(org_id)
    expected_overrides.sort(key=lambda item: (item["org_id"], item["user_id"]))
    actual_overrides.sort(key=lambda item: (item["org_id"], item["user_id"]))
    overrides_match = expected_overrides == actual_overrides
    return {
        "valid": not org_mismatches and overrides_match,
        "org_mismatches": org_mismatches,
        "overrides_match": overrides_match,
        "expected_override_count": len(expected_overrides),
        "actual_override_count": len(actual_overrides),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    state = json.loads(args.state.read_text())
    report = check(plan, state)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
