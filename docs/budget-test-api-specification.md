# Budget Test API Specification

## Overview

This document specifies new API endpoints that enable budget e2e tests to run without direct database access. These endpoints replace direct SQL queries with proper API calls that maintain security, authorization, and audit trails.

## Design Principles

1. **Production endpoints** expose functionality that should exist in production anyway (triggering maintenance, reading state)
2. **Test-mode endpoints** are gated by `TEST_MODE` environment variable and provide testing utilities
3. All endpoints require proper authentication and authorization
4. All operations are auditable and logged
5. Test-mode endpoints are disabled in production

## Authentication & Authorization

All endpoints require:
- **Authentication**: Valid bearer token or session
- **Authorization**: Organization admin role
- **Test-mode endpoints**: Additionally require `TEST_MODE=true` environment variable

## Production Endpoints

These endpoints should be available in all environments as they provide legitimate operational functionality.

### 1. Trigger Budget Maintenance

Manually trigger budget maintenance for an organization. Useful for forcing synchronization after configuration changes or for testing.

```
POST /api/organizations/{org_id}/budgets/maintenance
```

**Request Headers:**
```
Authorization: Bearer <token>
X-Org-Id: <org_id>
```

**Request Body:** None

**Response (202 Accepted):**
```json
{
  "task_id": 12345,
  "status": "PENDING",
  "created_at": "2026-09-30T20:00:00Z"
}
```

**Error Responses:**
- `401 Unauthorized`: Invalid or missing authentication
- `403 Forbidden`: User is not an admin of the organization
- `404 Not Found`: Organization does not exist
- `409 Conflict`: Maintenance task already running for this organization

**Implementation Notes:**
- Creates a maintenance task in the `maintenance_tasks` table
- Uses the same processor as scheduled maintenance: `OrgBudgetMaintenanceProcessor`
- Sets `delay=0` for immediate execution
- Returns task ID for polling

---

### 2. Get Maintenance Task Status

Poll the status of a budget maintenance task.

```
GET /api/organizations/{org_id}/budgets/maintenance/{task_id}
```

**Request Headers:**
```
Authorization: Bearer <token>
X-Org-Id: <org_id>
```

**Response (200 OK):**
```json
{
  "id": 12345,
  "status": "COMPLETED",
  "created_at": "2026-09-30T20:00:00Z",
  "updated_at": "2026-09-30T20:00:05Z",
  "info": {
    "org_id": "550e8400-e29b-41d4-a716-446655440000",
    "members_synced": 15,
    "spend_updated": 123.45,
    "error_count": 0
  }
}
```

**Status Values:**
- `PENDING`: Task created but not yet started
- `WORKING`: Task currently executing
- `COMPLETED`: Task finished successfully
- `ERROR`: Task failed

**Error Responses:**
- `401 Unauthorized`: Invalid or missing authentication
- `403 Forbidden`: User is not an admin of the organization
- `404 Not Found`: Task not found or belongs to different organization

**Implementation Notes:**
- Reads from `maintenance_tasks` table
- Filters by organization to prevent cross-org task access
- Returns full task info for debugging

---

### 3. Get Budget Cycle State

Read the current budget cycle state for an organization.

```
GET /api/organizations/{org_id}/budgets/cycle-state
```

**Request Headers:**
```
Authorization: Bearer <token>
X-Org-Id: <org_id>
```

**Response (200 OK):**
```json
{
  "cycle_start_at": "2026-09-01T00:00:00Z",
  "cycle_end_at": "2026-10-01T00:00:00Z",
  "cycle_start_spend": 0.0,
  "user_cycle_start_spend": {
    "user-id-1": 10.50,
    "user-id-2": 25.00
  },
  "litellm_last_sync_at": "2026-09-30T19:55:00Z",
  "litellm_last_sync_status": "success",
  "litellm_last_sync_error": null,
  "litellm_last_spend_snapshot_at": "2026-09-30T19:55:00Z",
  "litellm_last_team_spend": 123.45,
  "litellm_last_member_spend": {
    "user-id-1": 45.50,
    "user-id-2": 77.95
  },
  "litellm_known_member_ids": ["user-id-1", "user-id-2"]
}
```

