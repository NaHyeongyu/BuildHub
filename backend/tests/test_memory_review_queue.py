from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.memory import workflows


class FakeNestedTransaction:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_args: object) -> None:
        return None


class FakeSession:
    def begin_nested(self) -> FakeNestedTransaction:
        return FakeNestedTransaction()


def test_project_workspace_combines_detail_pending_ranges_and_latest_batch(
    monkeypatch,
) -> None:
    project = SimpleNamespace(id=uuid4())
    detail = {
        "activities": [],
        "files": [],
        "memory": {
            "latest_artifact_at": None,
            "recent_artifacts": [],
            "total_artifacts": 0,
        },
        "metrics": {},
        "project": {"id": str(project.id)},
        "prompt_activities": [],
    }
    latest_batch = object()
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        workflows,
        "read_project_detail_response",
        lambda project_id, user, db: (
            calls.append(("detail", (project_id, user, db))) or detail
        ),
    )
    monkeypatch.setattr(
        workflows,
        "read_latest_project_memory_batch",
        lambda db, *, project_id: (
            calls.append(("latest", (db, project_id))) or latest_batch
        ),
    )
    monkeypatch.setattr(
        workflows,
        "serialize_project_memory_batch",
        lambda db, batch, *, replayed: {
            "batch_id": "batch-1",
            "replayed": replayed,
        },
    )
    monkeypatch.setattr(
        workflows,
        "list_project_memory_pending_ranges",
        lambda db, *, project_id, limit: (
            calls.append(("pending", (db, project_id, limit)))
            or [{"draft_id": "draft-1"}]
        ),
    )

    db = FakeSession()
    user = object()
    response = workflows.read_project_workspace_response(
        db,
        pending_limit=100,
        project_id=project.id,
        user=user,
    )

    assert response["memory"] == {
        "drafts": [],
        "latest_artifact_at": None,
        "latest_batch": {"batch_id": "batch-1", "replayed": True},
        "pending_ranges": [{"draft_id": "draft-1"}],
        "recent_artifacts": [],
        "total_artifacts": 0,
    }
    assert calls == [
        ("detail", (project.id, user, db)),
        ("latest", (db, project.id)),
        ("pending", (db, project.id, 100)),
    ]


def test_review_queue_refresh_queries_ranges_only_for_projects_with_pending_work(
    monkeypatch,
) -> None:
    pending_project_id = uuid4()
    empty_project_id = uuid4()
    project_summaries = [
        {
            "id": str(empty_project_id),
            "pending_memory_count": 0,
        },
        {
            "id": str(pending_project_id),
            "pending_memory_count": 2,
        },
    ]
    summary_calls: list[object] = []
    pending_calls: list[tuple[object, int, object]] = []

    def summaries(_db, *, current_user):
        summary_calls.append(current_user)
        return project_summaries

    monkeypatch.setattr(workflows, "list_project_summaries", summaries)

    def pending_ranges(db, *, limit, project_id):
        pending_calls.append((db, limit, project_id))
        return [{"draft_id": "draft-1"}, {"draft_id": "draft-2"}]

    monkeypatch.setattr(
        workflows,
        "list_project_memory_pending_ranges",
        pending_ranges,
    )

    db = FakeSession()
    user = object()
    response = workflows.refresh_memory_review_queue_response(
        db,
        limit=100,
        user=user,
    )

    assert summary_calls == [user]
    assert pending_calls == [(db, 100, pending_project_id)]
    assert response["errors"] == []
    assert response["project_summaries"] == project_summaries
    assert response["projects"] == [
        {
            "pending_count": 2,
            "project_id": str(pending_project_id),
            "ranges": [{"draft_id": "draft-1"}, {"draft_id": "draft-2"}],
        }
    ]
    assert response["total_pending_count"] == 2


def test_review_queue_refresh_skips_range_query_when_all_projects_are_empty(
    monkeypatch,
) -> None:
    project_id = uuid4()
    summary = {
        "id": str(project_id),
        "pending_memory_count": 0,
    }
    monkeypatch.setattr(
        workflows,
        "list_project_summaries",
        lambda _db, *, current_user: [summary],
    )
    monkeypatch.setattr(
        workflows,
        "list_project_memory_pending_ranges",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("empty projects must not query pending ranges")
        ),
    )

    response = workflows.refresh_memory_review_queue_response(
        FakeSession(),
        limit=100,
        user=object(),
    )

    assert response["errors"] == []
    assert response["project_summaries"] == [summary]
    assert response["projects"] == []
    assert response["total_pending_count"] == 0


def test_review_queue_refresh_route_is_exposed() -> None:
    from app.main import app

    operation = app.openapi()["paths"]["/api/projects/memory/review-queue/refresh"]

    assert "post" in operation


