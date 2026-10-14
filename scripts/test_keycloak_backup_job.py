"""Opt-in: run the rendered keycloak-backup Job script against a real PostgreSQL.

KEYCLOAK_BACKUP_TEST_POSTGRES_IMAGE=postgres:16 (the bundled server is 16.x, which
also exercises the transaction_timeout strip for pg_dump 17).
"""

import os
import subprocess
import time
import uuid
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SERVER_IMAGE = os.environ.get("KEYCLOAK_BACKUP_TEST_POSTGRES_IMAGE")
pytestmark = pytest.mark.skipif(
    not SERVER_IMAGE, reason="Set KEYCLOAK_BACKUP_TEST_POSTGRES_IMAGE to run"
)


def render_job() -> dict:
    out = subprocess.run(
        ["helm", "template", "oh", str(ROOT / "charts/openhands"), "--is-upgrade",
         "--set", "keycloak.enabled=true", "-s", "templates/keycloak-backup-job.yaml"],
        check=True, capture_output=True, text=True,
    ).stdout
    container = yaml.safe_load(out)["spec"]["template"]["spec"]["containers"][0]
    env = {e["name"]: e.get("value") for e in container["env"]}
    return {"image": container["image"], "script": container["args"][0],
            "target": env["TARGET_VERSION"]}


@pytest.fixture(scope="module")
def server():
    tag = uuid.uuid4().hex[:8]
    net, name = f"kcb-{tag}", f"kcb-pg-{tag}"
    subprocess.run(["docker", "network", "create", net], check=True, capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", name, "--network", net,
                    "-e", "POSTGRES_PASSWORD=pw", SERVER_IMAGE], check=True, capture_output=True)
    try:
        for _ in range(60):
            if subprocess.run(["docker", "exec", name, "pg_isready", "-U", "postgres", "-h", "127.0.0.1"],
                              capture_output=True).returncode == 0:
                break
            time.sleep(1)
        yield {"net": net, "name": name, "job": render_job()}
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        subprocess.run(["docker", "network", "rm", net], capture_output=True)


def sql(server, db, query):
    return subprocess.run(
        ["docker", "exec", server["name"], "psql", "-U", "postgres", "-d", db,
         "-v", "ON_ERROR_STOP=1", "-tAXc", query],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def databases(server):
    return set(sql(server, "postgres", "SELECT datname FROM pg_database").split())


def keycloak_db(server, *versions):
    """A Keycloak database whose migration_model rows are applied in the given order."""
    db = f"kc_{uuid.uuid4().hex[:8]}"
    sql(server, "postgres", f"CREATE DATABASE {db}")
    sql(server, db, "CREATE TABLE migration_model (id text, version text, update_time bigint);"
                    "CREATE TABLE realm (name text); INSERT INTO realm VALUES ('openhands')")
    for i, v in enumerate(versions):
        sql(server, db, f"INSERT INTO migration_model VALUES ('{i}', '{v}', {1000 + i})")
    return db


def run_job(server, db):
    job = server["job"]
    return subprocess.run(
        ["docker", "run", "--rm", "--network", server["net"], "--tmpfs", "/dump:mode=1777",
         "-e", f"DB_HOST={server['name']}", "-e", "DB_PORT=5432", "-e", f"DB_NAME={db}",
         "-e", "DB_USER=postgres", "-e", "DB_PASS=pw", "-e", "PGSSLMODE=disable",
         "-e", f"TARGET_VERSION={job['target']}",
         "--entrypoint", "/bin/sh", job["image"], "-c", job["script"]],
        capture_output=True, text=True, timeout=180,
    )


def test_copies_the_database_named_after_the_current_schema(server):
    db = keycloak_db(server, "24.0.0", "26.3.0")

    result = run_job(server, db)

    assert result.returncode == 0, result.stdout + result.stderr
    assert sql(server, f"{db}_backup_26_3_0", "SELECT name FROM realm") == "openhands"
    assert f"{db}_backup_26_3_0_incomplete" not in databases(server)


def test_skips_when_the_schema_is_already_at_the_target(server):
    db = keycloak_db(server, server["job"]["target"])

    result = run_job(server, db)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not {d for d in databases(server) if d.startswith(f"{db}_backup")}


def test_keeps_an_existing_backup(server):
    db = keycloak_db(server, "26.3.0")
    sql(server, "postgres", f"CREATE DATABASE {db}_backup_26_3_0")
    sql(server, f"{db}_backup_26_3_0", "CREATE TABLE earlier_attempt ()")

    result = run_job(server, db)

    assert result.returncode == 0, result.stdout + result.stderr
    assert sql(server, f"{db}_backup_26_3_0", "SELECT to_regclass('earlier_attempt') IS NOT NULL") == "t"


def test_replaces_a_leftover_incomplete_copy(server):
    db = keycloak_db(server, "26.3.0")
    sql(server, "postgres", f"CREATE DATABASE {db}_backup_26_3_0_incomplete")

    result = run_job(server, db)

    assert result.returncode == 0, result.stdout + result.stderr
    assert sql(server, f"{db}_backup_26_3_0", "SELECT name FROM realm") == "openhands"
    assert f"{db}_backup_26_3_0_incomplete" not in databases(server)


def test_a_failed_restore_fails_the_job_and_leaves_no_backup(server):
    db = keycloak_db(server, "26.3.0")
    # New databases clone template1, so the restore's CREATE TABLE realm collides.
    sql(server, "template1", "CREATE TABLE realm (name text)")
    try:
        result = run_job(server, db)
    finally:
        sql(server, "template1", "DROP TABLE realm")

    assert result.returncode != 0
    assert "upgrade stopped" in result.stdout
    assert not {d for d in databases(server) if d.startswith(f"{db}_backup")}
