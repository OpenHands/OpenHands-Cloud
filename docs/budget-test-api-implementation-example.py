"""
Budget Test API Endpoints - Implementation Example

This is a reference implementation showing what the budget test API endpoints
might look like. Adapt to your actual backend framework and ORM.

Assumptions:
- FastAPI framework
- SQLAlchemy ORM
- Existing auth middleware that provides current_user
- Existing org authorization checks
"""

from datetime import datetime
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
import os

# =============================================================================
# Configuration
# =============================================================================

def is_test_mode() -> bool:
    """Check if TEST_MODE environment variable is enabled."""
    return os.getenv("TEST_MODE", "").lower() in ("true", "1", "yes")


# =============================================================================
# Models
# =============================================================================

class MaintenanceTaskCreate(BaseModel):
    """Response when creating a maintenance task."""
    task_id: int
    status: str = "PENDING"
    created_at: datetime


class MaintenanceTaskStatus(BaseModel):
    """Maintenance task status response."""
    id: int
    status: str  # PENDING, WORKING, COMPLETED, ERROR
    created_at: datetime
    updated_at: datetime
    info: Optional[Dict] = None


class BudgetCycleStateResponse(BaseModel):
    """Budget cycle state response."""
    cycle_start_at: datetime
    cycle_end_at: datetime
    cycle_start_spend: float
    user_cycle_start_spend: Dict[str, float]
    litellm_last_sync_at: Optional[datetime]
    litellm_last_sync_status: Optional[str]
    litellm_last_sync_error: Optional[str]
    litellm_last_spend_snapshot_at: Optional[datetime]
    litellm_last_team_spend: Optional[float]
    litellm_last_member_spend: Dict[str, float]
    litellm_known_member_ids: List[str]


class RestoreCycleStateRequest(BaseModel):
    """Request to restore budget cycle state."""
    cycle_start_at: datetime
    cycle_start_spend: float
    user_cycle_start_spend: Dict[str, float]
    litellm_last_sync_at: Optional[datetime]
    litellm_last_sync_status: Optional[str]
    litellm_last_sync_error: Optional[str]
    litellm_last_spend_snapshot_at: Optional[datetime]
    litellm_last_team_spend: Optional[float]
    litellm_last_member_spend: Dict[str, float]
    litellm_known_member_ids: List[str]


class SeedMembersRequest(BaseModel):
    """Request to seed test members."""
    count: int = Field(ge=1, le=100, description="Number of members to create (1-100)")


class SeedMembersResponse(BaseModel):
    """Response from seeding test members."""
    user_ids: List[str]
    created_at: datetime


class RemoveMembersRequest(BaseModel):
    """Request to remove test members."""
    user_ids: List[str]


class RemoveMembersResponse(BaseModel):
    """Response from removing test members."""
    deleted_count: int
    deleted_at: datetime


# =============================================================================
# Dependencies
# =============================================================================

async def get_current_user():
    """Get current authenticated user (placeholder)."""
    # Replace with your actual auth implementation
    pass


