/**
 * Budget API Client
 *
 * This replaces direct database access (BudgetDatabase class) with API calls.
 * Once the backend endpoints are implemented, tests can use this client instead
 * of requiring direct database access.
 */

import type { APIRequestContext } from "@playwright/test";
import { pollUntil } from "./budgets";

export interface BudgetCycleState {
  cycleStartAt: string;
  cycleStartSpend: number;
  userCycleStartSpend: Record<string, number>;
  liteLLMLastSyncAt: string | null;
  liteLLMLastSyncStatus: string | null;
  liteLLMLastSyncError: string | null;
  liteLLMLastSpendSnapshotAt: string | null;
  liteLLMLastTeamSpend: number | null;
  liteLLMLastMemberSpend: Record<string, number>;
  liteLLMKnownMemberIds: string[];
}

export interface BudgetMaintenanceTask {
  id: number;
  status: "PENDING" | "WORKING" | "COMPLETED" | "ERROR";
  createdAt: string;
  updatedAt: string;
  info: Record<string, unknown> | null;
}

export interface BudgetMaintenanceResult {
  id: number;
  status: string;
  info: Record<string, unknown> | null;
  updatedAt: string;
}

/**
 * Client for budget-related API endpoints that replace database access.
 */
export class BudgetApiClient {
  constructor(
    private readonly request: APIRequestContext,
    private readonly orgId: string,
  ) {}

  private get headers(): Record<string, string> {
    return { "X-Org-Id": this.orgId };
  }

  /**
   * Get current budget cycle state for the organization.
   * Replaces: database.getCycleState(orgId)
   */
  async getCycleState(): Promise<BudgetCycleState> {
    const response = await this.request.get(
      `/api/organizations/${this.orgId}/budgets/cycle-state`,
      { headers: this.headers },
    );

    if (!response.ok()) {
      throw new Error(
        `Failed to get cycle state: ${response.status()} ${await response.text()}`,
      );
    }

    const data = await response.json();

    // Convert API response format to BudgetCycleState format
    return {
      cycleStartAt: data.cycle_start_at,
      cycleStartSpend: data.cycle_start_spend,
      userCycleStartSpend: data.user_cycle_start_spend,
      liteLLMLastSyncAt: data.litellm_last_sync_at,
      liteLLMLastSyncStatus: data.litellm_last_sync_status,
      liteLLMLastSyncError: data.litellm_last_sync_error,
      liteLLMLastSpendSnapshotAt: data.litellm_last_spend_snapshot_at,
      liteLLMLastTeamSpend: data.litellm_last_team_spend,
      liteLLMLastMemberSpend: data.litellm_last_member_spend,
      liteLLMKnownMemberIds: data.litellm_known_member_ids,
    };
  }

  /**
   * Trigger budget maintenance for the organization.
   * Returns task ID for polling.
   * Replaces: database.runMaintenance(orgId, timeoutMs, intervalMs)
   */
  async triggerMaintenance(): Promise<number> {
    const response = await this.request.post(
      `/api/organizations/${this.orgId}/budgets/maintenance`,
      { headers: this.headers },
    );

    if (!response.ok()) {
      throw new Error(
        `Failed to trigger maintenance: ${response.status()} ${await response.text()}`,
      );
    }

    const data = await response.json();
    return data.task_id;
  }

  /**
   * Get maintenance task status.
   */
  async getMaintenanceTask(taskId: number): Promise<BudgetMaintenanceTask> {
    const response = await this.request.get(
      `/api/organizations/${this.orgId}/budgets/maintenance/${taskId}`,
      { headers: this.headers },
    );

    if (!response.ok()) {
      throw new Error(
        `Failed to get maintenance task: ${response.status()} ${await response.text()}`,
      );
    }

    const data = await response.json();
    return {
      id: data.id,
      status: data.status,
      createdAt: data.created_at,
      updatedAt: data.updated_at,
      info: data.info,
    };
  }

