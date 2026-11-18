# Budget Test Migration Guide

## Overview

This guide shows how to migrate budget e2e tests from direct database access (`BudgetDatabase`) to API access (`BudgetApiClient`) once the backend endpoints are implemented.

## Prerequisites

- Backend API endpoints must be deployed (see `budget-test-api-specification.md`)
- `TEST_MODE=true` must be set in test environment
- Tests must have valid authentication (already required)

## Migration Steps

### Step 1: Update Imports

**Before:**
```typescript
import { BudgetDatabase } from "../utils/budget-database";
```

**After:**
```typescript
import { BudgetApiClient, createBudgetApiClient } from "../utils/budget-api-client";
// Keep BudgetDatabase as optional fallback during migration
import { BudgetDatabase } from "../utils/budget-database";
```

### Step 2: Update Client Initialization

**Before:**
```typescript
let database: BudgetDatabase | undefined;

test.beforeAll(async ({ browser }, testInfo) => {
  if (!config.databaseUrl) {
    throw new Error("BUDGET_E2E_DATABASE_URL is required");
  }
  database = new BudgetDatabase(config.databaseUrl);
});
```

**After:**
```typescript
let apiClient: BudgetApiClient | undefined;
let database: BudgetDatabase | undefined; // Optional fallback during migration

test.beforeAll(async ({ browser, request }, testInfo) => {
  // Prefer API client (no database access needed)
  apiClient = createBudgetApiClient(request, config.orgId);
  
  // Optional: Keep database as fallback during migration
  if (config.databaseUrl) {
    database = new BudgetDatabase(config.databaseUrl);
  }
});
```

### Step 3: Update Method Calls

#### Getting Cycle State

**Before:**
```typescript
const cycleState = await database.getCycleState(config.orgId);
```

**After:**
```typescript
const cycleState = await apiClient.getCycleState();
```

#### Running Maintenance

**Before:**
```typescript
const maintenanceResult = await database.runMaintenance(
  config.orgId,
  config.syncTimeoutMs,
  config.pollIntervalMs,
);
```

**After:**
```typescript
const maintenanceResult = await apiClient.runMaintenance(
  config.syncTimeoutMs,
  config.pollIntervalMs,
);
```

#### Making Cycle Stale

**Before:**
```typescript
await database.makeCycleStale(config.orgId);
```

**After:**
```typescript
await apiClient.makeCycleStale();
```

#### Restoring Cycle State

**Before:**
```typescript
await database.restoreCycleState(config.orgId, originalCycleState);
```

**After:**
```typescript
await apiClient.restoreCycleState(originalCycleState);
```

#### Seeding Test Members

**Before:**
```typescript
const userIds = await database.seedMemberFinancialListingMembers(
  config.orgId,
  3,
);
```

**After:**
```typescript
const userIds = await apiClient.seedMemberFinancialListingMembers(3);
```

#### Removing Test Members

**Before:**
```typescript
await database.removeMemberFinancialListingMembers(config.orgId, userIds);
```

**After:**
```typescript
await apiClient.removeMemberFinancialListingMembers(userIds);
```

### Step 4: Update Cleanup

**Before:**
```typescript
test.afterAll(async () => {
  if (database) {
    await database.cleanupCreatedTasks();
  }
});
```

**After:**
```typescript
test.afterAll(async () => {
  // No cleanup needed - API handles task lifecycle
  // Test members should be cleaned up in test body
});
```

## Complete Example

### Before (Database Access)

