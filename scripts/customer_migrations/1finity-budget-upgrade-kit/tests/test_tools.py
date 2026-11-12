#!/usr/bin/env python3

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


compare_snapshots = load_module(
    "compare_snapshots", ROOT / "tools" / "compare_snapshots.py"
)
check_upgrade_logs = load_module(
    "check_upgrade_logs", ROOT / "tools" / "check_upgrade_logs.py"
)
restore_snapshot = load_module(
    "restore_snapshot", ROOT / "tools" / "restore_snapshot.py"
)


class SnapshotComparisonTests(unittest.TestCase):
    def snapshot(self, blocked=False, budget=5000):
        return {
            "org_id": "org-1",
            "team_max_budget": budget,
            "models": ["model-a"],
            "blocked": blocked,
            "member_caps": {"user-1": 250},
            "keys": [["key-1", "token-1", 75]],
        }

    def test_exact_snapshot_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = root / "before"
            after = root / "after"
            before.mkdir()
            after.mkdir()
            value = self.snapshot()
            (before / "org-1.json").write_text(json.dumps(value))
            (after / "org-1.json").write_text(json.dumps(value))
            report = compare_snapshots.compare(before, after, ["org-1"])
            self.assertEqual(report["mismatched_org_ids"], [])
            self.assertEqual(report["blocked_org_ids"], [])

    def test_changed_budget_and_blocked_team_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = root / "before"
            after = root / "after"
            before.mkdir()
            after.mkdir()
            (before / "org-1.json").write_text(json.dumps(self.snapshot()))
            (after / "org-1.json").write_text(
                json.dumps(self.snapshot(blocked=True, budget=1000))
            )
            report = compare_snapshots.compare(before, after, ["org-1"])
            self.assertEqual(report["mismatched_org_ids"], ["org-1"])
            self.assertEqual(report["blocked_org_ids"], ["org-1"])

    def test_changed_model_allowlist_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = root / "before"
            after = root / "after"
            before.mkdir()
            after.mkdir()
            original = self.snapshot()
            changed = self.snapshot()
            changed["models"] = []
            (before / "org-1.json").write_text(json.dumps(original))
            (after / "org-1.json").write_text(json.dumps(changed))
            report = compare_snapshots.compare(before, after, ["org-1"])
            self.assertEqual(report["mismatched_org_ids"], ["org-1"])
            self.assertEqual(report["orgs"][0]["changed_fields"], ["models"])
            self.assertEqual(report["orgs"][0]["models_expected"], ["model-a"])


class ShellHelperTests(unittest.TestCase):
    def test_failures_explicitly_halt_fail_closed(self):
        lib = (ROOT / "lib.sh").read_text()
        self.assertIn("MIGRATION HALTED (FAIL CLOSED)", lib)
        self.assertIn("all migration writers suspended", lib)

    def test_snapshot_exec_does_not_attach_stdin(self):
        lib = (ROOT / "lib.sh").read_text()
        function = lib.split("llsnap() {", 1)[1].split("\n}", 1)[0]
        self.assertNotIn("exec -i", function)

    def test_terminal_or_wrong_image_database_helper_is_recreated(self):
        lib = (ROOT / "lib.sh").read_text()
        function = lib.split("create_external_db_helper() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('phase" == Failed', function)
        self.assertIn('phase" == Succeeded', function)
        self.assertIn("managed by this kit", function)
        self.assertIn('"$existing_image" != "$DB_HELPER_IMAGE"', function)
        self.assertIn('k delete pod "$DB_EXEC_POD" --wait=true', function)
        self.assertIn('if ! k get pod "$DB_EXEC_POD"', function)

    def test_external_helper_uses_immutable_configured_image(self):
        lib = (ROOT / "lib.sh").read_text()
        self.assertIn("DB_HELPER_IMAGE must use an immutable sha256 digest", lib)
        function = lib.split("create_external_db_helper() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('--arg image "$DB_HELPER_IMAGE"', function)
        self.assertIn("image: $image", function)
        self.assertNotIn("image: $source_container.image", function)

    def test_plan_records_postgres_helper_image_identity(self):
        plan = (ROOT / "01-plan.sh").read_text()
        self.assertIn("postgres-client-image-configured.txt", plan)
        self.assertIn("postgres-client-image-resolved.txt", plan)


class UpgradeLogTests(unittest.TestCase):
    def artifact(self, orgs):
        return {
            "artifact_type": "org_budget_preflight",
            "phase": "post_upgrade",
            "mode": "strict",
            "orgs": [
                {
                    "org_id": org_id,
                    "blocking": False,
                    "cap_drift": [],
                    "findings": [],
                    "last_sync": {"status": "success", "error": None},
                    "litellm": {"status": "live", "error": None},
                }
                for org_id in orgs
            ],
            "summary": {
                "orgs": len(orgs),
                "blocking": False,
                "blocking_orgs": 0,
                "blocking_org_ids": [],
            },
            "litellm": {"reachable": True, "unreachable_orgs": []},
            "error": None,
        }

    def test_zero_org_artifact_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gate.log"
            path.write_text("prefix\n" + json.dumps(self.artifact([])) + "\n")
            self.assertTrue(check_upgrade_logs.inspect(path)["valid"])

    def test_nonzero_org_artifact_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gate.log"
            path.write_text(json.dumps(self.artifact(["org-1"])) + "\n")
            self.assertFalse(check_upgrade_logs.inspect(path)["valid"])

    def test_missing_artifact_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gate.log"
            path.write_text("ordinary log output\n")
            self.assertFalse(check_upgrade_logs.inspect(path)["valid"])


class RestoreOperationTests(unittest.TestCase):
    def test_builds_team_member_and_key_updates(self):
        snapshot = {
            "org_id": "org-1",
            "team_max_budget": 5000,
            "models": ["model-a"],
            "blocked": False,
            "member_caps": {"user-1": 250},
            "keys": [["key-1", "token-1", 75]],
        }
        operations = restore_snapshot.build_operations(snapshot, "org-1")
        self.assertEqual(
            [path for path, _ in operations],
            ["/team/update", "/team/member_update", "/key/update"],
        )
        self.assertEqual(operations[2][1], {"key": "token-1", "max_budget": 75})

    def test_rejects_wrong_org(self):
        snapshot = {
            "org_id": "org-2",
            "team_max_budget": 5000,
            "models": [],
            "blocked": False,
            "member_caps": {},
            "keys": [],
        }
        with self.assertRaises(ValueError):
            restore_snapshot.build_operations(snapshot, "org-1")

    def test_preserves_shared_member_semantics(self):
        snapshot = {
            "org_id": "org-1",
            "team_max_budget": 5000,
            "models": [],
            "blocked": False,
            "members": {
                "private-user": {
                    "max_budget": 250,
                    "uses_shared_budget": False,
                },
                "shared-user": {
                    "max_budget": 5000,
                    "uses_shared_budget": True,
                },
            },
            "member_caps": {"private-user": 250, "shared-user": 5000},
            "keys": [],
        }
        operations = restore_snapshot.build_operations(snapshot, "org-1")
        member_payloads = [
            payload for path, payload in operations if path == "/team/member_update"
        ]
        self.assertEqual(
            member_payloads,
            [
                {
                    "team_id": "org-1",
                    "user_id": "private-user",
                    "max_budget_in_team": 250,
                },
                {
                    "team_id": "org-1",
                    "user_id": "shared-user",
                    "max_budget_in_team": None,
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