async def require_org_admin(
    org_id: str,
    current_user = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Verify user is admin of the organization."""
    # Replace with your actual org authorization implementation
    if not await is_org_admin(db, org_id, current_user.id):
        raise HTTPException(status_code=403, detail="Organization admin required")
    return current_user


async def require_test_mode():
    """Verify TEST_MODE is enabled, return 404 if not (hide existence)."""
    if not is_test_mode():
        raise HTTPException(status_code=404, detail="Not found")


def get_db():
    """Get database session (placeholder)."""
    # Replace with your actual database session management
    pass


# =============================================================================
# Router
# =============================================================================

router = APIRouter(prefix="/api/organizations/{org_id}/budgets", tags=["budgets"])


# =============================================================================
# Production Endpoints
# =============================================================================

@router.post("/maintenance", response_model=MaintenanceTaskCreate)
async def trigger_budget_maintenance(
    org_id: str,
    current_user = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """
    Trigger budget maintenance for an organization.
    
    Creates a maintenance task that will be processed by the background worker.
    Use the returned task_id to poll for completion.
    """
    # Check if maintenance is already running
    existing_task = db.query(MaintenanceTask).filter(
        MaintenanceTask.processor_type == "server.maintenance_task_processor.org_budget_maintenance_processor.OrgBudgetMaintenanceProcessor",
        MaintenanceTask.status.in_(["PENDING", "WORKING"]),
    ).filter(
        MaintenanceTask.processor_json["org_ids"].astext.contains(org_id)
    ).first()
    
    if existing_task:
        raise HTTPException(
            status_code=409,
            detail=f"Maintenance task {existing_task.id} already running for this organization"
        )
    
    # Create maintenance task
    task = MaintenanceTask(
        status="PENDING",
        processor_type="server.maintenance_task_processor.org_budget_maintenance_processor.OrgBudgetMaintenanceProcessor",
        processor_json={"org_ids": [org_id]},
        delay=0,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    
    # Log operation
    audit_log.info(
        "budget_maintenance_triggered",
        org_id=org_id,
        task_id=task.id,
        user_id=current_user.id,
    )
    
    return MaintenanceTaskCreate(
        task_id=task.id,
        status=task.status,
        created_at=task.created_at,
    )


@router.get("/maintenance/{task_id}", response_model=MaintenanceTaskStatus)
async def get_maintenance_task_status(
    org_id: str,
    task_id: int,
    current_user = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """
    Get status of a budget maintenance task.
    
    Poll this endpoint to check if maintenance has completed.
    """
    task = db.query(MaintenanceTask).filter(
        MaintenanceTask.id == task_id,
    ).first()
    
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    
    # Verify task belongs to this org (security check)
    task_org_ids = task.processor_json.get("org_ids", [])
    if org_id not in task_org_ids:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return MaintenanceTaskStatus(
        id=task.id,
        status=task.status,
        created_at=task.created_at,
        updated_at=task.updated_at,
        info=task.info,
    )


@router.get("/cycle-state", response_model=BudgetCycleStateResponse)
async def get_budget_cycle_state(
    org_id: str,
    current_user = Depends(require_org_admin),
    db: Session = Depends(get_db),
):
    """
    Get current budget cycle state for an organization.
    
    Returns cycle dates, sync status, and spend information.
    """
    settings = db.query(OrgBudgetSettings).filter(
        OrgBudgetSettings.org_id == org_id
    ).first()
    
    if not settings:
        raise HTTPException(
            status_code=404,
            detail="Budget settings not found for this organization"
        )
    
    return BudgetCycleStateResponse(
        cycle_start_at=settings.cycle_start_at,
        cycle_end_at=settings.cycle_end_at,
        cycle_start_spend=settings.cycle_start_spend,
        user_cycle_start_spend=settings.user_cycle_start_spend,
        litellm_last_sync_at=settings.litellm_last_sync_at,
        litellm_last_sync_status=settings.litellm_last_sync_status,
        litellm_last_sync_error=settings.litellm_last_sync_error,
        litellm_last_spend_snapshot_at=settings.litellm_last_spend_snapshot_at,
        litellm_last_team_spend=settings.litellm_last_team_spend,
        litellm_last_member_spend=settings.litellm_last_member_spend,
        litellm_known_member_ids=settings.litellm_known_member_ids,
    )


# =============================================================================
# Test-Mode Endpoints
# =============================================================================

@router.post("/test/make-cycle-stale")
async def make_cycle_stale(
    org_id: str,
    current_user = Depends(require_org_admin),
    test_mode = Depends(require_test_mode),
    db: Session = Depends(get_db),
):
    """
    Make the budget cycle stale (40 days old) for testing.
    
    Requires TEST_MODE=true. Used to test cycle rollover without waiting.
    """
    result = db.execute(
        """
        UPDATE org_budget_settings
        SET cycle_start_at = CURRENT_TIMESTAMP - INTERVAL '40 days'
        WHERE org_id = :org_id
        RETURNING cycle_start_at
        """,
        {"org_id": org_id}
    )
    row = result.fetchone()
    
    if not row:
        raise HTTPException(
            status_code=404,
            detail="Budget settings not found for this organization"
        )
    
    db.commit()
    
    # Log operation
    audit_log.warning(
        "test_cycle_made_stale",
        org_id=org_id,
        user_id=current_user.id,
        new_cycle_start=row[0],
    )
    
    return {
        "success": True,
        "new_cycle_start": row[0],
        "days_moved": 40,
    }


@router.post("/test/restore-cycle-state")
async def restore_cycle_state(
    org_id: str,
    request: RestoreCycleStateRequest,
    current_user = Depends(require_org_admin),
    test_mode = Depends(require_test_mode),
    db: Session = Depends(get_db),
):
    """
    Restore budget cycle to a previous state.
    
    Requires TEST_MODE=true. Used to clean up after destructive tests.
    """
    result = db.execute(
        """
        UPDATE org_budget_settings
        SET cycle_start_at = :cycle_start_at,
            cycle_start_spend = :cycle_start_spend,
            user_cycle_start_spend = :user_cycle_start_spend,
            litellm_last_sync_at = :litellm_last_sync_at,
            litellm_last_sync_status = :litellm_last_sync_status,
            litellm_last_sync_error = :litellm_last_sync_error,
            litellm_last_spend_snapshot_at = :litellm_last_spend_snapshot_at,
            litellm_last_team_spend = :litellm_last_team_spend,
            litellm_last_member_spend = :litellm_last_member_spend,
            litellm_known_member_ids = :litellm_known_member_ids
        WHERE org_id = :org_id
        """,
        {
            "org_id": org_id,
            "cycle_start_at": request.cycle_start_at,
            "cycle_start_spend": request.cycle_start_spend,
            "user_cycle_start_spend": request.user_cycle_start_spend,
            "litellm_last_sync_at": request.litellm_last_sync_at,
            "litellm_last_sync_status": request.litellm_last_sync_status,
            "litellm_last_sync_error": request.litellm_last_sync_error,
            "litellm_last_spend_snapshot_at": request.litellm_last_spend_snapshot_at,
            "litellm_last_team_spend": request.litellm_last_team_spend,
            "litellm_last_member_spend": request.litellm_last_member_spend,
            "litellm_known_member_ids": request.litellm_known_member_ids,
        }
    )
    
    if result.rowcount == 0:
        raise HTTPException(
            status_code=404,
            detail="Budget settings not found for this organization"
        )
    
    db.commit()
    
    # Log operation
    audit_log.warning(
        "test_cycle_state_restored",
        org_id=org_id,
        user_id=current_user.id,
    )
    
    return {
        "success": True,
        "restored_at": datetime.utcnow(),
    }


# =============================================================================
# Test Member Endpoints
# =============================================================================

test_router = APIRouter(prefix="/api/organizations/{org_id}/test", tags=["test"])


@test_router.post("/seed-members", response_model=SeedMembersResponse)
async def seed_test_members(
    org_id: str,
    request: SeedMembersRequest,
    current_user = Depends(require_org_admin),
    test_mode = Depends(require_test_mode),
    db: Session = Depends(get_db),
):
    """
    Create test members for pagination testing.
    
    Requires TEST_MODE=true. Creates throwaway users that only exist in the
    OpenHands database (not in LiteLLM).
    """
    import uuid
    
    # Get member role ID
    member_role = db.query(Role).filter(Role.name == "member").first()
    if not member_role:
        raise HTTPException(status_code=500, detail="Member role not found")
    
    user_ids = []
    try:
        for i in range(request.count):
            user_id = str(uuid.uuid4())
            email = f"e2e-pagination-{user_id}@example.invalid"
            
            # Create user
            user = User(
                id=user_id,
                current_org_id=org_id,
                email=email,
            )
            db.add(user)
            
            # Create org member
            org_member = OrgMember(
                org_id=org_id,
                user_id=user_id,
                role_id=member_role.id,
                _llm_api_key=f"e2e-pagination-{user_id}",
                agent_settings_diff={},
                conversation_settings_diff={},
                has_custom_llm_api_key=False,
                managed_llm_key_ownership_version=1,
            )
            db.add(org_member)
            
            user_ids.append(user_id)
        
        db.commit()
        
        # Log operation
        audit_log.warning(
            "test_members_seeded",
            org_id=org_id,
            user_id=current_user.id,
            count=request.count,
            user_ids=user_ids,
        )
        
        return SeedMembersResponse(
            user_ids=user_ids,
            created_at=datetime.utcnow(),
        )
        
    except Exception as e:
        db.rollback()
        # Clean up any partially created users
        if user_ids:
            db.execute(
                "DELETE FROM org_member WHERE org_id = :org_id AND user_id = ANY(:user_ids)",
                {"org_id": org_id, "user_ids": user_ids}
            )
            db.execute(
                'DELETE FROM "user" WHERE id = ANY(:user_ids)',
                {"user_ids": user_ids}
            )
            db.commit()
        raise HTTPException(status_code=500, detail=str(e))


@test_router.delete("/members", response_model=RemoveMembersResponse)
async def remove_test_members(
    org_id: str,
    request: RemoveMembersRequest,
    current_user = Depends(require_org_admin),
    test_mode = Depends(require_test_mode),
    db: Session = Depends(get_db),
):
    """
    Remove test members created by seed-members endpoint.
    
    Requires TEST_MODE=true. Cleans up throwaway test users.
    """
    if not request.user_ids:
        return RemoveMembersResponse(deleted_count=0, deleted_at=datetime.utcnow())
    
    # Delete org_member entries
    result1 = db.execute(
        "DELETE FROM org_member WHERE org_id = :org_id AND user_id = ANY(:user_ids)",
        {"org_id": org_id, "user_ids": request.user_ids}
    )
    
    # Delete user entries
    result2 = db.execute(
        'DELETE FROM "user" WHERE id = ANY(:user_ids)',
        {"user_ids": request.user_ids}
    )
    
    db.commit()
    
    # Log operation
    audit_log.warning(
        "test_members_removed",
        org_id=org_id,
        user_id=current_user.id,
        deleted_count=result2.rowcount,
        user_ids=request.user_ids,
    )
    
    return RemoveMembersResponse(
        deleted_count=result2.rowcount,
        deleted_at=datetime.utcnow(),
    )


# =============================================================================
# Register Routers
# =============================================================================

# Add to your main FastAPI app:
# app.include_router(router)
# app.include_router(test_router)
