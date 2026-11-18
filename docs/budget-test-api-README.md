# Budget Test API Endpoints - Complete Solution

## Overview

This solution adds API endpoints that replace direct database access for budget e2e tests, enabling tests to run on all environments including beta (Replicated VMs) without requiring PostgreSQL exposure.

## The Problem

Budget e2e tests currently require:
- ❌ Direct PostgreSQL database connection (`BUDGET_E2E_DATABASE_URL`)
- ❌ Self-hosted GitHub runners with network access to private databases
- ❌ Security risk of exposing database or managing credentials in CI

**Result**: Budget tests don't run on beta, PR checks, or most environments.

## The Solution

Add API endpoints that provide the same functionality as direct database access:

### Production Endpoints (Always Available)
1. **POST `/budgets/maintenance`** - Trigger budget maintenance task
2. **GET `/budgets/maintenance/{task_id}`** - Poll maintenance status
3. **GET `/budgets/cycle-state`** - Read cycle state for verification

### Test-Mode Endpoints (TEST_MODE=true Only)
4. **POST `/budgets/test/make-cycle-stale`** - Force old cycle for rollover testing
5. **POST `/budgets/test/restore-cycle-state`** - Restore state after tests
6. **POST `/test/seed-members`** - Create test users for pagination testing
7. **DELETE `/test/members`** - Remove test users

## Documents in This Package

### 1. API Specification (`budget-test-api-specification.md`)
Complete API specification including:
- Endpoint URLs and HTTP methods
- Request/response formats
- Authentication and authorization requirements
- Security model for test-mode endpoints
- Implementation notes with SQL queries
- Error responses and status codes

**Start here** to understand what endpoints to build.

### 2. Implementation Example (`budget-test-api-implementation-example.py`)
Reference implementation showing:
- FastAPI endpoint implementations
- Authentication and authorization checks
- TEST_MODE gating for test endpoints
- Audit logging patterns
- Error handling

**Use this** as a starting point for backend development.

### 3. Migration Guide (`budget-test-migration-guide.md`)
Step-by-step guide for updating tests:
- Import changes
- Client initialization
- Method call updates
- Before/after code examples
- Hybrid approach for gradual rollout
- Testing and rollback plans

**Follow this** to update test code once endpoints are deployed.

### 4. API Client (`../e2e_tests/utils/budget-api-client.ts`)
New TypeScript client for tests:
- `BudgetApiClient` class mirroring `BudgetDatabase` interface
- Methods for all new endpoints
- Proper error handling
- Type-safe request/response models

**Use this** in tests instead of `BudgetDatabase`.

## Implementation Roadmap

### Phase 1: Backend Implementation (2 weeks)

**Repository**: OpenHands/OpenHands (main backend repository)

1. Add `TEST_MODE` environment variable to settings
2. Implement production endpoints (maintenance trigger, status, cycle-state)
3. Implement test-mode endpoints (state manipulation, member seeding)
4. Add comprehensive tests for each endpoint
5. Add audit logging for all operations
6. Review security model and authorization checks

**Deliverable**: Backend PR with all endpoints implemented

### Phase 2: Test Updates (1 week)

**Repository**: OpenHands/OpenHands-Cloud (this repository)

1. Update `BudgetApi` class to include new endpoint methods
2. Add dual-mode support (API + database fallback)
3. Update configuration to support `BUDGET_E2E_USE_API` flag
4. Test in staging with both modes
5. Verify equivalent behavior

**Deliverable**: Cloud PR with updated test utilities

### Phase 3: Beta Deployment (1 week)

1. Deploy backend with endpoints to beta
2. Set `TEST_MODE=true` in beta helm values
3. Enable `BUDGET_E2E_USE_API=true` in beta test configuration
4. Run tests and monitor results in ReportPortal
5. Fix any issues discovered

**Deliverable**: Budget tests running on beta

### Phase 4: Remove Database Dependency (1 week)

1. Remove database fallback code from tests
2. Remove `BUDGET_E2E_DATABASE_URL` requirement
3. Update documentation
4. Clean up `BudgetDatabase` class (archive for reference)

**Deliverable**: Tests run everywhere without database

## Expected Benefits

### Immediate
- ✅ Budget tests run on beta instance
- ✅ Budget tests run in PR checks (with TEST_MODE)
- ✅ No database credentials in CI
- ✅ Easier local testing

### Long-term
- ✅ Proper API-level testing (not database bypass)
- ✅ Better security (authentication, authorization, audit trails)
- ✅ Backend can change schema without breaking tests
- ✅ Test-mode functionality useful beyond e2e tests

## Security Model

### Production Endpoints
- Require authentication (bearer token)
- Require organization admin role
- Rate-limited to prevent abuse
- All operations logged to audit trail
- Available in all environments

### Test-Mode Endpoints
- All of the above, plus:
- Require `TEST_MODE=true` environment variable
- Return `404 Not Found` when TEST_MODE is false (hide existence)
- Disabled in production (TEST_MODE=false or unset)
- Extra warning-level audit logging
- Clear documentation that these are test-only

### Defense in Depth
1. Environment variable check (TEST_MODE)
2. Authentication check (valid token)
3. Authorization check (admin role)
4. Audit logging (all operations tracked)
5. Production deployment (TEST_MODE=false)

## Configuration Changes