  /**
   * Run maintenance and wait for completion.
   * Replaces: database.runMaintenance(orgId, timeoutMs, intervalMs)
   */
  async runMaintenance(
    timeoutMs: number,
    intervalMs: number,
  ): Promise<BudgetMaintenanceResult> {
    const taskId = await this.triggerMaintenance();

    const task = await pollUntil(
      () => this.getMaintenanceTask(taskId),
      (candidate) => ["COMPLETED", "ERROR"].includes(candidate.status),
      {
        description: `budget maintenance task ${taskId}`,
        timeoutMs,
        intervalMs,
      },
    );

    if (task.status !== "COMPLETED") {
      throw new Error(
        `Budget maintenance task ${taskId} failed: ${JSON.stringify(task.info)}`,
      );
    }

    if (Number(task.info?.error_count || 0) > 0) {
      throw new Error(
        `Budget maintenance task ${taskId} reported errors: ${JSON.stringify(task.info)}`,
      );
    }

    return {
      id: task.id,
      status: task.status,
      info: task.info,
      updatedAt: task.updatedAt,
    };
  }

  /**
   * Make the budget cycle stale (40 days old) for testing rollover.
   * Requires TEST_MODE=true.
   * Replaces: database.makeCycleStale(orgId)
   */
  async makeCycleStale(): Promise<void> {
    const response = await this.request.post(
      `/api/organizations/${this.orgId}/budgets/test/make-cycle-stale`,
      { headers: this.headers },
    );

    if (!response.ok()) {
      throw new Error(
        `Failed to make cycle stale: ${response.status()} ${await response.text()}`,
      );
    }
  }

  /**
   * Restore budget cycle state.
   * Requires TEST_MODE=true.
   * Replaces: database.restoreCycleState(orgId, state)
   */
  async restoreCycleState(state: BudgetCycleState): Promise<void> {
    const response = await this.request.post(
      `/api/organizations/${this.orgId}/budgets/test/restore-cycle-state`,
      {
        headers: { ...this.headers, "Content-Type": "application/json" },
        data: {
          cycle_start_at: state.cycleStartAt,
          cycle_start_spend: state.cycleStartSpend,
          user_cycle_start_spend: state.userCycleStartSpend,
          litellm_last_sync_at: state.liteLLMLastSyncAt,
          litellm_last_sync_status: state.liteLLMLastSyncStatus,
          litellm_last_sync_error: state.liteLLMLastSyncError,
          litellm_last_spend_snapshot_at: state.liteLLMLastSpendSnapshotAt,
          litellm_last_team_spend: state.liteLLMLastTeamSpend,
          litellm_last_member_spend: state.liteLLMLastMemberSpend,
          litellm_known_member_ids: state.liteLLMKnownMemberIds,
        },
      },
    );

    if (!response.ok()) {
      throw new Error(
        `Failed to restore cycle state: ${response.status()} ${await response.text()}`,
      );
    }
  }

  /**
   * Seed test members for pagination testing.
   * Requires TEST_MODE=true.
   * Replaces: database.seedMemberFinancialListingMembers(orgId, count)
   */
  async seedMemberFinancialListingMembers(count: number): Promise<string[]> {
    const response = await this.request.post(
      `/api/organizations/${this.orgId}/test/seed-members`,
      {
        headers: { ...this.headers, "Content-Type": "application/json" },
        data: { count },
      },
    );

    if (!response.ok()) {
      throw new Error(
        `Failed to seed members: ${response.status()} ${await response.text()}`,
      );
    }

    const data = await response.json();
    return data.user_ids;
  }

  /**
   * Remove test members.
   * Requires TEST_MODE=true.
   * Replaces: database.removeMemberFinancialListingMembers(orgId, userIds)
   */
  async removeMemberFinancialListingMembers(userIds: string[]): Promise<void> {
    if (userIds.length === 0) return;

    const response = await this.request.delete(
      `/api/organizations/${this.orgId}/test/members`,
      {
        headers: { ...this.headers, "Content-Type": "application/json" },
        data: { user_ids: userIds },
      },
    );

    if (!response.ok()) {
      throw new Error(
        `Failed to remove members: ${response.status()} ${await response.text()}`,
      );
    }
  }
}

/**
 * Helper to create BudgetApiClient from test context.
 *
 * Usage in tests:
 *   const apiClient = createBudgetApiClient(page.request, orgId);
 *   await apiClient.runMaintenance(timeoutMs, intervalMs);
 */
export function createBudgetApiClient(
  request: APIRequestContext,
  orgId: string,
): BudgetApiClient {
  return new BudgetApiClient(request, orgId);
}
