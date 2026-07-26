from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import logging
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.models.project_memory_batches import (
    ProjectMemoryBatch,
    ProjectMemoryBatchItem,
    ProjectMemoryBatchRequest,
)
from app.models.artifacts import Artifact
from app.services.memory import batches
from app.services.memory.constants import (
    REVIEW_STATE_DRAFT,
    REVIEW_STATE_IGNORED,
)


class FakeDB:
    def __init__(self) -> None:
        self.flush_count = 0
        self.commit_count = 0
        self.rollback_count = 0

    def flush(self) -> None:
        self.flush_count += 1

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class FakeScalarResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one(self):
        return self.value

    def scalars(self):
        return self

    def __iter__(self):
        return iter(self.value)


class FakeClaimDB(FakeDB):
    def __init__(self, batch) -> None:
        super().__init__()
        self.batch = batch
        self.commit_count = 0

    def execute(self, _statement):
        return FakeScalarResult(self.batch)

    def commit(self) -> None:
        self.commit_count += 1


class FakeLeaseDB(FakeDB):
    def __init__(self, *, rowcount: int) -> None:
        super().__init__()
        self.rowcount = rowcount
        self.statement = None
        self.closed = False

    def execute(self, statement):
        self.statement = statement
        return SimpleNamespace(rowcount=self.rowcount)

    def close(self) -> None:
        self.closed = True


class FakeFailureDB(FakeDB):
    def __init__(self, batch) -> None:
        super().__init__()
        self.batch = batch
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        return FakeScalarResult(self.batch)


class FakeAttemptDB(FakeDB):
    def __init__(self, batch, project) -> None:
        super().__init__()
        self.batch = batch
        self.project = project

    def get(self, model, _identifier):
        return self.batch if model is ProjectMemoryBatch else self.project


class FakePrepareDB(FakeDB):
    def __init__(self, drafts) -> None:
        super().__init__()
        self.added = []
        self.claimed_ids = set()
        self.drafts = drafts
        self.project = SimpleNamespace(memory_grouping_mode="session")
        self.statements = []

    def get(self, _model, _identifier):
        return self.project

    def add(self, value) -> None:
        if isinstance(value, ProjectMemoryBatch) and value.id is None:
            value.id = uuid4()
        if isinstance(value, ProjectMemoryBatchItem):
            self.claimed_ids.add(value.draft_id)
        self.added.append(value)

    def execute(self, statement):
        self.statements.append(statement)
        limit = statement._limit_clause.value
        available = sorted(
            (draft for draft in self.drafts if draft.id not in self.claimed_ids),
            key=lambda draft: (draft.created_at, draft.id),
        )
        return FakeScalarResult(available[:limit])


def _snapshot(draft_id=None):
    return batches.PendingDraftSnapshot(
        created_at=datetime(2026, 7, 12, tzinfo=UTC),
        id=draft_id or uuid4(),
        metadata_={},
        summary="Captured project work",
        title="Project work",
        version_id=uuid4(),
    )


def _generated_chunk(snapshots):
    return batches.GeneratedChunkPayload(
        first_event_at=None,
        last_event_at=None,
        metadata={},
        payload={"summary": "Generated context"},
        source_draft_ids=[str(snapshot.id) for snapshot in snapshots],
        source_draft_version_ids=[str(snapshot.version_id) for snapshot in snapshots],
        source_session_id=str(uuid4()),
    )


def _batch_rows(*, first_session_count: int, second_session_count: int = 0):
    project_id = uuid4()
    first_session = SimpleNamespace(id=uuid4(), project_id=project_id)
    second_session = SimpleNamespace(id=uuid4(), project_id=project_id)
    rows = []
    for index in range(first_session_count + second_session_count):
        session = first_session if index < first_session_count else second_session
        draft_id = uuid4()
        draft = SimpleNamespace(
            id=draft_id,
            metadata_={"review_state": REVIEW_STATE_DRAFT},
            updated_at=datetime(2026, 7, 12, tzinfo=UTC),
        )
        rows.append((SimpleNamespace(), draft, _snapshot(draft_id), session))
    return project_id, rows


