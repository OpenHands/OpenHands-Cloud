#!/usr/bin/env python3

import importlib.util
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


migration = load_module(
    "build_migration_plan", ROOT / "tools" / "build_migration_plan.py"
)
check_upgrade_logs = load_module(
    "check_upgrade_logs_migration", ROOT / "tools" / "check_upgrade_logs.py"
)
snapshot_litellm = load_module(
    "snapshot_litellm", ROOT / "tools" / "snapshot_litellm.py"
)
render_migration_sql = load_module(
    "render_migration_sql", ROOT / "tools" / "render_migration_sql.py"
)
check_database_plan = load_module(
    "check_database_plan", ROOT / "tools" / "check_database_plan.py"
)


class LiteLlmSnapshotTests(unittest.TestCase):
    def test_distinguishes_private_and_shared_role_only_members(self):
        team = {
            "team_info": {
                "max_budget": 5000,
                "spend": 125,
                "models": ["model-a"],
                "blocked": False,
                "metadata": {"team_member_budget_id": "shared"},
                "members_with_roles": [
                    {"user_id": "private-user"},
                    {"user_id": "shared-user"},
                ],
            },
            "team_memberships": [
                {
                    "user_id": "private-user",
                    "spend": 25,
                    "budget_id": "private",
                    "litellm_budget_table": {"max_budget": 250},
                }
            ],
        }
        keys = [
            {
                "user_id": "shared-user",
                "spend": 10,
                "key_alias": "shared-key",
                "token": "token",
                "max_budget": 75,
            }
        ]
        snapshot = snapshot_litellm.build_snapshot("org-1", team, keys)
        self.assertEqual(snapshot["team_spend"], 125)
        self.assertEqual(
            snapshot["members"]["private-user"],
            {"spend": 25, "max_budget": 250, "uses_shared_budget": False},
        )
        self.assertEqual(
            snapshot["members"]["shared-user"],
            {"spend": 10, "max_budget": 5000, "uses_shared_budget": True},
        )


class MigrationPlanTests(unittest.TestCase):
    def snapshot(self):
        return {
            "org_id": "11111111-1111-4111-8111-111111111111",
            "team_max_budget": 5000,
            "team_spend": 125,
            "models": ["model-a"],
            "blocked": False,
            "members": {
                "22222222-2222-4222-8222-222222222222": {
                    "spend": 25,
                    "max_budget": 250,
                    "uses_shared_budget": False,
                },
                "33333333-3333-4333-8333-333333333333": {
                    "spend": 10,
                    "max_budget": 5000,
                    "uses_shared_budget": True,
                },
            },
            "member_caps": {
                "22222222-2222-4222-8222-222222222222": 250,
                "33333333-3333-4333-8333-333333333333": 5000,
            },
            "keys": [["alias", "token", None]],
        }

    def roster(self):
        return {
            "11111111-1111-4111-8111-111111111111": {
                "22222222-2222-4222-8222-222222222222",
                "33333333-3333-4333-8333-333333333333",
            }
        }

    def test_preserves_absolute_caps_as_monthly_limits(self):
        plan = migration.build_plan(
            [self.snapshot()],
            self.roster(),
            reset_day=1,
            observed_at=datetime(2026, 10, 2, 12, tzinfo=UTC),
        )
        org = plan["orgs"][0]
        self.assertEqual(org["monthly_limit"], 5000)
        self.assertEqual(org["cycle_start_spend"], 0)
        self.assertEqual(org["cycle_start_at"], "2026-10-01T00:00:00+00:00")
        self.assertIsNone(org["default_user_monthly_limit"])
        self.assertEqual(
            org["user_cycle_start_spend"],
            {
                "22222222-2222-4222-8222-222222222222": 0,
                "33333333-3333-4333-8333-333333333333": 0,
            },
        )
        self.assertEqual(
            org["overrides"],
            [
                {
                    "user_id": "22222222-2222-4222-8222-222222222222",
                    "monthly_limit": 250,
                    "is_disabled": False,
                }
            ],
        )
        self.assertEqual(org["expected_models_after_adoption"], [])
        self.assertEqual(org["preserved_per_key_budget_count"], 0)

    def test_rejects_per_key_budgets_as_unsupported(self):
        snapshot = self.snapshot()
        snapshot["keys"][0][2] = 75
        with self.assertRaisesRegex(ValueError, "per-key budget"):
            migration.build_plan([snapshot], self.roster(), 1, datetime.now(UTC))

    def test_rejects_team_cap_below_current_spend(self):
        snapshot = self.snapshot()
        snapshot["team_spend"] = 5001
        with self.assertRaisesRegex(ValueError, "team cap .* below current spend"):
            migration.build_plan([snapshot], self.roster(), 1, datetime.now(UTC))

    def test_rejects_private_member_cap_below_spend(self):
        snapshot = self.snapshot()
        snapshot["members"]["22222222-2222-4222-8222-222222222222"]["spend"] = 251
        with self.assertRaisesRegex(ValueError, "member cap .* below current spend"):
            migration.build_plan([snapshot], self.roster(), 1, datetime.now(UTC))

    def test_rejects_roster_mismatch(self):
        roster = self.roster()
        roster[next(iter(roster))].add("44444444-4444-4444-8444-444444444444")
        with self.assertRaisesRegex(ValueError, "roster mismatch"):
            migration.build_plan([self.snapshot()], roster, 1, datetime.now(UTC))

    def test_rejects_blocked_or_unbudgeted_team(self):
        blocked = self.snapshot()
        blocked["blocked"] = True
        with self.assertRaisesRegex(ValueError, "blocked"):
            migration.build_plan([blocked], self.roster(), 1, datetime.now(UTC))

        unbudgeted = self.snapshot()
        unbudgeted["team_max_budget"] = None
        with self.assertRaisesRegex(ValueError, "positive team max budget"):
            migration.build_plan([unbudgeted], self.roster(), 1, datetime.now(UTC))


