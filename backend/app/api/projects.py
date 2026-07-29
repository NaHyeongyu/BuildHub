from __future__ import annotations

import logging
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.transactions import commit_or_conflict as _commit_or_conflict
from app.core.security import require_web_user
from app.db.session import get_db
from app.models.users import User
from app.schemas.projects import (
    ProjectBookmarkUpdateRequest,
    ProjectCreateRequest,
    ProjectDescriptionUpdateRequest,
    ProjectMetadataUpdateRequest,
    PublicProjectSaveUpdateRequest,
    ProjectRepositoryUpdateRequest,
    ProjectSummaryResponse,
)
from app.schemas.project_responses import (
    GithubRepositoriesResponse,
    ProjectDetailResponse,
    ProjectFilesResponse,
    ProjectGithubFileContentResponse,
    ProjectGithubFilesResponse,
    ProjectPromptActivitiesResponse,
    PublicProjectDetailResponse,
    PublicProjectListResponse,
    PublicProjectSaveResponse,
    PublicProjectViewResponse,
    PublicProfileResponse,
)
from app.schemas.memory_responses import ProjectWorkspaceResponse
from app.services.github_repositories import (
    list_github_repositories,
    read_github_repository_file_content,
    read_github_repository_tree_with_etag,
)
from app.services.projects.management import (
    create_project_summary,
    delete_project as delete_project_for_user,
    list_project_summaries,
    update_project_bookmark_summary,
    update_project_description_summary,
    update_project_metadata_summary,
    update_project_repository_summary,
)
from app.services.projects.public import (
    list_public_project_summaries,
    read_public_project_detail_response,
    read_public_profile_response,
    update_public_project_save,
)
from app.services.projects.analytics import record_public_project_view
from app.services.projects.activity_deletion import (
    delete_prompt_activity as delete_prompt_activity_for_user,
    delete_session_activity as delete_session_activity_for_user,
)
from app.services.projects.views import (
    project_for_user as _project_for_user,
    read_project_detail_response,
    read_project_files_response,
    read_project_prompt_activities_response,
)
from app.services.memory.workflows import read_project_workspace_response
from app.services.published_flow_asset_storage import delete_published_flow_asset

router = APIRouter(prefix="/api/projects", tags=["projects"])
logger = logging.getLogger(__name__)


def _cleanup_deleted_activity_assets(storage_keys: tuple[str, ...]) -> None:
    for storage_key in storage_keys:
        if not delete_published_flow_asset(storage_key):
            logger.warning(
                "Published-flow asset cleanup was not confirmed after activity deletion: %s",
                storage_key,
            )


