from __future__ import annotations

from datetime import UTC, datetime
from importlib import util
from pathlib import Path
from threading import Event, Lock
import time
from types import ModuleType, SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.models.project_memory_batches import ProjectMemoryBatch
from app.services.memory import batches


MIGRATION_PATH = (
    Path(__file__).parents[1] / "alembic" / "versions" / "0024_memory_chunk_progress.py"
)


def _snapshot(index: int) -> batches.PendingDraftSnapshot:
    return batches.PendingDraftSnapshot(
        created_at=datetime(2026, 7, 13, tzinfo=UTC),
        id=uuid4(),
        metadata_={},
        summary=f"summary-{index}",
        title=str(index),
        version_id=uuid4(),
    )


def _generation(index: int) -> batches.PendingChunkGeneration:
    return batches.PendingChunkGeneration(
        context={},
        snapshots=[_snapshot(index)],
        source_session_id=str(uuid4()),
    )


def _chunk(generation: batches.PendingChunkGeneration) -> batches.GeneratedChunkPayload:
    return batches.GeneratedChunkPayload(
        first_event_at=None,
        last_event_at=None,
        metadata={},
        payload={"summary": generation.snapshots[0].title},
        source_draft_ids=[str(snapshot.id) for snapshot in generation.snapshots],
        source_draft_version_ids=[str(snapshot.version_id) for snapshot in generation.snapshots],
        source_session_id=generation.source_session_id,
    )


def _prepared(
    generations: list[batches.PendingChunkGeneration],
    *,
    chunk_results: dict[str, list[batches.GeneratedChunkPayload]] | None = None,
) -> batches.BatchAttemptSnapshot:
    return batches.BatchAttemptSnapshot(
        batch_id=uuid4(),
        chunk_generations=generations,
        chunk_results=chunk_results or {},
        project_id=uuid4(),
        project_name="Promty",
        snapshot_manifest=[],
        source_session_ids=[],
    )


def test_chunk_key_is_derived_from_ordered_immutable_draft_versions() -> None:
    first = _generation(1)
    same_versions = batches.PendingChunkGeneration(
        context={"changed": True},
        snapshots=list(first.snapshots),
        source_session_id=str(uuid4()),
    )
    reversed_versions = batches.PendingChunkGeneration(
        context={},
        snapshots=[_snapshot(2), *first.snapshots],
        source_session_id=first.source_session_id,
    )

    assert batches._chunk_key(first) == batches._chunk_key(same_versions)
    assert batches._chunk_key(first) != batches._chunk_key(reversed_versions)


def test_chunk_generation_is_bounded_parallel_and_returns_snapshot_order(monkeypatch) -> None:
    generations = [_generation(index) for index in range(4)]
    active = 0
    maximum_active = 0
    lock = Lock()

    def generate(*, snapshots, **_kwargs):
        nonlocal active, maximum_active
        index = int(snapshots[0].title)
        with lock:
            active += 1
            maximum_active = max(maximum_active, active)
        try:
            time.sleep((4 - index) * 0.01)
            return [_chunk(generations[index])]
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(
        batches,
        "settings",
        SimpleNamespace(memory_worker_chunk_concurrency=2),
    )
    monkeypatch.setattr(batches, "_generate_chunk_payloads", generate)

    generated = batches._generate_batch_chunks(_prepared(generations))

    assert maximum_active == 2
    assert [chunk.payload["summary"] for chunk in generated] == ["0", "1", "2", "3"]


