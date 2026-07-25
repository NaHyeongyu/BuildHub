from __future__ import annotations

from datetime import UTC, datetime
import os
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.artifact_versions import ArtifactVersion
from app.models.artifacts import Artifact
from app.models.events import Event
from app.models.projects import Project
from app.models.sessions import Session as ProjectSession
from app.models.users import User
from app.services import context_graph
from app.services.context_graph import (
    read_project_context_graph,
    review_project_context_graph_node,
)
from app.services.event_payload_security import encrypt_event_payload
from app.services.memory.constants import MEMORY_ARTIFACT_TYPE
from app.services.memory.knowledge import build_memory_knowledge_projection


pytestmark = pytest.mark.skipif(
    os.environ.get("PROMTY_RUN_POSTGRES_TESTS") != "1",
    reason="PostgreSQL integration tests are disabled",
)


@pytest.fixture
def db() -> Session:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_context_graph_review_updates_compact_metadata_without_snapshot_amplification(
    db: Session,
) -> None:
    marker = str(uuid4())
    owner = User(
        email=f"knowledge-review-{marker}@example.com",
        github_id=f"knowledge-review-{marker}",
        username=f"knowledge-review-{marker}",
    )
    project = Project(
        default_branch="main",
        name="Knowledge review integration",
        owner=owner,
        slug=f"knowledge-review-{marker}",
        visibility="private",
    )
    draft = {
        "confidence": 0.9,
        "details": {
            "decisions": [
                {
                    "confidence": 0.92,
                    "decision": "Use a right-side drawer.",
                    "reason": "Keep the source context visible.",
                    "source_event_ids": [],
                }
            ]
        },
        "evidence": {"source_event_ids": []},
        "summary": "A review direction was selected.",
        "title": "Review direction",
        "type": "decision_note",
    }
    projection = build_memory_knowledge_projection(draft)
    decision = next(node for node in projection["nodes"] if node["kind"] == "decision")
    artifact = Artifact(
        changed_files=[],
        created_at=datetime.now(UTC),
        generator="openai:test",
        metadata_={
            "artifact_stage": "generated_memory",
            "event_count": 1,
            "knowledge_projection": projection,
            "review_state": "generated",
            "tool": "codex-cli",
        },
        outcome="Use a right-side drawer.",
        project=project,
        prompt_event_ids=[],
        reason="Keep the source context visible.",
        sections=[],
        storage_key=f"memory/test/{marker}",
        summary=draft["summary"],
        tags=["decision_note"],
        technologies=[],
        title=draft["title"],
        type=MEMORY_ARTIFACT_TYPE,
    )
    db.add_all((owner, project, artifact))
    db.flush()

    node_id = f"knowledge:{artifact.id}:{decision['id']}"
    result = review_project_context_graph_node(
        db,
        action="confirm",
        node_id=node_id,
        project_id=project.id,
        user=owner,
    )
    db.flush()
    db.expire_all()

    stored = db.get(Artifact, artifact.id)
    assert stored is not None
    stored_decision = next(
        node
        for node in stored.metadata_["knowledge_projection"]["nodes"]
        if node["id"] == decision["id"]
    )
    assert result["review_state"] == "confirmed"
    assert stored_decision["review_state"] == "confirmed"
    assert stored_decision["reviewed_by"] == str(owner.id)
    assert (
        db.scalar(
            select(func.count(ArtifactVersion.id)).where(
                ArtifactVersion.artifact_id == artifact.id
            )
        )
        == 0
    )

    outsider = User(
        email=f"knowledge-review-outsider-{marker}@example.com",
        github_id=f"knowledge-review-outsider-{marker}",
        username=f"knowledge-review-outsider-{marker}",
    )
    db.add(outsider)
    db.flush()
    with pytest.raises(HTTPException) as denied:
        review_project_context_graph_node(
            db,
            action="reject",
            node_id=node_id,
            project_id=project.id,
            user=outsider,
        )
    assert denied.value.status_code == 404


