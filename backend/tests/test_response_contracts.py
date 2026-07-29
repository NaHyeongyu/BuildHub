from __future__ import annotations

from fastapi import FastAPI

from app.api.published_flows import router as published_flows_router
from app.main import app
from app.schemas.project_responses import PromptFileChangeResponse


def _response_schema(path: str, method: str = "get") -> dict:
    return app.openapi()["paths"][path][method]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]


def test_project_detail_endpoints_publish_concrete_response_models() -> None:
    assert _response_schema("/api/projects/{project_id}/detail")["$ref"].endswith(
        "/ProjectDetailResponse"
    )
    assert _response_schema("/api/projects/{project_id}/workspace")["$ref"].endswith(
        "/ProjectWorkspaceResponse"
    )
    assert _response_schema("/api/projects/{project_id}/prompt-activities")["$ref"].endswith(
        "/ProjectPromptActivitiesResponse"
    )
    assert _response_schema("/api/projects/{project_id}/files")["$ref"].endswith(
        "/ProjectFilesResponse"
    )


def test_prompt_file_change_contract_accepts_patch_event_ids() -> None:
    response = PromptFileChangeResponse.model_validate(
        {
            "additions": 4,
            "binary": False,
            "deletions": 1,
            "event_id": "b9d9c474-82fe-43b2-b798-3252936913d1",
            "path": "frontend/src/App.tsx",
            "status": "modified",
        }
    )

    assert response.event_id == "b9d9c474-82fe-43b2-b798-3252936913d1"


def test_memory_endpoints_publish_concrete_response_models() -> None:
    assert _response_schema("/api/projects/{project_id}/memory/generation-preview")[
        "$ref"
    ].endswith("/MemoryGenerationPreviewResponse")
    assert _response_schema("/api/projects/{project_id}/memory/batches/{batch_id}")[
        "$ref"
    ].endswith("/MemoryBatchResponse")
    assert _response_schema("/api/projects/memory/review-queue/refresh", "post")["$ref"].endswith(
        "/MemoryReviewQueueResponse"
    )


def test_admin_inventory_endpoints_publish_concrete_response_models() -> None:
    assert _response_schema("/api/admin/overview")["$ref"].endswith("/AdminOverviewResponse")
    assert _response_schema("/api/admin/events")["$ref"].endswith("/AdminEventPageResponse")
    assert _response_schema("/api/admin/system")["$ref"].endswith("/AdminSystemResponse")
    assert "additionalProperties" not in _response_schema("/api/admin/users")
    assert "additionalProperties" not in _response_schema("/api/admin/projects")
    assert "additionalProperties" not in _response_schema("/api/admin/jobs")


def test_github_resource_endpoints_publish_concrete_response_models() -> None:
    assert _response_schema("/api/projects/github/repositories")["$ref"].endswith(
        "/GithubRepositoriesResponse"
    )
    assert _response_schema("/api/projects/{project_id}/github/files")["$ref"].endswith(
        "/ProjectGithubFilesResponse"
    )
    assert _response_schema("/api/projects/{project_id}/github/files/content")["$ref"].endswith(
        "/ProjectGithubFileContentResponse"
    )


def test_community_endpoints_publish_concrete_response_models() -> None:
    assert _response_schema("/api/projects/public")["$ref"].endswith("/PublicProjectListResponse")
    assert _response_schema("/api/projects/public/{project_id}")["$ref"].endswith(
        "/PublicProjectDetailResponse"
    )
    public_memory_fields = set(
        app.openapi()["components"]["schemas"]["PublicMemoryArtifactResponse"]["properties"]
    )
    assert public_memory_fields.isdisjoint(
        {
            "changed_files",
            "commit_sha",
            "memory_batch_id",
            "memory_batch_ids",
            "session_id",
            "source_draft_ids",
            "source_session_ids",
        }
    )
    assert _response_schema("/api/projects/public/{project_id}/view", "post")["$ref"].endswith(
        "/PublicProjectViewResponse"
    )
    community_app = FastAPI()
    community_app.include_router(published_flows_router)
    paths = community_app.openapi()["paths"]
    list_schema = paths["/api/published-flows"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert list_schema["type"] == "array"
    assert list_schema["items"]["$ref"].endswith("/PublishedFlowSummaryResponse")
    project_details_schema = paths["/api/published-flows/project/{project_id}/details"]["get"][
        "responses"
    ]["200"]["content"]["application/json"]["schema"]
    assert project_details_schema["type"] == "array"
    assert project_details_schema["items"]["$ref"].endswith("/PublishedFlowDetailResponse")
    detail_schema = paths["/api/published-flows/{flow_key}"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert detail_schema["$ref"].endswith("/PublishedFlowDetailResponse")