def test_first_chunk_failure_stops_submitting_unstarted_provider_work(
    monkeypatch,
) -> None:
    generations = [_generation(index) for index in range(6)]
    started: list[str] = []
    submitted = 0
    lock = Lock()
    two_started = Event()
    failure_started = Event()
    real_executor = batches.ThreadPoolExecutor

    class RecordingExecutor(real_executor):
        def submit(self, *args, **kwargs):
            nonlocal submitted
            submitted += 1
            return super().submit(*args, **kwargs)

    def generate(*, snapshots, **_kwargs):
        index = snapshots[0].title
        with lock:
            started.append(index)
            if len(started) == 2:
                two_started.set()
        assert two_started.wait(timeout=1)
        if index == "0":
            failure_started.set()
            raise batches.ProjectMemoryBatchGenerationError("chunk failed")
        assert failure_started.wait(timeout=1)
        time.sleep(0.05)
        return [_chunk(generations[int(index)])]

    monkeypatch.setattr(
        batches,
        "settings",
        SimpleNamespace(memory_worker_chunk_concurrency=2),
    )
    monkeypatch.setattr(batches, "ThreadPoolExecutor", RecordingExecutor)
    monkeypatch.setattr(batches, "_generate_chunk_payloads", generate)

    with pytest.raises(batches.ProjectMemoryBatchGenerationError, match="chunk failed"):
        batches._generate_batch_chunks(_prepared(generations))

    assert submitted == 2
    assert set(started) == {"0", "1"}


def test_retry_reuses_successful_and_empty_chunk_progress(monkeypatch) -> None:
    generations = [_generation(index) for index in range(3)]
    cached = _chunk(generations[0])
    cached_result = batches._deserialize_chunk_result(
        {
            "draft_version_ids": cached.source_draft_version_ids,
            "payloads": [batches._serialize_generated_chunk(cached)],
        },
        generation=generations[0],
    )
    empty_result = batches._deserialize_chunk_result(
        {
            "draft_version_ids": [
                str(snapshot.version_id) for snapshot in generations[1].snapshots
            ],
            "payloads": [],
        },
        generation=generations[1],
    )
    chunk_results = {
        batches._chunk_key(generations[0]): cached_result,
        batches._chunk_key(generations[1]): empty_result,
    }
    called: list[str] = []

    def generate(*, snapshots, **_kwargs):
        called.append(snapshots[0].title)
        return [_chunk(generations[2])]

    monkeypatch.setattr(batches, "_generate_chunk_payloads", generate)

    generated = batches._generate_batch_chunks(_prepared(generations, chunk_results=chunk_results))

    assert called == ["2"]
    assert [chunk.payload["summary"] for chunk in generated] == ["0", "2"]


def test_chunk_concurrency_setting_defaults_bounded_and_supports_aliases(monkeypatch) -> None:
    monkeypatch.delenv("PROMTY_MEMORY_WORKER_CHUNK_CONCURRENCY", raising=False)
    monkeypatch.delenv("PROMTY_MEMORY_WORKER_CHUNK_CONCURRENCY", raising=False)
    assert Settings().memory_worker_chunk_concurrency == 1

    monkeypatch.setenv("PROMTY_MEMORY_WORKER_CHUNK_CONCURRENCY", "4")
    assert Settings().memory_worker_chunk_concurrency == 4

    monkeypatch.setenv("PROMTY_MEMORY_WORKER_CHUNK_CONCURRENCY", "0")
    assert Settings().memory_worker_chunk_concurrency == 1


def test_batch_draft_cap_defaults_bounded_and_supports_aliases(monkeypatch) -> None:
    monkeypatch.delenv("PROMTY_PROJECT_MEMORY_BATCH_MAX_DRAFTS", raising=False)
    monkeypatch.delenv("PROMTY_PROJECT_MEMORY_BATCH_MAX_DRAFTS", raising=False)
    assert Settings().project_memory_batch_max_drafts == 60

    monkeypatch.setenv("PROMTY_PROJECT_MEMORY_BATCH_MAX_DRAFTS", "12")
    assert Settings().project_memory_batch_max_drafts == 12

    monkeypatch.setenv("PROMTY_PROJECT_MEMORY_BATCH_MAX_DRAFTS", "0")
    assert Settings().project_memory_batch_max_drafts == 1