def test_knowledge_graph_decrypts_only_referenced_prompt_and_paired_response(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = str(uuid4())
    owner = User(
        email=f"knowledge-evidence-{marker}@example.com",
        github_id=f"knowledge-evidence-{marker}",
        username=f"knowledge-evidence-{marker}",
    )
    project = Project(
        default_branch="main",
        name="Knowledge evidence bounds",
        owner=owner,
        slug=f"knowledge-evidence-{marker}",
        visibility="private",
    )
    now = datetime.now(UTC)
    session_id = uuid4()
    project_session = ProjectSession(
        id=session_id,
        cwd="/tmp/promty",
        project=project,
        started_at=now,
        tool="codex-cli",
    )
    prompt_id = uuid4()
    response_id = uuid4()
    events = [
        Event(
            id=prompt_id,
            project=project,
            session=project_session,
            sequence=1,
            schema_version=1,
            tool="codex-cli",
            event_type="PromptSubmitted",
            payload=encrypt_event_payload(
                "PromptSubmitted",
                {"prompt": "Referenced prompt"},
            ),
            created_at=now,
        ),
        Event(
            id=response_id,
            project=project,
            session=project_session,
            sequence=2,
            schema_version=1,
            tool="codex-cli",
            event_type="ResponseReceived",
            payload=encrypt_event_payload(
                "ResponseReceived",
                {"response": "Referenced output"},
            ),
            created_at=now,
        ),
    ]
    for sequence in range(3, 23, 2):
        events.extend(
            (
                Event(
                    id=uuid4(),
                    project=project,
                    session=project_session,
                    sequence=sequence,
                    schema_version=1,
                    tool="codex-cli",
                    event_type="PromptSubmitted",
                    payload=encrypt_event_payload(
                        "PromptSubmitted",
                        {"prompt": f"Unrelated prompt {sequence}"},
                    ),
                    created_at=now,
                ),
                Event(
                    id=uuid4(),
                    project=project,
                    session=project_session,
                    sequence=sequence + 1,
                    schema_version=1,
                    tool="codex-cli",
                    event_type="ResponseReceived",
                    payload=encrypt_event_payload(
                        "ResponseReceived",
                        {"response": f"Unrelated output {sequence}"},
                    ),
                    created_at=now,
                ),
            )
        )

    knowledge_projection = {
        "nodes": [
            {
                "confidence": 0.9,
                "evidence_type": "inferred",
                "id": "bounded-evidence",
                "kind": "decision",
                "label": "Load only referenced evidence.",
                "source_chunk_ids": [],
                "source_event_ids": [str(prompt_id)],
                "status": "active",
                "summary": "Avoid decrypting unrelated session history.",
            }
        ],
        "schema_version": 1,
        "source": "memory_generation",
    }
    artifact = Artifact(
        changed_files=[],
        created_at=now,
        generator="openai:test",
        metadata_={
            "artifact_stage": "generated_memory",
            "event_count": len(events),
            "knowledge_projection": knowledge_projection,
            "review_state": "generated",
            "tool": "codex-cli",
        },
        outcome="Bound evidence loading.",
        project=project,
        prompt_event_ids=[str(prompt_id)],
        reason="Keep graph reads cheap.",
        sections=[],
        session=project_session,
        storage_key=f"memory/test/{marker}",
        summary="Bound graph evidence loading.",
        tags=["decision_note"],
        technologies=[],
        title="Knowledge evidence bounds",
        type=MEMORY_ARTIFACT_TYPE,
    )
    db.add_all((owner, project, project_session, *events, artifact))
    db.flush()

    decrypted_event_types: list[str] = []
    real_decrypt = context_graph.decrypt_event_payload

    def track_decrypt(event_type: str, payload: dict) -> dict:
        decrypted_event_types.append(event_type)
        return real_decrypt(event_type, payload)

    monkeypatch.setattr(context_graph, "decrypt_event_payload", track_decrypt)
    payload = read_project_context_graph(
        db,
        limit=20,
        project_id=project.id,
        query=None,
        user=owner,
        view="knowledge",
    )

    node_ids = {node["id"] for node in payload["nodes"]}
    assert f"prompt:{prompt_id}" in node_ids
    assert f"response:{response_id}" in node_ids
    assert decrypted_event_types == ["PromptSubmitted", "ResponseReceived"]