@router.get("", response_model=list[ProjectSummaryResponse])
def list_projects(
    current_user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    return list_project_summaries(db, current_user=current_user)


@router.post("", response_model=ProjectSummaryResponse)
def create_project(
    payload: ProjectCreateRequest,
    current_user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    response = create_project_summary(
        db,
        default_branch=payload.default_branch,
        description=payload.description,
        github_url=payload.github_url,
        name=payload.name,
        user=current_user,
    )
    _commit_or_conflict(
        db,
        detail="Project could not be created because it conflicts with an existing project.",
    )
    return response


@router.get("/public", response_model=PublicProjectListResponse)
def list_public_projects(
    query: str | None = Query(default=None, max_length=120),
    saved_only: bool = Query(default=False),
    sort: Literal["newest", "popular", "recent"] = Query(default="popular"),
    limit: int = Query(default=24, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return list_public_project_summaries(
        db,
        current_user=current_user,
        limit=limit,
        offset=offset,
        query=query,
        saved_only=saved_only,
        sort=sort,
    )


@router.get("/public/profiles/{user_id}", response_model=PublicProfileResponse)
def read_public_profile(
    user_id: UUID,
    limit: int = Query(default=24, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return read_public_profile_response(
        db,
        current_user=current_user,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )


@router.get("/public/{project_id}", response_model=PublicProjectDetailResponse)
def read_public_project(
    project_id: UUID,
    current_user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return read_public_project_detail_response(
        db,
        current_user=current_user,
        project_id=project_id,
    )


@router.patch("/public/{project_id}/save", response_model=PublicProjectSaveResponse)
def save_public_project(
    project_id: UUID,
    payload: PublicProjectSaveUpdateRequest,
    current_user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    response = update_public_project_save(
        db,
        current_user=current_user,
        project_id=project_id,
        is_saved=payload.is_saved,
    )
    _commit_or_conflict(db, detail="Public project save could not be updated.")
    return response


@router.post("/public/{project_id}/view", response_model=PublicProjectViewResponse)
def track_public_project_view(
    project_id: UUID,
    current_user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    response = record_public_project_view(
        db,
        current_user=current_user,
        project_id=project_id,
    )
    _commit_or_conflict(db, detail="Public project view could not be recorded.")
    return response


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: UUID,
    current_user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
) -> Response:
    delete_project_for_user(db, project_id=project_id, user=current_user)
    _commit_or_conflict(db, detail="Project could not be deleted.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{project_id}/prompt-activities/{prompt_event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_prompt_activity(
    project_id: UUID,
    prompt_event_id: UUID,
    current_user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
) -> Response:
    result = delete_prompt_activity_for_user(
        db,
        project_id=project_id,
        prompt_event_id=prompt_event_id,
        user=current_user,
    )
    _commit_or_conflict(db, detail="Prompt activity could not be deleted.")
    _cleanup_deleted_activity_assets(result.asset_storage_keys)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/{project_id}/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_session_activity(
    project_id: UUID,
    session_id: UUID,
    current_user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
) -> Response:
    result = delete_session_activity_for_user(
        db,
        project_id=project_id,
        session_id=session_id,
        user=current_user,
    )
    _commit_or_conflict(db, detail="Session activity could not be deleted.")
    _cleanup_deleted_activity_assets(result.asset_storage_keys)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/github/repositories", response_model=GithubRepositoriesResponse)
def read_project_github_repositories(
    search: str | None = Query(default=None, max_length=120),
    current_user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return list_github_repositories(db, user=current_user, query=search)


@router.get("/{project_id}/detail", response_model=ProjectDetailResponse)
def read_project_detail(
    project_id: UUID,
    current_user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return read_project_detail_response(project_id, current_user, db)


@router.get("/{project_id}/workspace", response_model=ProjectWorkspaceResponse)
def read_project_workspace(
    project_id: UUID,
    pending_limit: int = Query(default=100, ge=1, le=100),
    current_user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    response = read_project_workspace_response(
        db,
        pending_limit=pending_limit,
        project_id=project_id,
        user=current_user,
    )
    db.commit()
    return response


@router.get(
    "/{project_id}/prompt-activities",
    response_model=ProjectPromptActivitiesResponse,
)
def read_project_prompt_activities(
    project_id: UUID,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=512),
    q: str | None = Query(default=None, max_length=120),
    session_id: UUID | None = Query(default=None),
    current_user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return read_project_prompt_activities_response(
        project_id,
        current_user,
        db,
        limit=limit,
        cursor=cursor,
        query=q,
        session_id=session_id,
    )


@router.get("/{project_id}/files", response_model=ProjectFilesResponse)
def read_project_files(
    project_id: UUID,
    limit: int = Query(default=2000, ge=1, le=5000),
    current_user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return read_project_files_response(
        project_id,
        current_user,
        db,
        limit=limit,
    )


@router.patch("/{project_id}/repository", response_model=ProjectSummaryResponse)
def update_project_repository(
    project_id: UUID,
    payload: ProjectRepositoryUpdateRequest,
    current_user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    response = update_project_repository_summary(
        db,
        default_branch=payload.default_branch,
        github_url=payload.github_url,
        project_id=project_id,
        user=current_user,
    )
    _commit_or_conflict(db, detail="Repository could not be connected to this project.")
    return response


@router.patch("/{project_id}/description", response_model=ProjectSummaryResponse)
def update_project_description(
    project_id: UUID,
    payload: ProjectDescriptionUpdateRequest,
    current_user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    response = update_project_description_summary(
        db,
        description=payload.description,
        project_id=project_id,
        user=current_user,
    )
    _commit_or_conflict(db, detail="Project description could not be updated.")
    return response


@router.patch("/{project_id}/metadata", response_model=ProjectSummaryResponse)
def update_project_metadata(
    project_id: UUID,
    payload: ProjectMetadataUpdateRequest,
    current_user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    response = update_project_metadata_summary(
        db,
        memory_grouping_mode=payload.memory_grouping_mode,
        name=payload.name,
        project_id=project_id,
        project_url=payload.project_url,
        project_url_is_set="project_url" in payload.model_fields_set,
        slug=payload.slug,
        tags=payload.tags,
        user=current_user,
        visibility=payload.visibility,
    )
    _commit_or_conflict(db, detail="Project metadata could not be updated.")
    return response


@router.patch("/{project_id}/bookmark", response_model=ProjectSummaryResponse)
def update_project_bookmark(
    project_id: UUID,
    payload: ProjectBookmarkUpdateRequest,
    current_user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    response = update_project_bookmark_summary(
        db,
        is_bookmarked=payload.is_bookmarked,
        project_id=project_id,
        user=current_user,
    )
    _commit_or_conflict(db, detail="Project bookmark could not be updated.")
    return response


@router.get(
    "/{project_id}/github/files",
    response_model=ProjectGithubFilesResponse,
    responses={304: {"description": "Repository tree has not changed"}},
)
def read_project_github_files(
    project_id: UUID,
    response: Response,
    if_none_match: str | None = Header(default=None, max_length=256),
    x_promty_repository_tree_key: str | None = Header(default=None, max_length=64),
    current_user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
) -> dict[str, Any] | Response:
    project = _project_for_user(db, project_id, current_user, allow_admin=True)
    result = read_github_repository_tree_with_etag(
        db,
        if_none_match=if_none_match,
        project=project,
        tree_key=x_promty_repository_tree_key,
        user=current_user,
    )
    if result.etag:
        response.headers["ETag"] = result.etag
    if result.tree_key:
        response.headers["X-Promty-Repository-Tree-Key"] = result.tree_key
    if result.not_modified:
        headers = {}
        if result.etag:
            headers["ETag"] = result.etag
        if result.tree_key:
            headers["X-Promty-Repository-Tree-Key"] = result.tree_key
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED,
            headers=headers or None,
        )
    if result.payload is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub repository tree response was invalid",
        )
    return result.payload


@router.get(
    "/{project_id}/github/files/content",
    response_model=ProjectGithubFileContentResponse,
)
def read_project_github_file_content(
    project_id: UUID,
    path: str = Query(..., min_length=1, max_length=2048),
    current_user: User = Depends(require_web_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = _project_for_user(db, project_id, current_user, allow_admin=True)
    return read_github_repository_file_content(
        db,
        path=path,
        project=project,
        user=current_user,
    )