def test_batch_draft_cap_bounds_futures_and_chunk_progress(monkeypatch) -> None:
    max_drafts = 60
    generations = [_generation(index) for index in range(max_drafts)]
    by_title = {generation.snapshots[0].title: generation for generation in generations}
    persisted_keys: list[str] = []
    submitted_futures = 0
    real_executor = batches.ThreadPoolExecutor

    class RecordingExecutor(real_executor):
        def submit(self, *args, **kwargs):
            nonlocal submitted_futures
            submitted_futures += 1
            return super().submit(*args, **kwargs)

    def generate(*, snapshots, **_kwargs):
        return [_chunk(by_title[snapshots[0].title])]

    def persist(*, chunk_key, **_kwargs):
        persisted_keys.append(chunk_key)

    monkeypatch.setattr(
        batches,
        "settings",
        SimpleNamespace(memory_worker_chunk_concurrency=2),
    )
    monkeypatch.setattr(batches, "ThreadPoolExecutor", RecordingExecutor)
    monkeypatch.setattr(batches, "_generate_chunk_payloads", generate)
    monkeypatch.setattr(batches, "_persist_chunk_result", persist)

    generated = batches._generate_batch_chunks(
        _prepared(generations),
        expected_attempt_count=1,
    )

    assert submitted_futures == max_drafts
    assert len(persisted_keys) == max_drafts
    assert len(set(persisted_keys)) == max_drafts
    assert len(generated) == max_drafts


class _ProgressDB:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount
        self.statement = None
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def execute(self, statement):
        self.statement = statement
        return SimpleNamespace(rowcount=self.rowcount)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def test_chunk_checkpoint_is_atomically_attempt_and_lease_fenced(monkeypatch) -> None:
    generation = _generation(0)
    db = _ProgressDB(rowcount=1)
    monkeypatch.setattr(batches, "SessionLocal", lambda: db)

    batches._persist_chunk_result(
        batch_id=uuid4(),
        chunk_key=batches._chunk_key(generation),
        chunks=[_chunk(generation)],
        expected_attempt_count=7,
        generation=generation,
    )

    sql = str(db.statement)
    assert "chunk_results || CAST" in sql
    assert "project_memory_batches.status" in sql
    assert "project_memory_batches.attempt_count" in sql
    assert "project_memory_batches.lease_expires_at" in sql
    assert db.commits == 1
    assert db.rollbacks == 0
    assert db.closed is True


def test_chunk_checkpoint_rejects_a_stale_attempt(monkeypatch) -> None:
    generation = _generation(0)
    db = _ProgressDB(rowcount=0)
    monkeypatch.setattr(batches, "SessionLocal", lambda: db)

    with pytest.raises(batches.ProjectMemoryBatchLeaseLostError):
        batches._persist_chunk_result(
            batch_id=uuid4(),
            chunk_key=batches._chunk_key(generation),
            chunks=[_chunk(generation)],
            expected_attempt_count=2,
            generation=generation,
        )

    assert db.commits == 0
    assert db.rollbacks == 1
    assert db.closed is True


class _RecordingOperations:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str):
        def record(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))

        return record


def _load_migration() -> ModuleType:
    spec = util.spec_from_file_location("memory_chunk_progress_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_chunk_progress_model_and_migration_are_non_nullable_jsonb(monkeypatch) -> None:
    assert ProjectMemoryBatch.__table__.c.chunk_results.nullable is False

    migration = _load_migration()
    operations = _RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    migration.upgrade()

    assert len(operations.calls) == 1
    name, args, _kwargs = operations.calls[0]
    assert name == "add_column"
    assert args[0] == "project_memory_batches"
    assert args[1].name == "chunk_results"
    assert args[1].nullable is False
    assert str(args[1].server_default.arg) == "'{}'::jsonb"


def test_chunk_progress_migration_downgrade_drops_column(monkeypatch) -> None:
    migration = _load_migration()
    operations = _RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)
    migration.downgrade()

    assert operations.calls == [("drop_column", ("project_memory_batches", "chunk_results"), {})]