```typescript
import { test, expect } from "@playwright/test";
import { BudgetDatabase } from "../utils/budget-database";
import { loadBudgetE2EConfig } from "../utils/budgets";

const config = loadBudgetE2EConfig();

test.describe("budget maintenance @budgets", () => {
  let database: BudgetDatabase | undefined;

  test.beforeEach(({ page: _page }, testInfo) => {
    test.skip(!config.databaseUrl, "Requires database access");
  });

  test.beforeAll(async () => {
    database = new BudgetDatabase(config.databaseUrl!);
  });

  test("cycle rollover creates new cycle", async () => {
    // Get original state
    const originalCycle = await database!.getCycleState(config.orgId);

    try {
      // Make cycle stale
      await database!.makeCycleStale(config.orgId);

      // Trigger maintenance
      await database!.runMaintenance(
        config.orgId,
        config.syncTimeoutMs,
        config.pollIntervalMs,
      );

      // Verify new cycle created
      const newCycle = await database!.getCycleState(config.orgId);
      expect(newCycle.cycleStartAt).not.toBe(originalCycle.cycleStartAt);
    } finally {
      // Restore original state
      await database!.restoreCycleState(config.orgId, originalCycle);
    }
  });

  test.afterAll(async () => {
    if (database) {
      await database.cleanupCreatedTasks();
    }
  });
});
```

### After (API Access)

```typescript
import { test, expect } from "@playwright/test";
import { BudgetApiClient, createBudgetApiClient } from "../utils/budget-api-client";
import { loadBudgetE2EConfig } from "../utils/budgets";

const config = loadBudgetE2EConfig();

test.describe("budget maintenance @budgets", () => {
  let apiClient: BudgetApiClient | undefined;

  test.beforeEach(({ page: _page }, testInfo) => {
    // No database requirement - just needs authentication
    test.skip(
      !config.orgId,
      "Requires BUDGET_E2E_ORG_ID for test organization",
    );
  });

  test.beforeAll(async ({ browser }) => {
    // Create authenticated request context
    const context = await browser.newContext({
      storageState: authReturningFile,
    });
    const page = await context.newPage();
    
    // Create API client using authenticated context
    apiClient = createBudgetApiClient(page.request, config.orgId);
  });

  test("cycle rollover creates new cycle", async () => {
    // Get original state
    const originalCycle = await apiClient!.getCycleState();

    try {
      // Make cycle stale
      await apiClient!.makeCycleStale();

      // Trigger maintenance
      await apiClient!.runMaintenance(
        config.syncTimeoutMs,
        config.pollIntervalMs,
      );

      // Verify new cycle created
      const newCycle = await apiClient!.getCycleState();
      expect(newCycle.cycleStartAt).not.toBe(originalCycle.cycleStartAt);
    } finally {
      // Restore original state
      await apiClient!.restoreCycleState(originalCycle);
    }
  });

  // No afterAll cleanup needed
});
```

## Hybrid Approach (During Migration)

During migration, you can support both database and API access to enable gradual rollout:

```typescript
import { test, expect } from "@playwright/test";
import { BudgetApiClient, createBudgetApiClient } from "../utils/budget-api-client";
import { BudgetDatabase } from "../utils/budget-database";
import { loadBudgetE2EConfig } from "../utils/budgets";

const config = loadBudgetE2EConfig();

test.describe("budget maintenance @budgets", () => {
  let apiClient: BudgetApiClient | undefined;
  let database: BudgetDatabase | undefined;

  test.beforeAll(async ({ browser }) => {
    // Prefer API, fallback to database
    if (config.useApiEndpoints) {
      const context = await browser.newContext({
        storageState: authReturningFile,
      });
      const page = await context.newPage();
      apiClient = createBudgetApiClient(page.request, config.orgId);
    } else if (config.databaseUrl) {
      database = new BudgetDatabase(config.databaseUrl);
    } else {
      throw new Error("Neither API endpoints nor database access available");
    }
  });

  test("cycle rollover creates new cycle", async () => {
    // Use whichever client is available
    const client = apiClient || database!;

    const originalCycle = await (apiClient
      ? apiClient.getCycleState()
      : database!.getCycleState(config.orgId));

    // ... rest of test
  });
});
```

## Configuration Updates

### Environment Variables

**Remove:**
```bash
BUDGET_E2E_DATABASE_URL=postgresql://...
```

**Add:**
```bash
TEST_MODE=true  # Required for test-mode endpoints
```

