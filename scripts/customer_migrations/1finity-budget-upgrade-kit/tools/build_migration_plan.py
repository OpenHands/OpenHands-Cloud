#!/usr/bin/env python3

import argparse
import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID


def finite_nonnegative(value: Any, field: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result) or result < 0 or (positive and result <= 0):
        qualifier = "positive " if positive else "non-negative "
        raise ValueError(f"{field} must be a finite {qualifier}number")
    return result


def uuid_string(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a UUID string")
    try:
        return str(UUID(value))
    except ValueError as error:
        raise ValueError(f"{field} must be a UUID string") from error


def current_cycle_start(observed_at: datetime, reset_day: int) -> datetime:
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    if not 1 <= reset_day <= 28:
        raise ValueError("reset_day must be between 1 and 28")
    current = observed_at.astimezone(UTC)
    if current.day >= reset_day:
        return datetime(current.year, current.month, reset_day, tzinfo=UTC)
    if current.month == 1:
        return datetime(current.year - 1, 12, reset_day, tzinfo=UTC)
    return datetime(current.year, current.month - 1, reset_day, tzinfo=UTC)


def build_plan(
    snapshots: list[dict[str, Any]],
    roster: dict[str, set[str]],
    reset_day: int,
    observed_at: datetime,
) -> dict[str, Any]:
    cycle_start = current_cycle_start(observed_at, reset_day)
    seen: set[str] = set()
    planned_orgs: list[dict[str, Any]] = []

    for snapshot in snapshots:
        org_id = uuid_string(snapshot.get("org_id"), "org_id")
        if org_id in seen:
            raise ValueError(f"duplicate snapshot for org {org_id}")
        seen.add(org_id)
        if org_id not in roster:
            raise ValueError(f"missing OpenHands roster for org {org_id}")
        if snapshot.get("blocked") is not False:
            raise ValueError(f"org {org_id} is blocked; refusing budget migration")

        team_cap = finite_nonnegative(
            snapshot.get("team_max_budget"),
            f"org {org_id} positive team max budget",
            positive=True,
        )
        team_spend = finite_nonnegative(
            snapshot.get("team_spend"), f"org {org_id} team spend"
        )
        if team_cap < team_spend:
            raise ValueError(
                f"org {org_id} team cap {team_cap} is below current spend {team_spend}"
            )

        raw_members = snapshot.get("members")
        if not isinstance(raw_members, dict):
            raise ValueError(f"org {org_id} members must be an object")
        members = {
            uuid_string(user_id, "member id"): member
            for user_id, member in raw_members.items()
        }
        if len(members) != len(raw_members):
            raise ValueError(f"org {org_id} contains duplicate canonical member IDs")
        member_ids = set(members)
        expected_member_ids = {
            uuid_string(value, "roster member id") for value in roster[org_id]
        }
        if member_ids != expected_member_ids:
            missing = sorted(expected_member_ids - member_ids)
            extra = sorted(member_ids - expected_member_ids)
            raise ValueError(
                f"org {org_id} roster mismatch: missing_from_litellm={missing} extra_in_litellm={extra}"
            )

        overrides = []
        member_baselines: dict[str, float] = {}
        for user_id in sorted(member_ids):
            member = members[user_id]
            if not isinstance(member, dict):
                raise ValueError(f"org {org_id} member {user_id} must be an object")
            spend = finite_nonnegative(
                member.get("spend"), f"org {org_id} member {user_id} spend"
            )
            shared = member.get("uses_shared_budget")
            if not isinstance(shared, bool):
                raise ValueError(
                    f"org {org_id} member {user_id} uses_shared_budget must be boolean"
                )
            cap_value = member.get("max_budget")
            if shared:
                if cap_value is not None:
                    shared_cap = finite_nonnegative(
                        cap_value, f"org {org_id} shared member {user_id} cap"
                    )
                    if abs(shared_cap - team_cap) > 1e-6:
                        raise ValueError(
                            f"org {org_id} shared member {user_id} cap {shared_cap} does not match team cap {team_cap}"
                        )
            else:
                cap = finite_nonnegative(
                    cap_value, f"org {org_id} member {user_id} cap"
                )
                if cap < spend:
                    raise ValueError(
                        f"org {org_id} member cap {cap} for {user_id} is below current spend {spend}"
                    )
                overrides.append(
                    {
                        "user_id": user_id,
                        "monthly_limit": cap,
                        "is_disabled": False,
                    }
                )
            member_baselines[user_id] = 0.0

        keys = snapshot.get("keys")
        if not isinstance(keys, list):
            raise ValueError(f"org {org_id} keys must be an array")
        per_key_budget_count = sum(
            1
            for key in keys
            if isinstance(key, list) and len(key) == 3 and key[2] is not None
        )
        if per_key_budget_count:
            raise ValueError(
                f"org {org_id} has {per_key_budget_count} per-key budget(s), which OpenHands Budgets cannot represent"
            )
        models = snapshot.get("models")
        if not isinstance(models, list) or not all(
            isinstance(model, str) for model in models
        ):
            raise ValueError(f"org {org_id} models must be an array of strings")

        planned_orgs.append(
            {
                "org_id": org_id,
                "enabled": True,
                "monthly_limit": team_cap,
                "reset_day": reset_day,
                "default_user_monthly_limit": None,
                "cycle_start_at": cycle_start.isoformat(),
                "cycle_start_spend": 0.0,
                "user_cycle_start_spend": member_baselines,
                "overrides": overrides,
                "expected_models_after_adoption": [],
                "original_models": sorted(models),
                "preserved_per_key_budget_count": per_key_budget_count,
            }
        )

    roster_orgs = set(roster)
    if seen != roster_orgs:
        missing = sorted(roster_orgs - seen)
        extra = sorted(seen - roster_orgs)
        raise ValueError(f"snapshot org mismatch: missing={missing} extra={extra}")
    if not planned_orgs:
        raise ValueError("no organizations to migrate")

    return {
        "artifact_type": "litellm_to_openhands_budget_migration_plan",
        "artifact_version": 1,
        "generated_at": observed_at.astimezone(UTC).isoformat(),
        "policy": "preserve_absolute_caps_with_zero_baselines",
        "orgs": sorted(planned_orgs, key=lambda item: item["org_id"]),
    }


def load_roster(path: Path) -> dict[str, set[str]]:
    roster: dict[str, set[str]] = {}
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 2:
            raise ValueError(
                f"{path}:{number}: expected tab-separated org_id and user_id"
            )
        org_id = uuid_string(parts[0], "org_id")
        user_id = uuid_string(parts[1], "user_id")
        roster.setdefault(org_id, set()).add(user_id)
    return roster


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--orgs", type=Path, required=True)
    parser.add_argument("--roster", type=Path, required=True)
    parser.add_argument("--reset-day", type=int, default=1)
    parser.add_argument("--observed-at", type=datetime.fromisoformat)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    org_ids = [
        line.strip() for line in args.orgs.read_text().splitlines() if line.strip()
    ]
    snapshots = []
    for org_id in org_ids:
        uuid_string(org_id, "org_id")
        with (args.snapshots / f"{org_id}.json").open() as stream:
            snapshots.append(json.load(stream))
    observed_at = args.observed_at or datetime.now(UTC)
    plan = build_plan(snapshots, load_roster(args.roster), args.reset_day, observed_at)
    args.output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {"orgs": len(plan["orgs"]), "output": str(args.output)}, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
