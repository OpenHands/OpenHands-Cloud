#!/usr/bin/env python3

import json
import os
import sys
from typing import Any

import httpx


def build_operations(
    snapshot: dict[str, Any], org_id: str
) -> list[tuple[str, dict[str, Any]]]:
    required = {"team_max_budget", "models", "blocked", "member_caps", "keys"}
    missing = sorted(required - snapshot.keys())
    if missing:
        raise ValueError(f"snapshot is missing required fields: {', '.join(missing)}")
    if snapshot.get("org_id") not in (None, org_id):
        raise ValueError("snapshot org_id does not match requested org")

    operations: list[tuple[str, dict[str, Any]]] = [
        (
            "/team/update",
            {
                "team_id": org_id,
                "max_budget": snapshot["team_max_budget"],
                "models": snapshot["models"],
                "blocked": snapshot["blocked"],
            },
        )
    ]
    members = snapshot.get("members")
    if members is not None:
        if not isinstance(members, dict):
            raise ValueError("snapshot members must be an object")
        member_updates = {
            user_id: None
            if member.get("uses_shared_budget") is True
            else member.get("max_budget")
            for user_id, member in members.items()
        }
    else:
        member_updates = snapshot["member_caps"]
    for user_id, max_budget in sorted(member_updates.items()):
        operations.append(
            (
                "/team/member_update",
                {
                    "team_id": org_id,
                    "user_id": user_id,
                    "max_budget_in_team": max_budget,
                },
            )
        )
    for key_alias, token, max_budget in snapshot["keys"]:
        if not token:
            raise ValueError(
                f"key {key_alias!r} has no token identifier and cannot be restored safely"
            )
        operations.append(("/key/update", {"key": token, "max_budget": max_budget}))
    return operations


def main() -> int:
    org_id = sys.argv[1]
    snapshot = json.load(sys.stdin)
    base_url = os.environ["LITE_LLM_API_URL"].rstrip("/")
    headers = {"Authorization": "Bearer " + os.environ["LITE_LLM_API_KEY"]}
    operations = build_operations(snapshot, org_id)

    with httpx.Client(base_url=base_url, headers=headers, timeout=30) as client:
        for path, payload in operations:
            response = client.post(path, json=payload)
            if response.is_error:
                raise RuntimeError(f"{path} failed with HTTP {response.status_code}")
    print(
        json.dumps({"org_id": org_id, "operations": len(operations), "restored": True})
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