def test_project_memory_batch_models_capture_exact_draft_versions() -> None:
    assert ProjectMemoryBatch.__table__.c.grouping_mode.nullable is False
    assert ProjectMemoryBatch.__table__.c.idempotency_key.nullable is False
    assert ProjectMemoryBatch.__table__.c.idempotency_keys.nullable is False
    assert ProjectMemoryBatch.__table__.c.lease_expires_at.nullable is True
    assert ProjectMemoryBatch.__table__.c.snapshot_manifest.nullable is False
    assert ProjectMemoryBatchItem.__table__.c.draft_version_id.nullable is False
    assert ProjectMemoryBatchRequest.__table__.c.idempotency_key.primary_key is True
    assert ProjectMemoryBatchRequest.__table__.c.project_id.primary_key is True
    assert any(
        constraint.name == "uq_project_memory_batch_items_draft_id"
        for constraint in ProjectMemoryBatchItem.__table__.constraints
    )
    assert any(
        index.name == "ux_artifacts_memory_storage_key" and index.unique
        for index in Artifact.__table__.indexes
    )


def test_project_memory_lock_key_is_stable_and_project_specific() -> None:
    first_project_id = uuid4()
    second_project_id = uuid4()

    assert batches._project_memory_lock_key(first_project_id) == batches._project_memory_lock_key(
        first_project_id
    )
    assert batches._project_memory_lock_key(first_project_id) != batches._project_memory_lock_key(
        second_project_id
    )


def test_latest_draft_versions_are_selected_by_postgres_distinct_on() -> None:
    first_id = uuid4()
    second_id = uuid4()
    latest_versions = [
        SimpleNamespace(artifact_id=first_id, version=7),
        SimpleNamespace(artifact_id=second_id, version=3),
    ]

    class VersionQueryDB:
        def __init__(self) -> None:
            self.statement = None

        def execute(self, statement):
            self.statement = statement
            return FakeScalarResult(latest_versions)

    db = VersionQueryDB()

    selected = batches._latest_versions_by_artifact(
        db,  # type: ignore[arg-type]
        [first_id, second_id],
    )

    assert selected == {
        first_id: latest_versions[0],
        second_id: latest_versions[1],
    }
    compiled = str(db.statement.compile(dialect=postgresql.dialect()))
    assert "SELECT DISTINCT ON (artifact_versions.artifact_id)" in compiled
    assert "ORDER BY artifact_versions.artifact_id, artifact_versions.version DESC" in compiled


def test_latest_draft_versions_skips_query_for_empty_batch() -> None:
    class DatabaseMustNotBeUsed:
        def execute(self, _statement):
            raise AssertionError("empty draft batches must not query artifact history")

    assert (
        batches._latest_versions_by_artifact(
            DatabaseMustNotBeUsed(),  # type: ignore[arg-type]
            [],
        )
        == {}
    )