**Error Responses:**
- `401 Unauthorized`: Invalid or missing authentication
- `403 Forbidden`: User is not an admin of the organization
- `404 Not Found`: Organization does not exist or budgets not enabled

**Implementation Notes:**
- Reads from `org_budget_settings` table
- Returns current cycle state for verification
- Does not modify any data

---

## Test-Mode Endpoints

These endpoints are only available when `TEST_MODE=true` environment variable is set. They provide testing utilities that should never be exposed in production.

### Security Model

Test-mode endpoints require **all** of the following:
1. Valid authentication (bearer token or session)
2. Organization admin authorization
3. `TEST_MODE=true` environment variable

If `TEST_MODE` is not true, these endpoints return `404 Not Found` (not `403 Forbidden`) to avoid leaking their existence.

### 4. Make Cycle Stale (Test Only)

Force the budget cycle to be old (40 days in the past) to test cycle rollover behavior.

```
POST /api/organizations/{org_id}/budgets/test/make-cycle-stale
```

**Request Headers:**
```
Authorization: Bearer <token>
X-Org-Id: <org_id>
```

**Request Body:** None

**Response (200 OK):**
```json
{
  "success": true,
  "previous_cycle_start": "2026-09-01T00:00:00Z",
  "new_cycle_start": "2026-08-21T00:00:00Z",
  "days_moved": 40
}
```

**Error Responses:**
- `401 Unauthorized`: Invalid or missing authentication
- `403 Forbidden`: User is not an admin of the organization
- `404 Not Found`: TEST_MODE is false, org not found, or budgets not enabled

**Implementation Notes:**
```sql
UPDATE org_budget_settings
   SET cycle_start_at = CURRENT_TIMESTAMP - INTERVAL '40 days'
 WHERE org_id = $1
```
- Logs operation to audit trail
- Only works in test mode
- Used to test cycle rollover without waiting

---

### 5. Restore Cycle State (Test Only)

Restore the budget cycle to a previous state. Used to clean up after destructive tests.

```
POST /api/organizations/{org_id}/budgets/test/restore-cycle-state
```

**Request Headers:**
```
Authorization: Bearer <token>
X-Org-Id: <org_id>
Content-Type: application/json
```

**Request Body:**
```json
{
  "cycle_start_at": "2026-09-01T00:00:00Z",
  "cycle_start_spend": 0.0,
  "user_cycle_start_spend": {
    "user-id-1": 10.50
  },
  "litellm_last_sync_at": "2026-09-30T19:55:00Z",
  "litellm_last_sync_status": "success",
  "litellm_last_sync_error": null,
  "litellm_last_spend_snapshot_at": "2026-09-30T19:55:00Z",
  "litellm_last_team_spend": 123.45,
  "litellm_last_member_spend": {
    "user-id-1": 45.50
  },
  "litellm_known_member_ids": ["user-id-1"]
}
```

**Response (200 OK):**
```json
{
  "success": true,
  "restored_at": "2026-09-30T20:00:00Z"
}
```

**Error Responses:**
- `400 Bad Request`: Invalid cycle state data
- `401 Unauthorized`: Invalid or missing authentication
- `403 Forbidden`: User is not an admin of the organization
- `404 Not Found`: TEST_MODE is false, org not found, or budgets not enabled

**Implementation Notes:**
```sql
UPDATE org_budget_settings
   SET cycle_start_at = $2,
       cycle_start_spend = $3,
       user_cycle_start_spend = $4,
       litellm_last_sync_at = $5,
       litellm_last_sync_status = $6,
       litellm_last_sync_error = $7,
       litellm_last_spend_snapshot_at = $8,
       litellm_last_team_spend = $9,
       litellm_last_member_spend = $10,
       litellm_known_member_ids = $11
 WHERE org_id = $1
```
- Logs operation to audit trail
- Only works in test mode
- Used to restore state after destructive tests

---

### 6. Seed Test Members (Test Only)

Create throwaway organization members for testing pagination and other member-related functionality.

```
POST /api/organizations/{org_id}/test/seed-members
```

**Request Headers:**
```
Authorization: Bearer <token>
X-Org-Id: <org_id>
Content-Type: application/json
```