class MigrationSqlTests(unittest.TestCase):
    def test_renders_atomic_guarded_sql(self):
        plan = {
            "artifact_type": "litellm_to_openhands_budget_migration_plan",
            "artifact_version": 1,
            "orgs": [{"org_id": "11111111-1111-4111-8111-111111111111"}],
        }
        sql = render_migration_sql.render(plan)
        self.assertIn("BEGIN;", sql)
        self.assertIn("CREATE TABLE rh_backup_org_budget_settings", sql)
        self.assertIn("team organizations missing from migration plan", sql)
        self.assertIn("post-write organization settings differ", sql)
        self.assertIn("COMMIT;", sql)
        self.assertNotIn("__PLAN__", sql)

    def test_rejects_wrong_or_empty_plan(self):
        with self.assertRaisesRegex(ValueError, "artifact type"):
            render_migration_sql.render({"artifact_type": "wrong", "orgs": [{}]})
        with self.assertRaisesRegex(ValueError, "no organizations"):
            render_migration_sql.render(
                {
                    "artifact_type": "litellm_to_openhands_budget_migration_plan",
                    "artifact_version": 1,
                    "orgs": [],
                }
            )


class DatabasePlanTests(unittest.TestCase):
    def values(self):
        org_id = "11111111-1111-4111-8111-111111111111"
        user_id = "22222222-2222-4222-8222-222222222222"
        plan = {
            "orgs": [
                {
                    "org_id": org_id,
                    "monthly_limit": 5000,
                    "reset_day": 1,
                    "cycle_start_at": "2026-10-01T00:00:00+00:00",
                    "cycle_start_spend": 0,
                    "user_cycle_start_spend": {user_id: 0},
                    "overrides": [
                        {
                            "user_id": user_id,
                            "monthly_limit": 250,
                            "is_disabled": False,
                        }
                    ],
                }
            ]
        }
        state = {
            "orgs": [
                {
                    "org_id": org_id,
                    "enabled": True,
                    "monthly_limit": 5000,
                    "reset_day": 1,
                    "default_user_monthly_limit": None,
                    "cycle_start_at": "2026-10-01T00:00:00+00:00",
                    "cycle_start_spend": 0,
                    "user_cycle_start_spend": {user_id: 0},
                    "litellm_last_sync_status": "success",
                    "litellm_last_sync_error": None,
                }
            ],
            "overrides": [
                {
                    "org_id": org_id,
                    "user_id": user_id,
                    "monthly_limit": 250,
                    "is_disabled": False,
                }
            ],
        }
        return plan, state

    def test_exact_database_policy_passes(self):
        plan, state = self.values()
        self.assertTrue(check_database_plan.check(plan, state)["valid"])

    def test_baseline_drift_fails(self):
        plan, state = self.values()
        state["orgs"][0]["cycle_start_spend"] = 10
        report = check_database_plan.check(plan, state)
        self.assertFalse(report["valid"])
        self.assertEqual(
            report["org_mismatches"],
            ["11111111-1111-4111-8111-111111111111"],
        )


class MigrationUpgradeLogTests(unittest.TestCase):
    def artifact(self, org_ids, *, blocking=False, cap_drift=False):
        return {
            "artifact_type": "org_budget_preflight",
            "phase": "post",
            "mode": "strict",
            "error": None,
            "litellm": {"reachable": True, "unreachable_orgs": []},
            "orgs": [
                {
                    "org_id": org_id,
                    "blocking": blocking,
                    "cap_drift": ["drift"] if cap_drift else [],
                    "findings": [{"severity": "blocking"}] if blocking else [],
                    "last_sync": {"status": "success", "error": None},
                    "litellm": {"status": "live", "error": None},
                }
                for org_id in org_ids
            ],
            "summary": {
                "orgs": len(org_ids),
                "blocking": blocking,
                "blocking_orgs": len(org_ids) if blocking else 0,
                "blocking_org_ids": org_ids if blocking else [],
            },
        }

    def write(self, value):
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / "gate.log"
        path.write_text(json.dumps(value) + "\n")
        self.addCleanup(directory.cleanup)
        return path

    def test_exact_migrated_org_set_without_drift_passes(self):
        expected = {"org-a", "org-b"}
        result = check_upgrade_logs.inspect(
            self.write(self.artifact(sorted(expected))), expected
        )
        self.assertTrue(result["valid"])

    def test_wrong_org_set_fails(self):
        result = check_upgrade_logs.inspect(
            self.write(self.artifact(["org-a"])), {"org-a", "org-b"}
        )
        self.assertFalse(result["valid"])

    def test_blocking_or_cap_drift_fails(self):
        blocking = check_upgrade_logs.inspect(
            self.write(self.artifact(["org-a"], blocking=True)), {"org-a"}
        )
        drift = check_upgrade_logs.inspect(
            self.write(self.artifact(["org-a"], cap_drift=True)), {"org-a"}
        )
        self.assertFalse(blocking["valid"])
        self.assertFalse(drift["valid"])


if __name__ == "__main__":
    unittest.main()