### Before (Database Required)
```bash
BUDGET_E2E_DATABASE_URL=postgresql://user:pass@host:5432/db
BUDGET_E2E_LITELLM_URL=https://litellm...
BUDGET_E2E_LITELLM_API_KEY=sk-...
# ... 10+ more variables
```

### After (API Only)
```bash
BUDGET_E2E_ORG_ID=550e8400-e29b-41d4-a716-446655440000
TEST_MODE=true  # In test environments only

# No longer needed:
# BUDGET_E2E_DATABASE_URL
```

## Testing Strategy

### 1. Unit Tests (Backend)
- Test each endpoint in isolation
- Mock database and auth
- Verify request/response formats
- Test error conditions

### 2. Integration Tests (Backend)
- Test with real database
- Test with real auth
- Verify authorization checks
- Test TEST_MODE gating

### 3. E2E Tests (Cloud)
- Use real endpoints in tests
- Compare API results with database results (dual mode)
- Verify equivalent behavior
- Test in staging before beta

### 4. Security Tests
- Verify TEST_MODE endpoints return 404 when disabled
- Verify authorization checks work
- Verify audit logging captures operations
- Attempt to call endpoints without proper auth

## Success Metrics

### Week 1 (After Backend Deployment)
- [ ] All 7 endpoints implemented and tested
- [ ] Endpoints deployed to development environment
- [ ] Manual testing shows correct behavior

### Week 2 (After Test Updates)
- [ ] Tests work with both API and database
- [ ] Both modes produce equivalent results
- [ ] No regressions in test coverage

### Week 3 (After Beta Deployment)
- [ ] Budget tests visible in ReportPortal for beta runs
- [ ] At least 80% of test scenarios pass
- [ ] No TEST_MODE security issues

### Month 1 (Steady State)
- [ ] 100% of budget tests pass on beta
- [ ] Tests run on every beta deploy
- [ ] Zero database credentials needed
- [ ] ReportPortal shows consistent budget coverage

## Rollback Plan

### If Backend Issues
- Keep endpoints, fix bugs, redeploy
- Tests can continue using database fallback
- No impact on existing test coverage

### If Test Issues
- Revert to database-only mode (`BUDGET_E2E_USE_API=false`)
- Fix test code offline
- Redeploy when ready

### If Security Issues
- Set `TEST_MODE=false` to disable test endpoints
- Review security model
- Fix and redeploy

### If Critical Issues
- Revert entire feature
- Investigate root cause offline
- Implement fixes and redeploy

## Future Enhancements

### Near-term
- Add maintenance scheduling (delay parameter)
- Add batch maintenance triggering (multiple orgs)
- Add maintenance webhooks/notifications

### Medium-term
- Pre-defined budget state fixtures
- Snapshot/restore for full budget state
- Performance monitoring for endpoints

### Long-term
- Use these endpoints for operational tasks (not just testing)
- Add to admin UI for manual maintenance triggering
- Extend to other e2e test domains (not just budgets)

## Questions & Answers

### Q: Why not just use the existing maintenance cron?
A: Cron runs on schedule (e.g., every 5 minutes). Tests need to trigger maintenance synchronously and immediately after setup. Waiting for cron makes tests slow and flaky.

### Q: Why separate production and test-mode endpoints?
A: Production endpoints provide legitimate operational value (manual maintenance trigger). Test-mode endpoints manipulate state in ways that are only safe in test environments.

### Q: How do we prevent test-mode abuse?
A: Multiple layers: TEST_MODE environment check, admin auth, 404 response when disabled, audit logging, and production deployment with TEST_MODE=false.

### Q: What if someone accidentally enables TEST_MODE in production?
A: Still safe - requires admin auth, manipulates budget state (not actual payments), all operations logged. But we have deployment checks to prevent this.

### Q: Can we use these endpoints for non-test purposes?
A: Yes! Production endpoints (maintenance trigger, status poll, cycle-state read) are useful for operations and debugging in production. Test-mode endpoints remain test-only.

## Related Documents

- **Original Investigation**: `/workspace/budget-e2e-analysis.md`
- **Database Value Analysis**: `/workspace/DATABASE_VALUE_ANALYSIS.md`
- **Executive Summary**: `/workspace/EXECUTIVE_SUMMARY.md`
- **Current Test Implementation**: `e2e_tests/tests/009-budgets.spec.ts`
- **Current Database Class**: `e2e_tests/utils/budget-database.ts`

## Next Steps

1. **Review** this package with backend and QA teams
2. **Approve** the API design and security model
3. **Create** backend PR implementing endpoints (OpenHands/OpenHands repo)
4. **Deploy** to development and test manually
5. **Update** tests to use endpoints (this repo)
6. **Deploy** to beta with TEST_MODE=true
7. **Monitor** ReportPortal for test results
8. **Remove** database dependency once stable

## Contact

For questions about this solution:
- **API Design**: See `budget-test-api-specification.md`
- **Implementation**: See `budget-test-api-implementation-example.py`
- **Test Migration**: See `budget-test-migration-guide.md`
- **Original Problem**: See `/workspace/EXECUTIVE_SUMMARY.md`

---

**Last Updated**: 2026-09-30  
**Status**: Design Complete, Awaiting Implementation  
**Estimated Effort**: 4-6 weeks total  
**Expected Impact**: Budget tests run on all environments without database access
