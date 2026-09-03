import { Client } from "pg";

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

export interface BudgetMaintenanceResult {
  id: number;
  status: string;
  info: Record<string, unknown> | null;
  updatedAt: string;
}

export class BudgetDatabase {
  private readonly createdTaskIds: number[] = [];

  constructor(private readonly connectionString: string) {}

  private async withClient<T>(
    operation: (client: Client) => Promise<T>,
  ): Promise<T> {
    const client = new Client({ connectionString: this.connectionString });
    await client.connect();
    try {
      return await operation(client);
    } finally {
      await client.end();
    }
  }

  getCycleState(orgId: string): Promise<BudgetCycleState> {
    return this.withClient(async (client) => {
      const result = await client.query<{
        cycle_start_at: Date;
        cycle_start_spend: number;
        user_cycle_start_spend: Record<string, number>;
        litellm_last_sync_at: Date | null;
        litellm_last_sync_status: string | null;
        litellm_last_sync_error: string | null;
        litellm_last_spend_snapshot_at: Date | null;
        litellm_last_team_spend: number | null;
        litellm_last_member_spend: Record<string, number>;
        litellm_known_member_ids: string[];
      }>(
        `SELECT cycle_start_at,
                cycle_start_spend,
                user_cycle_start_spend,
                litellm_last_sync_at,
                litellm_last_sync_status,
                litellm_last_sync_error,
                litellm_last_spend_snapshot_at,
                litellm_last_team_spend,
                litellm_last_member_spend,
                litellm_known_member_ids
           FROM org_budget_settings
          WHERE org_id = $1`,
        [orgId],
      );
      const row = result.rows[0];
      if (!row) throw new Error(`Budget settings are absent for org ${orgId}`);
      return {
        cycleStartAt: row.cycle_start_at.toISOString(),
        cycleStartSpend: Number(row.cycle_start_spend),
        userCycleStartSpend: row.user_cycle_start_spend,
        liteLLMLastSyncAt: row.litellm_last_sync_at?.toISOString() ?? null,
        liteLLMLastSyncStatus: row.litellm_last_sync_status,
        liteLLMLastSyncError: row.litellm_last_sync_error,
        liteLLMLastSpendSnapshotAt:
          row.litellm_last_spend_snapshot_at?.toISOString() ?? null,
        liteLLMLastTeamSpend:
          row.litellm_last_team_spend === null
            ? null
            : Number(row.litellm_last_team_spend),
        liteLLMLastMemberSpend: row.litellm_last_member_spend,
        liteLLMKnownMemberIds: row.litellm_known_member_ids,
      };
    });
  }

  makeCycleStale(orgId: string): Promise<void> {
    return this.withClient(async (client) => {
      const result = await client.query(
        `UPDATE org_budget_settings
            SET cycle_start_at = CURRENT_TIMESTAMP - INTERVAL '40 days'
          WHERE org_id = $1`,
        [orgId],
      );
      if (result.rowCount !== 1) {
        throw new Error(`Failed to make budget cycle stale for org ${orgId}`);
      }
    });
  }

  restoreCycleState(orgId: string, state: BudgetCycleState): Promise<void> {
    return this.withClient(async (client) => {
      const result = await client.query(
        `UPDATE org_budget_settings
            SET cycle_start_at = $2,
                cycle_start_spend = $3,
                user_cycle_start_spend = $4::json,
                litellm_last_sync_at = $5,
                litellm_last_sync_status = $6,
                litellm_last_sync_error = $7,
                litellm_last_spend_snapshot_at = $8,
                litellm_last_team_spend = $9,
                litellm_last_member_spend = $10::json,
                litellm_known_member_ids = $11::json
          WHERE org_id = $1`,
        [
          orgId,
          state.cycleStartAt,
          state.cycleStartSpend,
          JSON.stringify(state.userCycleStartSpend),
          state.liteLLMLastSyncAt,
          state.liteLLMLastSyncStatus,
          state.liteLLMLastSyncError,
          state.liteLLMLastSpendSnapshotAt,
          state.liteLLMLastTeamSpend,
          JSON.stringify(state.liteLLMLastMemberSpend),
          JSON.stringify(state.liteLLMKnownMemberIds),
        ],
      );
      if (result.rowCount !== 1) {
        throw new Error(`Failed to restore budget cycle for org ${orgId}`);
      }
    });
  }

  private getMaintenanceTask(taskId: number): Promise<BudgetMaintenanceResult> {
    return this.withClient(async (client) => {
      const result = await client.query<{
        id: number;
        status: string;
        info: Record<string, unknown> | null;
        updated_at: Date;
      }>(
        `SELECT id, status, info, updated_at
           FROM maintenance_tasks
          WHERE id = $1`,
        [taskId],
      );
      const row = result.rows[0];
      if (!row) throw new Error(`Maintenance task ${taskId} is absent`);
      return {
        id: row.id,
        status: row.status,
        info: row.info,
        updatedAt: row.updated_at.toISOString(),
      };
    });
  }

  async runMaintenance(
    orgId: string,
    timeoutMs: number,
    intervalMs: number,
  ): Promise<BudgetMaintenanceResult> {
    const taskId = await this.withClient(async (client) => {
      const result = await client.query<{ id: number }>(
        `INSERT INTO maintenance_tasks
           (status, processor_type, processor_json, delay)
         VALUES
           ('PENDING', $1, $2, 0)
         RETURNING id`,
        [
          "server.maintenance_task_processor.org_budget_maintenance_processor.OrgBudgetMaintenanceProcessor",
          JSON.stringify({ org_ids: [orgId] }),
        ],
      );
      return result.rows[0].id;
    });
    this.createdTaskIds.push(taskId);
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
    return task;
  }

  cleanupCreatedTasks(): Promise<void> {
    if (this.createdTaskIds.length === 0) return Promise.resolve();
    return this.withClient(async (client) => {
      const active = await client.query<{ id: number }>(
        `SELECT id
           FROM maintenance_tasks
          WHERE id = ANY($1::integer[])
            AND status = 'WORKING'`,
        [this.createdTaskIds],
      );
      if (active.rows.length > 0) {
        throw new Error(
          `Refusing to delete active maintenance tasks: ${active.rows.map((row) => row.id).join(", ")}`,
        );
      }
      await client.query(
        `DELETE FROM maintenance_tasks
          WHERE id = ANY($1::integer[])`,
        [this.createdTaskIds],
      );
      this.createdTaskIds.length = 0;
    });
  }
}
