from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, engine
from app.models.code_change_patches import CodeChangePatch
from app.models.events import Event
from app.models.project_files import ProjectFile
from app.models.project_stats import ProjectStats
from app.models.prompt_search_documents import PromptSearchDocument
from app.models.sessions import Session as PromptSession
from app.models.users import User
from app.schemas.events import EventCreate
from app.services import events as events_service
from app.services.events import EventIngestConflict, add_events
from app.services.projects.management import list_project_summaries

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


def _event(
    *,
    event_id: UUID | None = None,
    event_type: str = "PromptSubmitted",
    payload: dict[str, Any] | None = None,
    project_id: UUID,
    sequence: int,
    session_id: UUID,
) -> EventCreate:
    default_payloads = {
        "FilesChanged": {"changes": []},
        "PromptSubmitted": {"prompt": f"PostgreSQL ingest test {sequence}"},
        "ResponseReceived": {"response": f"Response {sequence}", "success": True},
        "SessionEnded": {"reason": "test"},
        "SessionStarted": {},
    }
    return EventCreate(
        id=event_id or uuid4(),
        schema_version=1,
        project_id=project_id,
        session_id=session_id,
        sequence=sequence,
        tool="codex-cli",
        event_type=event_type,
        timestamp=datetime(2026, 7, 13, tzinfo=timezone.utc) + timedelta(seconds=sequence),
        payload=payload if payload is not None else default_payloads[event_type],
    )


def test_same_session_batch_has_bounded_sql_and_flush_counts(db: Session) -> None:
    project_id = uuid4()
    session_id = uuid4()
    events = [
        _event(project_id=project_id, session_id=session_id, sequence=index)
        for index in range(1, 101)
    ]
    statement_count = 0
    flush_count = 0
    statements: list[str] = []

    def count_statement(
        _connection: Any,
        _cursor: Any,
        statement: str,
        *_args: Any,
    ) -> None:
        nonlocal statement_count
        statement_count += 1
        statements.append(statement)

    def count_flush(*_args: Any) -> None:
        nonlocal flush_count
        flush_count += 1

    sqlalchemy_event.listen(engine, "before_cursor_execute", count_statement)
    sqlalchemy_event.listen(db, "after_flush", count_flush)
    try:
        assert add_events(db, events) == [str(event.id) for event in events]
        db.flush()
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", count_statement)

    assert statement_count <= 20
    assert flush_count <= 2
    assert any(
        "FROM sessions" in statement and "FOR UPDATE" in statement for statement in statements
    )
    prompt_session = db.get(PromptSession, session_id)
    assert prompt_session is not None
    assert prompt_session.last_activity_at == events[-1].timestamp
    stats = db.get(ProjectStats, project_id)
    assert stats is not None
    assert stats.session_count == 1
    assert stats.event_count == 100
    assert stats.prompt_count == 100
    assert stats.latest_event_at == events[-1].timestamp


