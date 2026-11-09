"""Exercise rendered diagnostics against PostgreSQL, including older schemas."""

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def collectors():
    rendered = subprocess.check_output(
        [
            "helm",
            "template",
            "openhands",
            "charts/openhands",
            "--set",
            "automation.enabled=true",
            "--show-only",
            "templates/troubleshoot/secrets.yaml",
        ],
        cwd=ROOT,
        text=True,
    )
    secret = next(
        doc
        for doc in yaml.safe_load_all(rendered)
        if "support-bundle-spec" in doc.get("stringData", {})
    )
    return yaml.safe_load(secret["stringData"]["support-bundle-spec"])["spec"][
        "collectors"
    ]


@pytest.fixture(scope="module")
def collector(collectors):
    return next(
        c["exec"]
        for c in collectors
        if c.get("exec", {}).get("collectorName") == "azure-devops-webhook-diagnostics"
    )


def test_rendered_collector_and_related_logs(collector, collectors):
    assert collector["selector"] == ["app=openhands"]
    assert collector["containerName"] == "openhands"
    assert collector["timeout"] == "20s"
    compile(collector["args"][0], "collector", "exec")
    log_names = {c["logs"]["name"] for c in collectors if "logs" in c}
    assert {
        "app/openhands/logs",
        "app/openhands-integrations/logs",
        "app/automation/logs",
        "app/automation-events/logs",
    } <= log_names


@pytest.fixture(scope="module")
def database():
    image = os.environ.get("WEBHOOK_TEST_POSTGRES_IMAGE")
    if not image:
        pytest.skip(
            "Set WEBHOOK_TEST_POSTGRES_IMAGE to run PostgreSQL diagnostics tests"
        )
    import psycopg2

    container = subprocess.check_output(
        [
            "docker",
            "run",
            "--rm",
            "-d",
            "-p",
            "127.0.0.1::5432",
            "-e",
            "POSTGRES_PASSWORD=fixture-password",
            image,
        ],
        text=True,
    ).strip()
    try:
        port = (
            subprocess.check_output(["docker", "port", container, "5432"], text=True)
            .strip()
            .rsplit(":", 1)[1]
        )
        for _ in range(60):
            try:
                conn = psycopg2.connect(
                    host="127.0.0.1",
                    port=port,
                    user="postgres",
                    password="fixture-password",
                    dbname="postgres",
                )
                break
            except psycopg2.OperationalError:
                time.sleep(0.5)
        else:
            pytest.fail("PostgreSQL did not become ready")
        conn.autocommit = True
        yield (
            conn,
            {
                "DB_HOST": "127.0.0.1",
                "DB_PORT": port,
                "DB_USER": "postgres",
                "DB_NAME": "postgres",
                "DB_PASS": "fixture-password",
                "DB_SSL_MODE": "disable",
            },
        )
        conn.close()
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container], check=True, capture_output=True
        )


@pytest.fixture
def db(database):
    conn, env = database
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        cur.execute("""
            CREATE TABLE org (id uuid, name text, is_default boolean);
            CREATE TABLE "user" (id uuid);
            CREATE TABLE org_git_claim (git_organization text, org_id uuid, claimed_at timestamptz, provider text);
            CREATE TABLE azure_devops_webhook (
                organization text, installed_by_user_id text, installed_at timestamptz,
                last_verified_at timestamptz, pr_subscription_id text, work_item_subscription_id text,
                token text DEFAULT 'must-not-be-exported');
            INSERT INTO org VALUES ('00000000-0000-0000-0000-000000000001', 'Team', true);
            INSERT INTO org VALUES ('00000000-0000-0000-0000-000000000002', 'Personal', false);
            INSERT INTO "user" VALUES ('00000000-0000-0000-0000-000000000002');
            INSERT INTO org_git_claim VALUES ('test-ado', '00000000-0000-0000-0000-000000000001', now(), 'azure_devops');
            INSERT INTO org_git_claim VALUES ('unrelated', '00000000-0000-0000-0000-000000000001', now(), 'github');
            INSERT INTO azure_devops_webhook (organization, installed_by_user_id, installed_at,
                last_verified_at, pr_subscription_id, work_item_subscription_id)
                VALUES ('test-ado', 'installer', now(), now(), 'pr-hook', 'work-hook');
        """)
    return conn, env