**Keep:**
```bash
BUDGET_E2E_ORG_ID=<uuid>  # Still required
# All other BUDGET_E2E_* variables remain the same
```

### loadBudgetE2EConfig Changes

Update `utils/budgets.ts`:

```typescript
export function loadBudgetE2EConfig(): BudgetE2EConfig {
  // ... existing code ...

  return {
    // ... existing fields ...
    
    // Database URL becomes optional
    databaseUrl: process.env.BUDGET_E2E_DATABASE_URL?.trim(),
    
    // Add flag to control API vs DB usage
    useApiEndpoints: process.env.BUDGET_E2E_USE_API?.toLowerCase() === "true" ||
                     !process.env.BUDGET_E2E_DATABASE_URL, // Default to API if no DB
  };
}
```

## Testing the Migration

### 1. Test API Endpoints in Isolation

```bash
# Set TEST_MODE in backend
export TEST_MODE=true

# Test cycle state endpoint
curl -H "Authorization: Bearer $TOKEN" \
     -H "X-Org-Id: $ORG_ID" \
     https://beta.../api/organizations/$ORG_ID/budgets/cycle-state

# Test maintenance trigger
curl -X POST \
     -H "Authorization: Bearer $TOKEN" \
     -H "X-Org-Id: $ORG_ID" \
     https://beta.../api/organizations/$ORG_ID/budgets/maintenance
```

### 2. Test with Dual Mode

```bash
# Run tests with both API and database
BUDGET_E2E_USE_API=true \
BUDGET_E2E_DATABASE_URL=postgresql://... \
npm run test:budgets

# Verify both produce same results
```

### 3. Test API-Only Mode

```bash
# Run tests with API only (no database)
BUDGET_E2E_USE_API=true \
TEST_MODE=true \
npm run test:budgets

# Should pass without database access
```

## Rollback Plan

If API endpoints have issues:

1. **Keep database fallback**: Set `BUDGET_E2E_USE_API=false`
2. **Fix endpoints**: Deploy fixes without changing tests
3. **Re-enable API**: Set `BUDGET_E2E_USE_API=true`

## Common Issues

### Issue: 404 on test endpoints

**Cause**: `TEST_MODE=false` or not set

**Solution**: Set `TEST_MODE=true` in environment

### Issue: 403 Forbidden

**Cause**: User is not admin of test organization

**Solution**: Verify test user has admin role

### Issue: Tests timeout waiting for maintenance

**Cause**: Maintenance task not completing

**Debug**:
```typescript
// Add logging
const taskId = await apiClient.triggerMaintenance();
console.log(`Started maintenance task ${taskId}`);

const task = await apiClient.getMaintenanceTask(taskId);
console.log(`Task status: ${task.status}`, task.info);
```

### Issue: Cycle state mismatch after restore

**Cause**: Maintenance ran between restore and verification

**Solution**: Ensure maintenance is not scheduled during tests, or add delay

## Benefits After Migration

✅ **No database credentials needed**
- Tests run on beta, PR checks, local dev
- No self-hosted GitHub runners
- Reduced security risk

✅ **Better security**
- Proper authentication and authorization
- Audit trails for all operations
- TEST_MODE gating for dangerous operations

✅ **Easier maintenance**
- API contract instead of SQL queries
- Backend can change schema without breaking tests
- Centralized business logic

✅ **Better test isolation**
- Tests use APIs like real users
- Test through full stack
- Catch integration issues

## Timeline

1. **Week 1**: Implement backend endpoints
2. **Week 2**: Deploy to dev, test manually
3. **Week 3**: Update tests with dual mode (API + DB)
4. **Week 4**: Deploy to beta with TEST_MODE=true
5. **Week 5**: Remove database fallback
6. **Week 6**: Deploy to all test environments

## Questions?

See:
- API Specification: `docs/budget-test-api-specification.md`
- Database Value Analysis: `/workspace/DATABASE_VALUE_ANALYSIS.md`
- Original Investigation: `/workspace/budget-e2e-analysis.md`