**Request Body:**
```json
{
  "count": 5
}
```

**Response (200 OK):**
```json
{
  "user_ids": [
    "e2e-test-user-1-uuid",
    "e2e-test-user-2-uuid",
    "e2e-test-user-3-uuid",
    "e2e-test-user-4-uuid",
    "e2e-test-user-5-uuid"
  ],
  "created_at": "2026-09-30T20:00:00Z"
}
```

**Error Responses:**
- `400 Bad Request`: Invalid count (must be 1-100)
- `401 Unauthorized`: Invalid or missing authentication
- `403 Forbidden`: User is not an admin of the organization
- `404 Not Found`: TEST_MODE is false or org not found

**Implementation Notes:**
- Creates users in `user` table with email like `e2e-pagination-{uuid}@example.invalid`
- Creates org_member entries with `member` role
- Users exist only in OpenHands DB, not in LiteLLM (spend reads as null)
- Used for testing pagination, member listing, etc.
- Must be removed before running budget maintenance (would report as missing from LiteLLM)

---

### 7. Remove Test Members (Test Only)

Remove test members created by `seed-members` endpoint.

```
DELETE /api/organizations/{org_id}/test/members
```

**Request Headers:**
```
Authorization: Bearer <token>
X-Org-Id: <org_id>
Content-Type: application/json
```

**Request Body:**
```json
{
  "user_ids": [
    "e2e-test-user-1-uuid",
    "e2e-test-user-2-uuid"
  ]
}
```

**Response (200 OK):**
```json
{
  "deleted_count": 2,
  "deleted_at": "2026-09-30T20:00:00Z"
}
```

**Error Responses:**
- `400 Bad Request`: Invalid user_ids array
- `401 Unauthorized`: Invalid or missing authentication
- `403 Forbidden`: User is not an admin of the organization
- `404 Not Found`: TEST_MODE is false or org not found

**Implementation Notes:**
```sql
DELETE FROM org_member WHERE org_id = $1 AND user_id = ANY($2);
DELETE FROM "user" WHERE id = ANY($2);
```
- Only deletes users from the specified organization
- Logs operation to audit trail
- Used to clean up after pagination tests

---

## Implementation Checklist

### Backend (OpenHands/OpenHands repository)

- [ ] Add `TEST_MODE` environment variable to settings
- [ ] Create new budget test endpoints router
- [ ] Implement production endpoints:
  - [ ] POST `/api/organizations/{org_id}/budgets/maintenance`
  - [ ] GET `/api/organizations/{org_id}/budgets/maintenance/{task_id}`
  - [ ] GET `/api/organizations/{org_id}/budgets/cycle-state`
- [ ] Implement test-mode endpoints:
  - [ ] POST `/api/organizations/{org_id}/budgets/test/make-cycle-stale`
  - [ ] POST `/api/organizations/{org_id}/budgets/test/restore-cycle-state`
  - [ ] POST `/api/organizations/{org_id}/test/seed-members`
  - [ ] DELETE `/api/organizations/{org_id}/test/members`
- [ ] Add authorization checks (admin role required)
- [ ] Add TEST_MODE checks for test endpoints
- [ ] Add audit logging for all operations
- [ ] Write unit tests for each endpoint
- [ ] Write integration tests for each endpoint

### e2e Tests (OpenHands/OpenHands-Cloud repository)

- [ ] Update `BudgetApi` class to add new endpoint methods
- [ ] Update `009-budgets.spec.ts` to use API instead of database:
  - [ ] Replace `database.runMaintenance()` with API call
  - [ ] Replace `database.getCycleState()` with API call
  - [ ] Replace `database.makeCycleStale()` with API call
  - [ ] Replace `database.restoreCycleState()` with API call
  - [ ] Replace `database.seedMemberFinancialListingMembers()` with API call
  - [ ] Replace `database.removeMemberFinancialListingMembers()` with API call
- [ ] Update configuration to remove `BUDGET_E2E_DATABASE_URL` requirement
- [ ] Update README documentation
- [ ] Add `TEST_MODE=true` to beta environment configuration

### Deployment