def collect(collector, env, **config):
    result = subprocess.run(
        [sys.executable, "-c", collector["args"][0]],
        env={**env, **config},
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    assert not result.stderr
    return json.loads(result.stdout), result.stdout


def test_saved_state_is_not_reported_as_live_verification(collector, db):
    _, env = db
    data, raw = collect(
        collector,
        env,
        WEB_HOST="app.example.test",
        AUTOMATION_EVENT_FORWARDING_ENABLED="true",
        AZURE_DEVOPS_WEBHOOK_SECRET="fixture-webhook-secret",
        AUTOMATION_SERVICE_URL="https://user:fixture-password@automation.test/api?key=hidden#hidden",
    )
    assert data["azure_subscription_verification"] == "not_performed"
    assert data["azure_to_openhands_delivery_verification"] == "not_performed"
    assert (
        data["config"]["webhook_url"]
        == "https://app.example.test/integration/azure-devops/events"
    )
    assert data["config"]["automation_service_url"] == "https://automation.test/api"
    assert data["config"]["webhook_secret_set"] is True
    assert data["installation_records"]["rows"][0]["pr_subscription_id"] == "pr-hook"
    assert len(data["organization_claims"]["rows"]) == 1
    assert [r["name"] for r in data["team_organizations"]["rows"]] == ["Team"]
    for secret in (
        "fixture-webhook-secret",
        "fixture-password",
        "must-not-be-exported",
        "hidden",
    ):
        assert secret not in raw


def test_older_schema_is_unavailable_not_an_empty_install(collector, db):
    conn, env = db
    with conn.cursor() as cur:
        cur.execute("DROP TABLE azure_devops_webhook")
    data, _ = collect(collector, env)
    assert data["installation_records"] == {
        "status": "unavailable",
        "error_type": "UndefinedTable",
    }
    assert data["organization_claims"]["status"] == "collected"
    assert data["team_organizations"]["status"] == "collected"


def test_row_limit_is_explicit(collector, db):
    conn, env = db
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO azure_devops_webhook (organization) SELECT 'org-' || n FROM generate_series(1, 501) n"
        )
    data, _ = collect(collector, env)
    assert data["installation_records"]["truncated"] is True
    assert len(data["installation_records"]["rows"]) == 500


def test_database_failure_keeps_config_without_credentials(collector):
    data, raw = collect(
        collector, {}, WEB_HOST="localhost:3000", DB_PASS="private-password"
    )
    assert data["database"] == {"status": "unavailable", "error_type": "KeyError"}
    assert (
        data["config"]["webhook_url"]
        == "http://localhost:3000/integration/azure-devops/events"
    )
    assert "private-password" not in raw


def test_event_patterns_are_collected_without_filter_values(collectors, db):
    conn, _ = db
    script = next(
        c["exec"]["args"][0]
        for c in collectors
        if c.get("exec", {}).get("collectorName") == "automation-db-diagnostics"
    )
    node = next(
        n
        for n in ast.walk(ast.parse(script))
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "queries" for t in n.targets)
    )
    query = next(
        sql
        for label, sql in ast.literal_eval(node.value)
        if label.startswith("automation event triggers")
    )
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE automations (id text, org_id text, enabled boolean, trigger json, deleted_at timestamptz)"
        )
        cur.execute(
            "INSERT INTO automations VALUES ('event', 'team', true, %s, NULL)",
            [
                json.dumps(
                    {
                        "type": "event",
                        "source": "azure_devops",
                        "on": "pr.*",
                        "filter": "private-filter",
                    }
                )
            ],
        )
        cur.execute(query)
        rows = cur.fetchall()
    assert rows == [("event", "team", True, "azure_devops", "pr.*", True)]
    assert "private-filter" not in json.dumps(rows)


def test_recent_schema_exports_retained_install_and_listing_evidence(collector, db):
    conn, env = db
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE azure_devops_webhook_diagnostics (organization text, updated_at timestamptz, last_install_attempt jsonb, last_observation jsonb, private_token text)"
        )
        attempt = {
            "outcome": "failed",
            "stage": "create_work_item_comment",
            "created_subscription_ids": ["pr-new"],
            "azure_http_status": 400,
            "azure_message": "Permission denied",
        }
        observation = {
            "observed_at": "2026-11-09T12:00:00Z",
            "delivery_verified": False,
            "subscriptions": [
                {
                    "id": "pr-old",
                    "event_type": "ms.vss-code.git-pullrequest-comment-event",
                    "matches_callback": True,
                }
            ],
        }
        cur.execute(
            "INSERT INTO azure_devops_webhook_diagnostics VALUES (%s, now(), %s, %s, %s)",
            (
                "test-ado",
                json.dumps(attempt),
                json.dumps(observation),
                "must-not-export-token",
            ),
        )
    data, raw = collect(collector, env)
    row = data["operation_diagnostics"]["rows"][0]
    assert row["last_install_attempt"] == attempt
    assert row["last_observation"] == observation
    assert data["azure_subscription_verification"] == "not_performed"
    assert "must-not-export-token" not in raw


def test_older_app_without_operation_diagnostics_still_collects_other_evidence(
    collector, db
):
    _, env = db
    data, _ = collect(collector, env)
    assert data["operation_diagnostics"] == {
        "status": "unavailable",
        "error_type": "UndefinedTable",
    }
    assert data["installation_records"]["status"] == "collected"