def test_many_session_prompt_batch_does_not_run_memory_queries_per_session(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = uuid4()
    events = [
        _event(
            project_id=project_id,
            session_id=uuid4(),
            sequence=1,
        )
        for _index in range(100)
    ]
    statement_count = 0

    def count_statement(*_args: Any) -> None:
        nonlocal statement_count
        statement_count += 1

    monkeypatch.setattr(
        events_service,
        "generate_due_memory_artifacts_for_session",
        lambda *_args, **_kwargs: pytest.fail(
            "prompt-only sessions cannot have a complete memory window"
        ),
    )
    sqlalchemy_event.listen(engine, "before_cursor_execute", count_statement)
    try:
        add_events(db, events)
        db.flush()
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", count_statement)

    assert statement_count <= 20


def test_multi_session_memory_prefilter_runs_only_complete_first_windows(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = uuid4()
    due_session_id = uuid4()
    reasoning_session_id = uuid4()
    finalized_session_id = uuid4()
    generated: list[tuple[UUID, bool]] = []
    monkeypatch.setattr(events_service, "memory_slice_prompt_target", lambda: 1)
    monkeypatch.setattr(
        events_service,
        "generate_due_memory_artifacts_for_session",
        lambda _db, session, *, finalize: generated.append((session.id, finalize)),
    )

    add_events(
        db,
        [
            _event(project_id=project_id, session_id=due_session_id, sequence=1),
            _event(
                event_type="ResponseReceived",
                project_id=project_id,
                session_id=due_session_id,
                sequence=2,
            ),
            _event(
                event_type="FilesChanged",
                project_id=project_id,
                session_id=due_session_id,
                sequence=3,
            ),
            _event(
                project_id=project_id,
                session_id=reasoning_session_id,
                sequence=1,
            ),
            _event(
                event_type="ResponseReceived",
                project_id=project_id,
                session_id=reasoning_session_id,
                sequence=2,
            ),
            _event(
                project_id=project_id,
                session_id=finalized_session_id,
                sequence=1,
            ),
            _event(
                event_type="ResponseReceived",
                project_id=project_id,
                session_id=finalized_session_id,
                sequence=2,
            ),
            _event(
                event_type="FilesChanged",
                project_id=project_id,
                session_id=finalized_session_id,
                sequence=3,
            ),
            _event(
                event_type="SessionEnded",
                project_id=project_id,
                session_id=finalized_session_id,
                sequence=4,
            ),
        ],
    )

    assert sorted(generated, key=lambda item: str(item[0])) == sorted(
        [
            (due_session_id, False),
            (reasoning_session_id, False),
            (finalized_session_id, True),
        ],
        key=lambda item: str(item[0]),
    )


def test_memory_prefilter_skips_empty_or_metadata_only_responses(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = uuid4()
    empty_response_session_id = uuid4()
    metadata_only_session_id = uuid4()
    generated: list[tuple[UUID, bool]] = []
    monkeypatch.setattr(events_service, "memory_slice_prompt_target", lambda: 1)
    monkeypatch.setattr(
        events_service,
        "generate_due_memory_artifacts_for_session",
        lambda _db, session, *, finalize: generated.append((session.id, finalize)),
    )

    add_events(
        db,
        [
            _event(
                project_id=project_id,
                session_id=empty_response_session_id,
                sequence=1,
            ),
            _event(
                event_type="ResponseReceived",
                payload={
                    "response": "",
                    "response_original_length": 0,
                    "success": True,
                },
                project_id=project_id,
                session_id=empty_response_session_id,
                sequence=2,
            ),
            _event(
                project_id=project_id,
                session_id=metadata_only_session_id,
                sequence=1,
            ),
            _event(
                event_type="ResponseReceived",
                payload={"success": True},
                project_id=project_id,
                session_id=metadata_only_session_id,
                sequence=2,
            ),
        ],
    )

    assert generated == []


def test_late_prompt_can_complete_a_window_after_response_and_files(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = uuid4()
    session_id = uuid4()
    generated: list[tuple[UUID, bool]] = []
    monkeypatch.setattr(events_service, "memory_slice_prompt_target", lambda: 1)
    monkeypatch.setattr(
        events_service,
        "generate_due_memory_artifacts_for_session",
        lambda _db, session, *, finalize: generated.append((session.id, finalize)),
    )

    add_events(
        db,
        [
            _event(
                event_type="ResponseReceived",
                project_id=project_id,
                session_id=session_id,
                sequence=2,
            ),
            _event(
                event_type="FilesChanged",
                project_id=project_id,
                session_id=session_id,
                sequence=3,
            ),
        ],
    )
    assert generated == []

    add_events(
        db,
        [
            _event(
                project_id=project_id,
                session_id=session_id,
                sequence=1,
            )
        ],
    )

    assert generated == [(session_id, False)]


def test_duplicate_id_rules_are_preserved(db: Session) -> None:
    project_id = uuid4()
    session_id = uuid4()
    original = _event(project_id=project_id, session_id=session_id, sequence=1)

    accepted = add_events(db, [original, original])
    db.flush()

    assert accepted == [str(original.id), str(original.id)]
    assert db.scalar(select(func.count(Event.id)).where(Event.id == original.id)) == 1
    assert (
        db.scalar(
            select(func.count(PromptSearchDocument.id)).where(
                PromptSearchDocument.prompt_event_id == original.id
            )
        )
        == 1
    )

    changed = _event(
        event_id=original.id,
        payload={"prompt": "different content"},
        project_id=project_id,
        session_id=session_id,
        sequence=1,
    )
    with pytest.raises(EventIngestConflict, match="different content"):
        add_events(db, [changed])


def test_duplicate_sequence_inside_batch_is_rejected_before_flush(db: Session) -> None:
    project_id = uuid4()
    session_id = uuid4()
    first = _event(project_id=project_id, session_id=session_id, sequence=1)
    second = _event(project_id=project_id, session_id=session_id, sequence=1)

    with pytest.raises(EventIngestConflict, match="sequence already exists"):
        add_events(db, [first, second])

    assert not db.new


def test_database_replay_updates_no_duplicate_rows(db: Session) -> None:
    project_id = uuid4()
    session_id = uuid4()
    original = _event(project_id=project_id, session_id=session_id, sequence=1)

    add_events(db, [original])
    db.flush()
    add_events(db, [original])
    db.flush()

    assert db.scalar(select(func.count(Event.id)).where(Event.id == original.id)) == 1
    assert (
        db.scalar(
            select(func.count(PromptSearchDocument.id)).where(
                PromptSearchDocument.prompt_event_id == original.id
            )
        )
        == 1
    )
    stats = db.get(ProjectStats, project_id)
    assert stats is not None
    assert stats.event_count == 1
    assert stats.prompt_count == 1
    assert stats.session_count == 1


def test_files_changed_batch_keeps_last_file_state_and_all_patches(db: Session) -> None:
    project_id = uuid4()
    session_id = uuid4()
    first = _event(
        event_type="FilesChanged",
        payload={
            "changes": [{"path": "backend/app/main.py", "status": "added", "patch": "+first"}]
        },
        project_id=project_id,
        session_id=session_id,
        sequence=1,
    )
    second = _event(
        event_type="FilesChanged",
        payload={
            "changes": [
                {
                    "path": "backend/app/main.py",
                    "status": "deleted",
                    "patch": "-second",
                }
            ]
        },
        project_id=project_id,
        session_id=session_id,
        sequence=2,
    )

    add_events(db, [first, second])
    db.flush()

    project_file = db.scalar(
        select(ProjectFile).where(
            ProjectFile.project_id == project_id,
            ProjectFile.path == "backend/app/main.py",
        )
    )
    assert project_file is not None
    assert project_file.status == "deleted"
    assert project_file.last_event_id == second.id
    stats = db.get(ProjectStats, project_id)
    assert stats is not None
    assert stats.tracked_files == 0
    assert (
        db.scalar(
            select(func.count(CodeChangePatch.id)).where(CodeChangePatch.project_id == project_id)
        )
        == 2
    )


def test_project_list_reads_incremental_activity_rollup(db: Session) -> None:
    marker = str(uuid4())
    owner = User(
        github_id=f"rollup-{marker}",
        email=f"rollup-{marker}@example.com",
        username=f"rollup-{marker}",
    )
    db.add(owner)
    db.flush()
    project_id = uuid4()
    session_id = uuid4()
    events = [
        _event(project_id=project_id, session_id=session_id, sequence=1),
        _event(
            event_type="ResponseReceived",
            project_id=project_id,
            session_id=session_id,
            sequence=2,
        ),
    ]
    add_events(db, events, owner=owner)
    db.flush()
    statements: list[str] = []

    def capture_statement(
        _connection: Any,
        _cursor: Any,
        statement: str,
        *_args: Any,
    ) -> None:
        statements.append(statement)

    sqlalchemy_event.listen(engine, "before_cursor_execute", capture_statement)
    try:
        summaries = list_project_summaries(db, current_user=owner)
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", capture_statement)

    assert len(summaries) == 1
    assert summaries[0]["events"] == 2
    assert summaries[0]["prompts"] == 1
    assert summaries[0]["sessions"] == 1
    assert len(statements) == 2
    assert "project_stats" in statements[0]
    assert "FROM events" not in statements[0]
    assert "project_files" not in statements[0]


def test_session_project_and_owner_checks_are_preserved(db: Session) -> None:
    first_owner = User(
        github_id=f"owner-{uuid4()}",
        username=f"owner-{uuid4()}",
    )
    second_owner = User(
        github_id=f"owner-{uuid4()}",
        username=f"owner-{uuid4()}",
    )
    db.add_all((first_owner, second_owner))
    db.flush()
    project_id = uuid4()
    session_id = uuid4()
    first = _event(project_id=project_id, session_id=session_id, sequence=1)
    add_events(db, [first], owner=first_owner)
    db.flush()

    foreign_session_event = _event(
        project_id=uuid4(),
        session_id=session_id,
        sequence=2,
    )
    with pytest.raises(EventIngestConflict, match="different project_id"):
        add_events(db, [foreign_session_event], owner=first_owner)

    second_session_event = _event(
        project_id=project_id,
        session_id=uuid4(),
        sequence=1,
    )
    with pytest.raises(EventIngestConflict, match="different user"):
        add_events(db, [second_session_event], owner=second_owner)

    session = db.get(PromptSession, session_id)
    assert session is not None
    assert session.project_id == project_id
