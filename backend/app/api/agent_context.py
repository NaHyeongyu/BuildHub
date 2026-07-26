from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session as DBSession

from app.core.security import require_collector_user
from app.db.session import get_db
from app.models.users import User
from app.schemas.agent_context import AgentProjectContextResponse
from app.services.agent_context import read_agent_project_context


router = APIRouter(prefix="/api/agent", tags=["agent-context"])


@router.get(
    "/projects/{project_id}/context",
    response_model=AgentProjectContextResponse,
)
def get_agent_project_context(
    project_id: UUID,
    current_user: User = Depends(require_collector_user),
    db: DBSession = Depends(get_db),
) -> dict[str, Any]:
    response = read_agent_project_context(
        db,
        project_id=project_id,
        user=current_user,
    )
    db.commit()
    return response