def test_project_memory_generation_preview_route_is_exposed() -> None:
    from app.main import app

    operation = app.openapi()["paths"][
        "/api/projects/{project_id}/memory/generation-preview"
    ]

    assert "get" in operation


def test_memory_generation_is_project_scoped() -> None:
    from app.main import app

    schema = app.openapi()
    paths = schema["paths"]

    assert "post" in paths["/api/projects/{project_id}/memory/generate"]
    assert "get" in paths["/api/projects/{project_id}/memory/batches/latest"]
    assert "get" in paths["/api/projects/{project_id}/memory/batches/{batch_id}"]
    assert "get" in paths["/api/projects/{project_id}/memory/generation-review"]
    assert (
        "/api/projects/{project_id}/memory/generation-review/prompts/{event_id}"
        not in paths
    )
    assert "/api/projects/{project_id}/sessions/{session_id}/checkpoint" not in paths

    request_schema = schema["components"]["schemas"]["ProjectMemoryGenerateRequest"]
    assert "review_token" in request_schema["required"]
    assert "excluded_prompt_event_ids" in request_schema["properties"]


def test_generation_rejects_unconfigured_provider_without_consuming_drafts(
    monkeypatch,
) -> None:
    project_id = uuid4()
    user = SimpleNamespace(id=uuid4())
    monkeypatch.setattr(
        workflows,
        "project_for_user",
        lambda *_args, **_kwargs: SimpleNamespace(id=project_id),
    )
    monkeypatch.setattr(workflows, "provider_is_configured", lambda _provider: False)

    with pytest.raises(HTTPException) as raised:
        workflows.generate_project_memory_response(
            FakeSession(),  # type: ignore[arg-type]
            idempotency_key="safe-preflight",
            project_id=project_id,
            user=user,  # type: ignore[arg-type]
        )

    assert getattr(raised.value, "status_code", None) == 503
    assert "Captured work is safe" in str(getattr(raised.value, "detail", ""))


def test_review_queue_refresh_isolates_project_materialization_errors(
    monkeypatch,
) -> None:
    failing_project_id = uuid4()
    healthy_project_id = uuid4()
    project_summaries = [
        {"id": str(failing_project_id), "pending_memory_count": 1},
        {"id": str(healthy_project_id), "pending_memory_count": 1},
    ]

    monkeypatch.setattr(
        workflows,
        "list_project_summaries",
        lambda _db, *, current_user: project_summaries,
    )

    def pending_ranges(_db, *, limit, project_id):
        if project_id == failing_project_id:
            raise ValueError("broken project")
        return [{"draft_id": "healthy-draft"}]

    monkeypatch.setattr(
        workflows,
        "list_project_memory_pending_ranges",
        pending_ranges,
    )

    response = workflows.refresh_memory_review_queue_response(
        FakeSession(),
        limit=100,
        user=object(),
    )

    assert response["errors"] == [
        {
            "message": "Captured work could not be checked for this project.",
            "project_id": str(failing_project_id),
        }
    ]
    assert response["project_summaries"] == project_summaries
    assert response["projects"] == [
        {
            "pending_count": 1,
            "project_id": str(healthy_project_id),
            "ranges": [{"draft_id": "healthy-draft"}],
        }
    ]


def test_manual_session_completion_materializes_the_final_pending_window(
    monkeypatch,
) -> None:
    project = SimpleNamespace(id=uuid4())
    session = SimpleNamespace(id=uuid4())
    generated_sessions: list[object] = []
    pending_range = {
        "can_checkpoint": True,
        "session_id": str(session.id),
    }

    monkeypatch.setattr(workflows, "project_for_user", lambda *_args, **_kwargs: project)
    monkeypatch.setattr(workflows, "session_for_project", lambda *_args: session)
    monkeypatch.setattr(
        workflows,
        "complete_session_if_ready",
        lambda *_args, **_kwargs: {
            "completed": True,
            "completed_at": datetime(2026, 7, 12, tzinfo=UTC),
            "reason": "manual",
        },
    )
    monkeypatch.setattr(
        workflows,
        "generate_due_memory_artifacts_for_session",
        lambda _db, target_session, **_kwargs: generated_sessions.append(target_session),
    )
    monkeypatch.setattr(
        workflows,
        "list_project_memory_pending_ranges",
        lambda *_args, **_kwargs: [pending_range],
    )

    response = workflows.complete_project_session_response(
        object(),
        force=True,
        project_id=project.id,
        session_id=session.id,
        user=object(),
    )

    assert generated_sessions == [session]
    assert response["pending_range"] == pending_range
    assert response["status"] == "pending_memory"
