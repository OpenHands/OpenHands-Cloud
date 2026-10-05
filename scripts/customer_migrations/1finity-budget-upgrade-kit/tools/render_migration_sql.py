#!/usr/bin/env python3

import argparse
import json
from pathlib import Path


SQL = r"""\set ON_ERROR_STOP on
BEGIN;

DO $guard$
BEGIN
  IF to_regclass('public.rh_backup_org_budget_settings') IS NOT NULL
     OR to_regclass('public.rh_backup_org_user_budget_override') IS NOT NULL THEN
    RAISE EXCEPTION 'rh_backup_* tables already exist; stop and investigate before retrying';
  END IF;
END
$guard$;

CREATE TEMP TABLE rh_budget_migration_org ON COMMIT DROP AS
SELECT
  (item->>'org_id')::uuid AS org_id,
  (item->>'monthly_limit')::double precision AS monthly_limit,
  (item->>'reset_day')::integer AS reset_day,
  (item->>'cycle_start_at')::timestamptz AS cycle_start_at,
  (item->>'cycle_start_spend')::double precision AS cycle_start_spend,
  (item->'user_cycle_start_spend')::json AS user_cycle_start_spend
FROM jsonb_array_elements(
  $migration_plan$__PLAN__$migration_plan$::jsonb->'orgs'
) AS item;

CREATE UNIQUE INDEX ON rh_budget_migration_org (org_id);

CREATE TEMP TABLE rh_budget_migration_override ON COMMIT DROP AS
SELECT
  (org_item->>'org_id')::uuid AS org_id,
  (override_item->>'user_id')::uuid AS user_id,
  (override_item->>'monthly_limit')::double precision AS monthly_limit,
  (override_item->>'is_disabled')::boolean AS is_disabled
FROM jsonb_array_elements(
  $migration_plan$__PLAN__$migration_plan$::jsonb->'orgs'
) AS org_item
CROSS JOIN LATERAL jsonb_array_elements(org_item->'overrides') AS override_item;

CREATE UNIQUE INDEX ON rh_budget_migration_override (org_id, user_id);

DO $validate$
DECLARE
  missing_from_plan uuid[];
  extra_in_plan uuid[];
  invalid_members text[];
BEGIN
  SELECT array_agg(o.id ORDER BY o.id) INTO missing_from_plan
  FROM org o
  LEFT JOIN "user" personal ON personal.id = o.id
  LEFT JOIN rh_budget_migration_org plan ON plan.org_id = o.id
  WHERE personal.id IS NULL AND plan.org_id IS NULL;

  SELECT array_agg(plan.org_id ORDER BY plan.org_id) INTO extra_in_plan
  FROM rh_budget_migration_org plan
  LEFT JOIN org o ON o.id = plan.org_id
  LEFT JOIN "user" personal ON personal.id = o.id
  WHERE o.id IS NULL OR personal.id IS NOT NULL;

  SELECT array_agg((plan.org_id::text || ':' || plan.user_id::text) ORDER BY plan.org_id, plan.user_id)
    INTO invalid_members
  FROM rh_budget_migration_override plan
  LEFT JOIN org_member member
    ON member.org_id = plan.org_id AND member.user_id = plan.user_id
  WHERE member.user_id IS NULL;

  IF missing_from_plan IS NOT NULL THEN
    RAISE EXCEPTION 'team organizations missing from migration plan: %', missing_from_plan;
  END IF;
  IF extra_in_plan IS NOT NULL THEN
    RAISE EXCEPTION 'migration plan contains non-team organizations: %', extra_in_plan;
  END IF;
  IF invalid_members IS NOT NULL THEN
    RAISE EXCEPTION 'migration overrides contain non-members: %', invalid_members;
  END IF;
  IF EXISTS (
    SELECT 1 FROM rh_budget_migration_org
    WHERE monthly_limit <= 0 OR reset_day NOT BETWEEN 1 AND 28 OR cycle_start_spend <> 0
  ) THEN
    RAISE EXCEPTION 'migration plan contains invalid org settings';
  END IF;
  IF EXISTS (
    SELECT 1 FROM rh_budget_migration_override
    WHERE monthly_limit < 0 OR is_disabled
  ) THEN
    RAISE EXCEPTION 'migration plan contains invalid member overrides';
  END IF;
END
$validate$;

CREATE TABLE rh_backup_org_budget_settings AS
SELECT * FROM org_budget_settings;

CREATE TABLE rh_backup_org_user_budget_override AS
SELECT * FROM org_user_budget_override;

INSERT INTO org_budget_settings (
  org_id,
  enabled,
  monthly_limit,
  reset_day,
  default_user_monthly_limit,
  cycle_start_at,
  cycle_start_spend,
  user_cycle_start_spend,
  litellm_last_sync_at,
  litellm_last_sync_status,
  litellm_last_sync_error,
  created_at,
  updated_at
)
SELECT
  plan.org_id,
  true,
  plan.monthly_limit,
  plan.reset_day,
  NULL,
  plan.cycle_start_at,
  plan.cycle_start_spend,
  plan.user_cycle_start_spend,
  CURRENT_TIMESTAMP,
  'success',
  NULL,
  CURRENT_TIMESTAMP,
  CURRENT_TIMESTAMP
FROM rh_budget_migration_org plan
ON CONFLICT (org_id) DO UPDATE
SET enabled = EXCLUDED.enabled,
    monthly_limit = EXCLUDED.monthly_limit,
    reset_day = EXCLUDED.reset_day,
    default_user_monthly_limit = NULL,
    cycle_start_at = EXCLUDED.cycle_start_at,
    cycle_start_spend = EXCLUDED.cycle_start_spend,
    user_cycle_start_spend = EXCLUDED.user_cycle_start_spend,
    litellm_last_sync_at = CURRENT_TIMESTAMP,
    litellm_last_sync_status = 'success',
    litellm_last_sync_error = NULL,
    updated_at = CURRENT_TIMESTAMP;

DELETE FROM org_user_budget_override
WHERE org_id IN (SELECT org_id FROM rh_budget_migration_org);

INSERT INTO org_user_budget_override (
  org_id,
  user_id,
  monthly_limit,
  is_disabled,
  created_at,
  updated_at
)
SELECT
  org_id,
  user_id,
  monthly_limit,
  is_disabled,
  CURRENT_TIMESTAMP,
  CURRENT_TIMESTAMP
FROM rh_budget_migration_override;

DO $assert$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM rh_budget_migration_org plan
    LEFT JOIN org_budget_settings settings ON settings.org_id = plan.org_id
    WHERE settings.org_id IS NULL
       OR NOT settings.enabled
       OR settings.monthly_limit IS DISTINCT FROM plan.monthly_limit
       OR settings.reset_day IS DISTINCT FROM plan.reset_day
       OR settings.default_user_monthly_limit IS NOT NULL
       OR settings.cycle_start_at IS DISTINCT FROM plan.cycle_start_at
       OR settings.cycle_start_spend IS DISTINCT FROM plan.cycle_start_spend
       OR settings.user_cycle_start_spend::jsonb IS DISTINCT FROM plan.user_cycle_start_spend::jsonb
       OR settings.litellm_last_sync_at IS NULL
       OR settings.litellm_last_sync_status IS DISTINCT FROM 'success'
       OR settings.litellm_last_sync_error IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'post-write organization settings differ from migration plan';
  END IF;

  IF EXISTS (
    (SELECT org_id, user_id, monthly_limit, is_disabled FROM rh_budget_migration_override
     EXCEPT
     SELECT org_id, user_id, monthly_limit, is_disabled
     FROM org_user_budget_override
     WHERE org_id IN (SELECT org_id FROM rh_budget_migration_org))
    UNION ALL
    (SELECT org_id, user_id, monthly_limit, is_disabled
     FROM org_user_budget_override
     WHERE org_id IN (SELECT org_id FROM rh_budget_migration_org)
     EXCEPT
     SELECT org_id, user_id, monthly_limit, is_disabled FROM rh_budget_migration_override)
  ) THEN
    RAISE EXCEPTION 'post-write member overrides differ from migration plan';
  END IF;
END
$assert$;

COMMIT;
"""


def render(plan: dict) -> str:
    if plan.get("artifact_type") != "litellm_to_openhands_budget_migration_plan":
        raise ValueError("unexpected migration plan artifact type")
    if plan.get("artifact_version") != 1:
        raise ValueError("unsupported migration plan version")
    if not plan.get("orgs"):
        raise ValueError("migration plan contains no organizations")
    payload = json.dumps(plan, separators=(",", ":"), sort_keys=True)
    if "$migration_plan$" in payload:
        raise ValueError("migration plan contains the SQL dollar-quote delimiter")
    return SQL.replace("__PLAN__", payload)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with args.plan.open() as stream:
        plan = json.load(stream)
    args.output.write_text(render(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
