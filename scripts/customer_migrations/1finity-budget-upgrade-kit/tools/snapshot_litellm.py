#!/usr/bin/env python3

import json
import math
import os
import sys
from typing import Any

import httpx


def finite_nonnegative(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return result


def build_snapshot(
    org_id: str, team: dict[str, Any], keys: list[dict[str, Any]]
) -> dict[str, Any]:
    info = team.get("team_info")
    if not isinstance(info, dict):
        raise ValueError("team response is missing team_info")
    if "max_budget" not in info or "spend" not in info:
        raise ValueError("team_info is missing max_budget or spend")
    team_cap = info["max_budget"]
    team_spend = finite_nonnegative(info["spend"], "team spend")

    metadata = info.get("metadata") or {}
    if not isinstance(metadata, dict):
        raise ValueError("team metadata must be an object")
    default_budget_id = metadata.get("team_member_budget_id")

    memberships: dict[str, dict[str, Any]] = {}
    for membership in team.get("team_memberships") or []:
        if not isinstance(membership, dict):
            raise ValueError("team membership must be an object")
        user_id = membership.get("user_id")
        if isinstance(user_id, str) and user_id and user_id != "default_user_id":
            memberships[user_id] = membership

    role_member_ids = set()
    for member in info.get("members_with_roles") or []:
        if not isinstance(member, dict):
            raise ValueError("role member must be an object")
        user_id = member.get("user_id")
        if isinstance(user_id, str) and user_id and user_id != "default_user_id":
            role_member_ids.add(user_id)

    members: dict[str, dict[str, Any]] = {}
    for user_id, membership in memberships.items():
        if "spend" not in membership or membership["spend"] is None:
            raise ValueError(f"membership {user_id} is missing spend")
        budget_id = membership.get("budget_id")
        shared = budget_id is None or (
            default_budget_id is not None and budget_id == default_budget_id
        )
        if shared:
            max_budget = team_cap
        else:
            budget_table = membership.get("litellm_budget_table")
            if not isinstance(budget_table, dict) or "max_budget" not in budget_table:
                raise ValueError(f"membership {user_id} is missing private max_budget")
            max_budget = budget_table["max_budget"]
        members[user_id] = {
            "spend": finite_nonnegative(membership["spend"], f"member {user_id} spend"),
            "max_budget": max_budget,
            "uses_shared_budget": shared,
        }

    role_only_ids = role_member_ids - memberships.keys()
    role_only_spend = {user_id: 0.0 for user_id in role_only_ids}
    role_only_key_counts = {user_id: 0 for user_id in role_only_ids}
    for key in keys:
        user_id = key.get("user_id")
        if user_id in role_only_ids:
            role_only_spend[user_id] += finite_nonnegative(
                key.get("spend"), f"key spend for member {user_id}"
            )
            role_only_key_counts[user_id] += 1
    missing_key_spend = sorted(
        user_id for user_id, count in role_only_key_counts.items() if count == 0
    )
    if missing_key_spend:
        raise ValueError(
            "role-only members have no validated key spend: "
            + ", ".join(missing_key_spend)
        )
    for user_id, spend in role_only_spend.items():
        members[user_id] = {
            "spend": spend,
            "max_budget": team_cap,
            "uses_shared_budget": True,
        }

    return {
        "org_id": org_id,
        "team_max_budget": team_cap,
        "team_spend": team_spend,
        "models": sorted(info.get("models") or []),
        "blocked": info.get("blocked"),
        "members": dict(sorted(members.items())),
        "member_caps": {
            user_id: member["max_budget"] for user_id, member in sorted(members.items())
        },
        "keys": sorted(
            [
                [key.get("key_alias"), key.get("token"), key.get("max_budget")]
                for key in keys
            ],
            key=str,
        ),
    }


def main() -> int:
    org_id = sys.argv[1]
    base_url = os.environ["LITE_LLM_API_URL"].rstrip("/")
    headers = {"Authorization": "Bearer " + os.environ["LITE_LLM_API_KEY"]}
    with httpx.Client(base_url=base_url, headers=headers, timeout=30) as client:
        response = client.get("/team/info", params={"team_id": org_id})
        response.raise_for_status()
        team = response.json()

        keys = []
        page = 1
        total_count = None
        while True:
            response = client.get(
                "/key/list",
                params={
                    "team_id": org_id,
                    "return_full_object": "true",
                    "size": 100,
                    "page": page,
                },
            )
            response.raise_for_status()
            payload = response.json()
            keys.extend(payload.get("keys") or [])
            total_count = payload.get("total_count", total_count)
            total_pages = int(payload.get("total_pages") or 1)
            if page >= total_pages:
                break
            page += 1
    if total_count is not None and int(total_count) != len(keys):
        raise RuntimeError(
            f"key snapshot incomplete for {org_id}: received {len(keys)} of {total_count}"
        )
    print(json.dumps(build_snapshot(org_id, team, keys), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