- [ ] Add `TEST_MODE=true` to beta helm values
- [ ] Add `TEST_MODE=true` to staging helm values
- [ ] Ensure `TEST_MODE=false` (or unset) in production
- [ ] Update environment variable documentation

---

## Security Considerations

### Production Endpoints

**Maintenance Trigger:**
- Risk: Could be abused to spam maintenance tasks
- Mitigation: Rate limit per org (max 1 per 5 minutes)
- Mitigation: Reject if maintenance already running
- Mitigation: Admin-only access

**Cycle State Read:**
- Risk: Exposes financial data
- Mitigation: Admin-only access
- Mitigation: Standard org authorization checks

### Test-Mode Endpoints

**Overall Security:**
- Risk: Could be abused to manipulate production data
- Mitigation: **Disabled in production** (TEST_MODE=false)
- Mitigation: Return 404 (not 403) when disabled to avoid leaking existence
- Mitigation: Admin-only access
- Mitigation: Comprehensive audit logging

**State Manipulation:**
- Risk: Could corrupt budget state if misused
- Mitigation: Only in test environments
- Mitigation: Logged operations for debugging
- Mitigation: Document proper cleanup procedures

**Test Member Creation:**
- Risk: Could spam database with fake users
- Mitigation: Only in test environments
- Mitigation: Limit count per request (max 100)
- Mitigation: Use distinctive email pattern for easy cleanup

---

## Migration Strategy

### Phase 1: Add API Endpoints (Backend PR)
1. Implement all endpoints in backend
2. Add TEST_MODE environment variable
3. Deploy to development environment
4. Manual testing of each endpoint

### Phase 2: Update Tests (Cloud PR)
1. Update `BudgetApi` class with new methods
2. Update `009-budgets.spec.ts` to use APIs
3. Keep database fallback initially (dual mode)
4. Test in staging with both DB and API approaches

### Phase 3: Enable in Beta
1. Deploy backend with endpoints to beta
2. Set `TEST_MODE=true` in beta helm values
3. Deploy updated tests to beta
4. Verify tests run successfully
5. Monitor ReportPortal for results

### Phase 4: Remove Database Dependency
1. Remove database fallback code from tests
2. Remove `BUDGET_E2E_DATABASE_URL` from configuration
3. Update documentation
4. Clean up `BudgetDatabase` class (keep for reference)

---

## Rollback Plan

If issues are discovered:

1. **Backend issues**: Keep endpoints, fix bugs, redeploy
2. **Test issues**: Temporarily revert to database approach in tests
3. **Beta issues**: Set `TEST_MODE=false` to disable test endpoints
4. **Critical issues**: Revert entire PR and investigate offline

---

## Future Enhancements

1. **Maintenance scheduling**: Allow specifying delay for maintenance tasks
2. **Batch operations**: Trigger maintenance for multiple orgs
3. **Webhooks**: Notify when maintenance completes
4. **Test fixtures**: Pre-defined budget states for common scenarios
5. **Snapshot/restore**: Full budget state backup and restore

---

## Questions & Answers

**Q: Why not just expose the database?**
A: Security risk. Replicated VMs don't expose PostgreSQL for good reason. API endpoints provide proper authentication, authorization, and audit trails.

**Q: Why separate production and test-mode endpoints?**
A: Production endpoints provide legitimate operational functionality. Test endpoints manipulate state in ways that are only safe in test environments.

**Q: Can test endpoints be abused?**
A: No. They're disabled in production (TEST_MODE=false), require admin auth, return 404 when disabled (not 403), and all operations are logged.

**Q: What if TEST_MODE is accidentally enabled in production?**
A: Still safe - requires admin auth, operations are logged, and test endpoints manipulate budget state (not payments or actual charges).

**Q: How do we prevent test-mode endpoints from being called in production?**
A: Multiple layers: TEST_MODE environment check, admin authorization, audit logging, and infrastructure monitoring for unexpected API usage.

---

## References

- Budget E2E Test Analysis: `/workspace/budget-e2e-analysis.md`
- Database Value Analysis: `/workspace/DATABASE_VALUE_ANALYSIS.md`
- Current Test Implementation: `e2e_tests/tests/009-budgets.spec.ts`
- Current Database Class: `e2e_tests/utils/budget-database.ts`