def test_prepare_batch_claims_deterministic_cap_and_leaves_excess_pending(
    monkeypatch,
) -> None:
    project_id = uuid4()
    user_id = uuid4()
    session_id = uuid4()
    created_at = datetime(2026, 7, 13, tzinfo=UTC)
    draft_ids = [UUID(int=value) for value in (5, 2, 4, 1, 3)]
    drafts = [
        SimpleNamespace(
            created_at=created_at + timedelta(seconds=draft_id.int // 4),
            id=draft_id,
            metadata_={"review_state": REVIEW_STATE_DRAFT},
            session_id=session_id,
            updated_at=created_at,
        )
        for draft_id in draft_ids
    ]
    versions = {draft.id: SimpleNamespace(id=uuid4(), artifact_id=draft.id) for draft in drafts}
    db = FakePrepareDB(drafts)
    monkeypatch.setattr(
        batches,
        "settings",
        SimpleNamespace(project_memory_batch_max_drafts=3),
    )
    monkeypatch.setattr(
        batches,
        "_latest_versions_by_artifact",
        lambda _db, ids: {draft_id: versions[draft_id] for draft_id in ids},
    )
    monkeypatch.setattr(batches, "_attach_idempotency_key", lambda _db, batch, _key: batch)

    first = batches._prepare_batch(
        db,  # type: ignore[arg-type]
        idempotency_key="first",
        project_id=project_id,
        user_id=user_id,
    )
    second = batches._prepare_batch(
        db,  # type: ignore[arg-type]
        idempotency_key="second",
        project_id=project_id,
        user_id=user_id,
    )

    assert [item["draft_id"] for item in first.snapshot_manifest] == [
        str(UUID(int=1)),
        str(UUID(int=2)),
        str(UUID(int=3)),
    ]
    assert [item["draft_id"] for item in second.snapshot_manifest] == [
        str(UUID(int=4)),
        str(UUID(int=5)),
    ]
    assert all(draft.metadata_["review_state"] == REVIEW_STATE_DRAFT for draft in drafts)
    assert len(db.statements) == 2
    compiled = str(
        db.statements[0].compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "ORDER BY artifacts.created_at, artifacts.id" in compiled
    assert "LIMIT 3 FOR UPDATE" in compiled
    assert "sent_to_ai_at" in compiled
    assert "IS NULL" in compiled


def test_generation_exclusions_are_stored_in_the_immutable_batch_manifest(
    monkeypatch,
) -> None:
    project_id = uuid4()
    prompt_id = uuid4()
    draft = SimpleNamespace(
        created_at=datetime(2026, 7, 13, tzinfo=UTC),
        id=uuid4(),
        metadata_={"review_state": REVIEW_STATE_DRAFT},
        session_id=uuid4(),
        updated_at=datetime(2026, 7, 13, tzinfo=UTC),
    )
    version = SimpleNamespace(
        artifact_id=draft.id,
        id=uuid4(),
        metadata_={},
        prompt_event_ids=[prompt_id],
    )
    db = FakePrepareDB([draft])
    monkeypatch.setattr(
        batches,
        "settings",
        SimpleNamespace(project_memory_batch_max_drafts=3),
    )
    monkeypatch.setattr(
        batches,
        "_latest_versions_by_artifact",
        lambda _db, _ids: {draft.id: version},
    )
    monkeypatch.setattr(batches, "_attach_idempotency_key", lambda _db, batch, _key: batch)
    validated: list[set[str]] = []
    monkeypatch.setattr(
        batches,
        "_validate_memory_review_token",
        lambda **kwargs: validated.append(kwargs["excluded_prompt_event_ids"]),
    )

    batch = batches._prepare_batch(
        db,  # type: ignore[arg-type]
        excluded_prompt_event_ids=[prompt_id],
        idempotency_key="reviewed",
        project_id=project_id,
        review_token="signed-review",
        user_id=uuid4(),
    )

    assert validated == [{str(prompt_id)}]
    assert batch.snapshot_manifest[0]["excluded_prompt_event_ids"] == [str(prompt_id)]


def test_generation_exclusions_filter_only_the_batch_snapshot() -> None:
    prompt_id = uuid4()
    response_id = uuid4()
    retained_prompt_id = uuid4()
    retained_response_id = uuid4()
    metadata = {
        "draft_evidence": {
            "changed_files": [{"path": "src/app.ts", "status": "modified"}],
            "commits": [{"hash": "abc", "message": "Keep metadata"}],
            "events": [
                {
                    "event_type": "PromptSubmitted",
                    "payload": {"prompt": "private", "turn_id": "turn-1"},
                    "sequence": 1,
                },
                {
                    "event_type": "ResponseReceived",
                    "payload": {"response": "private response", "turn_id": "turn-1"},
                    "sequence": 2,
                },
                {
                    "event_type": "PromptSubmitted",
                    "payload": {"prompt": "retained", "turn_id": "turn-2"},
                    "sequence": 3,
                },
                {
                    "event_type": "ResponseReceived",
                    "payload": {"response": "retained response", "turn_id": "turn-2"},
                    "sequence": 4,
                },
            ],
            "prompts": [
                {
                    "event_id": str(prompt_id),
                    "paired_response_event_id": str(response_id),
                    "sequence": 1,
                    "turn_id": "turn-1",
                },
                {
                    "event_id": str(retained_prompt_id),
                    "paired_response_event_id": str(retained_response_id),
                    "sequence": 3,
                    "turn_id": "turn-2",
                },
            ],
            "responses": [
                {"event_id": str(response_id), "sequence": 2, "turn_id": "turn-1"},
                {
                    "event_id": str(retained_response_id),
                    "sequence": 4,
                    "turn_id": "turn-2",
                },
            ],
        },
        "event_count": 4,
    }
    original_metadata = deepcopy(metadata)
    version = SimpleNamespace(
        id=uuid4(),
        metadata_=metadata,
        summary="Summary derived from the private prompt",
        title="Private prompt title",
    )

    title, summary, filtered = batches._filtered_snapshot_values(
        version,  # type: ignore[arg-type]
        excluded_prompt_ids={str(prompt_id)},
    )

    evidence = filtered["draft_evidence"]
    assert title == "Reviewed pending activity"
    assert summary is None
    assert [item["event_id"] for item in evidence["prompts"]] == [
        str(retained_prompt_id)
    ]
    assert [item["event_id"] for item in evidence["responses"]] == [
        str(retained_response_id)
    ]
    assert [item["sequence"] for item in evidence["events"]] == [3, 4]
    assert evidence["changed_files"] == original_metadata["draft_evidence"]["changed_files"]
    assert filtered["review_excluded_prompt_event_ids"] == [str(prompt_id)]
    assert metadata == original_metadata


@pytest.mark.parametrize("status", ["running", "failed", "succeeded", "superseded"])
def test_only_pending_batch_can_be_claimed(status: str) -> None:
    now = datetime.now(UTC)
    batch = SimpleNamespace(
        attempt_count=4,
        lease_expires_at=now - timedelta(minutes=5),
        status=status,
    )
    db = FakeClaimDB(batch)

    claimed_batch, claimed, attempt_count = batches._claim_batch_run(db, uuid4())

    assert claimed_batch is batch
    assert claimed is False
    assert attempt_count == 4
    assert batch.attempt_count == 4
    assert db.commit_count == 1


def test_heartbeat_update_cannot_revive_an_expired_or_null_lease(monkeypatch) -> None:
    db = FakeLeaseDB(rowcount=0)
    monkeypatch.setattr(batches, "SessionLocal", lambda: db)

    assert batches._extend_batch_lease(uuid4(), attempt_count=3) is False

    assert db.statement is not None
    compiled = str(db.statement.compile(dialect=postgresql.dialect()))
    assert "lease_expires_at IS NOT NULL" in compiled
    assert "lease_expires_at >" in compiled
    assert db.commit_count == 1
    assert db.closed is True


def test_batch_chunks_all_sessions_into_one_project_update(monkeypatch) -> None:
    project_id, rows = _batch_rows(first_session_count=7, second_session_count=2)
    generated_calls: list[list[object]] = []
    generations = []
    for session_id in dict.fromkeys(session.id for _, _, _, session in rows):
        session_rows = [row for row in rows if row[3].id == session_id]
        for chunk_rows in batches._chunks(
            session_rows,
            batches.PROJECT_MEMORY_BATCH_CHUNK_SIZE,
        ):
            generations.append(
                batches.PendingChunkGeneration(
                    context={},
                    snapshots=[snapshot for _, _, snapshot, _ in chunk_rows],
                    source_session_id=str(session_id),
                )
            )
    prepared = batches.BatchAttemptSnapshot(
        batch_id=uuid4(),
        chunk_generations=generations,
        project_id=project_id,
        project_name="Promty",
        snapshot_manifest=[{"draft_id": str(draft.id)} for _, draft, _, _ in rows],
        source_session_ids=list(dict.fromkeys(str(session.id) for _, _, _, session in rows)),
    )

    def generate_chunk(*, snapshots, **_kwargs):
        generated_calls.append(snapshots)
        return [_generated_chunk(snapshots)]

    monkeypatch.setattr(batches, "_generate_chunk_payloads", generate_chunk)

    generated_chunks = batches._generate_batch_chunks(prepared)
    memory = batches._prepare_project_batch_memory(
        batch=prepared,
        chunks=generated_chunks,
    )

    assert sorted(len(chunk) for chunk in generated_calls) == [1, 2, 6]
    assert memory.extra_metadata["internal_chunk_count"] == 3
    assert memory.extra_metadata["source_session_ids"] == prepared.source_session_ids
    assert memory.storage_key.endswith(f"/batch/{prepared.batch_id}/memory")


def test_chunk_generation_respects_project_grouping_mode(monkeypatch) -> None:
    _project_id, rows = _batch_rows(first_session_count=7, second_session_count=2)

    def context_for_drafts(_db, session, drafts):
        return {
            "changed_files": [],
            "commits": [],
            "ended_at": None,
            "event_count": len(drafts),
            "events": [],
            "first_event_id": None,
            "last_event_id": None,
            "model": "gpt-test",
            "output_locale": "ko",
            "pending_drafts": [{"id": str(draft.id)} for draft in drafts],
            "project_id": str(session.project_id),
            "project_name": "Promty",
            "prompt_events": [],
            "response_count": 0,
            "responses": [],
            "session_id": str(session.id),
            "started_at": None,
            "tool": "codex-cli",
            "window": {"end_sequence": None, "start_sequence": None},
        }

    monkeypatch.setattr(batches, "_pending_draft_generation_context", context_for_drafts)

    session_generations = batches._chunk_generations_for_rows(
        FakeDB(),  # type: ignore[arg-type]
        rows,
        grouping_mode="session",
    )
    chronological_generations = batches._chunk_generations_for_rows(
        FakeDB(),  # type: ignore[arg-type]
        rows,
        grouping_mode="chronological",
    )

    assert [len(item.snapshots) for item in session_generations] == [6, 1, 2]
    assert [len(item.snapshots) for item in chronological_generations] == [6, 3]
    assert chronological_generations[1].source_session_id == "chronological"
    assert chronological_generations[1].context["source_session_ids"] == [
        str(rows[6][3].id),
        str(rows[7][3].id),
    ]
    assert [
        draft["id"]
        for generation in chronological_generations
        for draft in generation.context["pending_drafts"]
    ] == [str(snapshot.id) for _, _, snapshot, _ in rows]


def test_batch_attempt_uses_grouping_mode_captured_when_queued(monkeypatch) -> None:
    project_id, rows = _batch_rows(first_session_count=7, second_session_count=2)
    batch = SimpleNamespace(
        attempt_count=1,
        chunk_results={},
        grouping_mode="chronological",
        id=uuid4(),
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        project_id=project_id,
        snapshot_manifest=[],
        source_session_ids=list(dict.fromkeys(str(row[3].id) for row in rows)),
        status="running",
    )
    db = FakeAttemptDB(batch, SimpleNamespace(name="Promty"))

    monkeypatch.setattr(batches, "_load_batch_snapshots", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(
        batches,
        "_pending_draft_generation_context",
        lambda _db, session, drafts: {
            "changed_files": [],
            "commits": [],
            "ended_at": None,
            "event_count": len(drafts),
            "events": [],
            "first_event_id": None,
            "last_event_id": None,
            "model": "gpt-test",
            "output_locale": "ko",
            "pending_drafts": [{"id": str(draft.id)} for draft in drafts],
            "project_id": str(session.project_id),
            "project_name": "Promty",
            "prompt_events": [],
            "response_count": 0,
            "responses": [],
            "session_id": str(session.id),
            "started_at": None,
            "tool": "codex-cli",
            "window": {"end_sequence": None, "start_sequence": None},
        },
    )

    prepared = batches._prepare_batch_attempt(
        db,  # type: ignore[arg-type]
        batch_id=batch.id,
        expected_attempt_count=1,
    )

    assert prepared is not None
    assert prepared.grouping_mode == "chronological"
    assert [len(item.snapshots) for item in prepared.chunk_generations] == [6, 3]


def test_batch_memory_keeps_complete_chunk_details() -> None:
    project_id = uuid4()
    snapshots = [_snapshot()]
    chunks = [
        batches.GeneratedChunkPayload(
            first_event_at=None,
            last_event_at=None,
            metadata={},
            payload={
                "outcome": f"Result {index}. " + ("outcome detail " * 80),
                "sections": [
                    {
                        "summary": f"Section {index}. " + ("section detail " * 40),
                        "title": "Tasks",
                    }
                ],
                "summary": f"Summary {index}. " + ("summary detail " * 80),
            },
            source_draft_ids=[str(snapshots[0].id)],
            source_draft_version_ids=[str(snapshots[0].version_id)],
            source_session_id=str(uuid4()),
        )
        for index in range(5)
    ]
    prepared = batches.BatchAttemptSnapshot(
        batch_id=uuid4(),
        chunk_generations=[],
        project_id=project_id,
        project_name="Promty",
        snapshot_manifest=[],
        source_session_ids=[],
    )

    memory = batches._prepare_project_batch_memory(batch=prepared, chunks=chunks)

    assert memory.payload["outcome"] == " ".join(chunk.payload["outcome"] for chunk in chunks)
    assert memory.payload["summary"] == " ".join(chunk.payload["summary"] for chunk in chunks)
    assert memory.payload["sections"] == [
        {
            "summary": " / ".join(
                chunk.payload["sections"][0]["summary"].strip() for chunk in chunks
            ),
            "title": "Tasks",
        }
    ]
    assert "Result 0." in memory.payload["outcome"]
    assert not memory.payload["outcome"].endswith("...")




def test_generation_failure_does_not_consume_any_snapshot_draft(monkeypatch) -> None:
    project_id, rows = _batch_rows(first_session_count=7)
    call_count = 0
    prepared = batches.BatchAttemptSnapshot(
        batch_id=uuid4(),
        chunk_generations=[
            batches.PendingChunkGeneration(
                context={},
                snapshots=[snapshot for _, _, snapshot, _ in chunk_rows],
                source_session_id=str(chunk_rows[0][3].id),
            )
            for chunk_rows in batches._chunks(
                rows,
                batches.PROJECT_MEMORY_BATCH_CHUNK_SIZE,
            )
        ],
        project_id=project_id,
        project_name="Promty",
        snapshot_manifest=[],
        source_session_ids=[],
    )

    def generate_chunk(**_kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise batches.ProjectMemoryBatchGenerationError("provider unavailable")
        return [_generated_chunk(_kwargs["snapshots"])]

    monkeypatch.setattr(batches, "_generate_chunk_payloads", generate_chunk)

    with pytest.raises(batches.ProjectMemoryBatchGenerationError):
        batches._generate_batch_chunks(prepared)

    assert call_count == 2
    assert all(draft.metadata_ == {"review_state": REVIEW_STATE_DRAFT} for _, draft, _, _ in rows)


def test_successful_empty_generation_consumes_snapshot_without_compiling(monkeypatch) -> None:
    project_id, rows = _batch_rows(first_session_count=2)
    batch = SimpleNamespace(id=uuid4(), project_id=project_id)
    batches._consume_batch_snapshots(
        FakeDB(),
        batch=batch,
        generated_chunks=[],
        memory=None,
        rows=rows,
    )

    assert all(draft.metadata_["review_state"] == REVIEW_STATE_IGNORED for _, draft, _, _ in rows)


def test_provider_failure_is_not_treated_as_successful_empty_generation(monkeypatch) -> None:
    snapshot = _snapshot()
    project_id = uuid4()
    session = SimpleNamespace(id=uuid4(), project_id=project_id)

    monkeypatch.setattr(
        batches,
        "_pending_draft_generation_context",
        lambda *_args: {"last_event_id": None},
    )
    monkeypatch.setattr(
        batches,
        "build_memory_draft_payloads_from_context",
        lambda *_args, **_kwargs: (
            [],
            {"fallback_reason": "provider request failed"},
        ),
    )

    with pytest.raises(batches.ProjectMemoryBatchGenerationError):
        batches._generate_chunk_payloads(
            context={"last_event_id": None},
            snapshots=[snapshot],
            source_session_id=str(session.id),
        )


def test_unconfigured_provider_has_a_safe_actionable_failure(monkeypatch) -> None:
    snapshot = _snapshot()

    monkeypatch.setattr(
        batches,
        "build_memory_draft_payloads_from_context",
        lambda *_args, **_kwargs: ([], {"fallback_reason": "local_disabled"}),
    )

    with pytest.raises(
        batches.ProjectMemoryBatchProviderUnavailableError,
        match="not configured",
    ):
        batches._generate_chunk_payloads(
            context={"last_event_id": None},
            snapshots=[snapshot],
            source_session_id=str(uuid4()),
        )


def test_generate_request_enqueues_without_running_provider(monkeypatch) -> None:
    project_id = uuid4()
    batch = SimpleNamespace(id=uuid4(), project_id=project_id, status="pending")
    db = FakeDB()

    monkeypatch.setattr(batches, "_in_progress_batch_by_idempotency_key", lambda *_a, **_k: None)
    monkeypatch.setattr(batches, "_visible_active_batch", lambda *_a, **_k: None)
    monkeypatch.setattr(batches, "lock_project_memory", lambda *_a, **_k: None)
    monkeypatch.setattr(batches, "_batch_by_idempotency_key", lambda *_a, **_k: None)
    monkeypatch.setattr(batches, "_active_batch", lambda *_a, **_k: None)
    monkeypatch.setattr(batches, "_prepare_batch", lambda *_a, **_k: batch)
    monkeypatch.setattr(
        batches,
        "serialize_project_memory_batch",
        lambda _db, value, *, replayed: {
            "batch_id": str(value.id),
            "replayed": replayed,
            "status": "generation_in_progress",
        },
    )
    monkeypatch.setattr(
        batches,
        "_generate_batch_chunks",
        lambda *_a, **_k: pytest.fail("provider work must not run in the request"),
    )

    response = batches.generate_project_memory_batch(
        db,  # type: ignore[arg-type]
        idempotency_key="request-key",
        project_id=project_id,
        user_id=uuid4(),
    )

    assert response["status"] == "generation_in_progress"
    assert response["batch_id"] == str(batch.id)


def test_generation_preview_matches_batch_chunking_and_configured_costs(
    monkeypatch,
) -> None:
    project_id = uuid4()
    session_id = uuid4()
    drafts = [SimpleNamespace(id=uuid4(), session_id=session_id) for _ in range(7)]

    class PreviewDB:
        def scalar(self, _statement):
            return 9

        def execute(self, _statement):
            return FakeScalarResult(drafts)

    monkeypatch.setattr(
        batches,
        "settings",
        SimpleNamespace(
            memory_draft_generator="openai",
            memory_draft_prompt_max_bytes=300,
            memory_provider_output_max_tokens=10,
            openai_api_key="test-key",
            openai_input_usd_per_million_tokens=0.25,
            openai_model="gpt-test",
            openai_output_usd_per_million_tokens=2.0,
            project_memory_batch_max_drafts=60,
            project_memory_generator="openai",
            project_memory_prompt_max_bytes=600,
        ),
    )
    monkeypatch.setattr(
        batches,
        "_pending_draft_range",
        lambda draft: {
            "draft_id": str(draft.id),
            "event_count": 3,
            "file_change_event_count": 1,
            "prompt_count": 2,
            "session_id": str(draft.session_id),
        },
    )

    preview = batches.preview_project_memory_batch(
        PreviewDB(),  # type: ignore[arg-type]
        project_id=project_id,
    )

    assert preview["can_generate"] is True
    assert preview["draft_count"] == 7
    assert preview["session_count"] == 1
    assert preview["estimated_provider_calls"] == 3
    assert preview["estimated_input_tokens"] == 400
    assert preview["estimated_output_tokens"] == 30
    assert preview["estimated_cost_microusd"] == 160
    assert preview["overflow_draft_count"] == 2
    assert preview["prompt_count"] == 14
    assert preview["event_count"] == 21
    assert preview["file_change_event_count"] == 7
    assert preview["one_time_generation"] is True
    assert preview["retryable"] is False


def test_failed_batch_response_does_not_requeue_it(monkeypatch) -> None:
    attempt_count = 4
    batch = SimpleNamespace(
        attempt_count=attempt_count,
        completed_at=datetime.now(UTC),
        error_code="generation_failed",
        error_message="provider unavailable",
        lease_expires_at=datetime.now(UTC),
        result_status="generation_failed",
        status="failed",
        updated_at=datetime.now(UTC),
    )

    monkeypatch.setattr(
        batches,
        "serialize_project_memory_batch",
        lambda _db, value, *, replayed: {
            "batch_status": value.status,
            "replayed": replayed,
        },
    )
    response = batches._queued_batch_response(
        FakeDB(),  # type: ignore[arg-type]
        batch,
        replayed=True,
    )

    assert response == {"batch_status": "failed", "replayed": True}
    assert batch.status == "failed"
    assert batch.attempt_count == attempt_count
    assert batch.error_code == "generation_failed"
    assert batch.error_message == "provider unavailable"


def test_batch_failure_message_persists_http_status_without_secret_detail() -> None:
    secret = "PROVIDER_ERROR_BODY_SECRET_8141b3"
    batch_id = uuid4()
    batch = SimpleNamespace(
        attempt_count=2,
        chunk_results={},
        id=batch_id,
        status="running",
    )
    db = FakeFailureDB(batch)

    batches._record_batch_failure(
        db,  # type: ignore[arg-type]
        batch_id=batch_id,
        error=batches.ProjectMemoryBatchGenerationError(
            f"Provider request failed with HTTP status 503. {secret}"
        ),
        expected_attempt_count=2,
    )

    assert batch.error_code == "generation_failed"
    assert batch.error_message == "Memory provider request failed with HTTP status 503."
    assert secret not in batch.error_message
    assert len(db.statements) == 3
    compiled_update = str(db.statements[1].compile(dialect=postgresql.dialect()))
    compiled_release = str(db.statements[2].compile(dialect=postgresql.dialect()))
    assert "UPDATE artifacts" in compiled_update
    assert "DELETE FROM project_memory_batch_items" in compiled_release
    assert db.flush_count == 1
    assert db.commit_count == 1


def test_provider_unavailable_failure_message_is_safe_and_retryable() -> None:
    error = batches.ProjectMemoryBatchProviderUnavailableError("secret provider detail")

    assert batches._batch_failure_code(error) == "provider_unavailable"
    assert batches._safe_batch_failure_message(error) == (
        "AI generation is not configured. Captured work is safe; try again shortly."
    )


def test_batch_failure_log_does_not_render_exception_or_secret(
    monkeypatch,
    caplog,
) -> None:
    secret = "PROVIDER_ERROR_BODY_SECRET_c1378d"
    batch_id = uuid4()
    failed_batch = SimpleNamespace(id=batch_id, status="failed")
    db = FakeDB()
    monkeypatch.setattr(
        batches,
        "_prepare_batch_attempt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            batches.ProjectMemoryBatchGenerationError(
                f"Provider request failed with HTTP status 500. {secret}"
            )
        ),
    )
    monkeypatch.setattr(
        batches,
        "_record_batch_failure",
        lambda *_args, **_kwargs: failed_batch,
    )
    caplog.set_level(logging.ERROR, logger=batches.logger.name)

    result = batches._execute_claimed_batch(
        db,  # type: ignore[arg-type]
        batch_id=batch_id,
        attempt_count=1,
    )

    assert result is failed_batch
    records = [record for record in caplog.records if record.name == batches.logger.name]
    assert len(records) == 1
    assert records[0].exc_info is None
    assert secret not in caplog.text


def test_run_next_batch_releases_claim_selection_before_execution(monkeypatch) -> None:
    batch_id = uuid4()
    db = FakeDB()
    calls = []
    monkeypatch.setattr(batches, "next_project_memory_batch_id", lambda _db: batch_id)
    monkeypatch.setattr(
        batches,
        "run_project_memory_batch",
        lambda _db, **kwargs: calls.append((db.commit_count, kwargs["batch_id"])),
    )

    assert batches.run_next_project_memory_batch(db) is True  # type: ignore[arg-type]
    assert calls == [(1, batch_id)]


def test_run_next_batch_returns_false_when_queue_is_empty(monkeypatch) -> None:
    db = FakeDB()
    monkeypatch.setattr(batches, "next_project_memory_batch_id", lambda _db: None)

    assert batches.run_next_project_memory_batch(db) is False  # type: ignore[arg-type]
    assert db.commit_count == 1
