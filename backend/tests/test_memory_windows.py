from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.core.config import Settings
from app.services.memory import artifacts as memory_artifacts
from app.services.memory import context as memory_context
from app.services.memory import windows


class FakeResult:
    def __init__(self, value) -> None:
        self.value = value

    def scalars(self):
        values = self.value if isinstance(self.value, list) else []
        return iter(values)

    def scalar_one_or_none(self):
        return self.value

    def one(self):
        return self.value

    def one_or_none(self):
        return self.value


class QueueDB:
    def __init__(self, *responses) -> None:
        self.responses = list(responses)
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        return FakeResult(self.responses.pop(0))


def _session():
    return SimpleNamespace(id=uuid4(), project_id=uuid4())


def _event(event_type: str, sequence: int, payload: dict | None = None):
    return SimpleNamespace(
        created_at=datetime(2026, 7, 13, 12, sequence, tzinfo=UTC),
        event_type=event_type,
        id=uuid4(),
        payload=payload or {},
        sequence=sequence,
        tool="codex-cli",
    )


def test_due_prompt_count_window_limits_prompt_scan_and_reuses_event_rows(
    monkeypatch,
) -> None:
    session = _session()
    prompts = [
        _event("PromptSubmitted", 1),
        _event("PromptSubmitted", 4),
        _event("PromptSubmitted", 8),
    ]
    window_events = [
        prompts[0],
        prompts[1],
        _event("ResponseReceived", 5, {"response": "done"}),
        _event("FilesChanged", 6),
    ]
    db = QueueDB(prompts, (True, True), window_events)
    monkeypatch.setattr(windows, "memory_slice_prompt_target", lambda: 2)

    window = windows.due_memory_window(
        db,
        session,
        after_sequence=None,
        finalize=False,
    )

    assert window is not None
    assert window["start_sequence"] == 1
    assert window["end_sequence"] == 7
    assert window["selected_prompts"] == prompts[:2]
    assert window["events"] == window_events
    assert len(db.statements) == 3
    assert db.statements[0]._limit_clause.value == 3
    assert db.statements[-1]._limit_clause.value == 500


def test_due_prompt_count_window_uses_latest_event_only_without_next_prompt(
    monkeypatch,
) -> None:
    session = _session()
    prompts = [_event("PromptSubmitted", 1), _event("PromptSubmitted", 4)]
    latest_event = _event("FilesChanged", 6)
    window_events = [
        *prompts,
        _event("ResponseReceived", 5, {"response": "done"}),
        latest_event,
    ]
    db = QueueDB(prompts, latest_event, (True, True), window_events)
    monkeypatch.setattr(windows, "memory_slice_prompt_target", lambda: 2)

    window = windows.due_memory_window(
        db,
        session,
        after_sequence=None,
        finalize=False,
    )

    assert window is not None
    assert window["end_sequence"] == latest_event.sequence
    assert window["events"] == window_events
    assert len(db.statements) == 4


def test_due_memory_window_does_not_scan_latest_event_below_target(
    monkeypatch,
) -> None:
    session = _session()
    prompts = [_event("PromptSubmitted", 1)]
    db = QueueDB(prompts)
    monkeypatch.setattr(windows, "memory_slice_prompt_target", lambda: 2)

    window = windows.due_memory_window(
        db,
        session,
        after_sequence=None,
        finalize=False,
    )

    assert window is None
    assert len(db.statements) == 1
    assert db.statements[0]._limit_clause.value == 3


def test_due_final_window_classifies_response_only_as_reasoning(
    monkeypatch,
) -> None:
    session = _session()
    prompt = _event("PromptSubmitted", 4)
    latest_event = _event("FilesChanged", 7)
    valid_events = [
        prompt,
        _event("ResponseReceived", 6, {"response": "done"}),
        latest_event,
    ]
    reasoning_latest_event = _event("ResponseReceived", 6, {"response": "done"})
    reasoning_events = [prompt, reasoning_latest_event]
    monkeypatch.setattr(windows, "memory_slice_prompt_target", lambda: 2)

    valid = windows.due_memory_window(
        QueueDB([prompt], prompt, latest_event, (True, True), valid_events),
        session,
        after_sequence=3,
        finalize=True,
    )
    missing_files = windows.due_memory_window(
        QueueDB(
            [prompt],
            prompt,
            reasoning_latest_event,
            (True, False),
            reasoning_events,
        ),
        session,
        after_sequence=3,
        finalize=True,
    )

    assert valid is not None
    assert valid["reason"] == "session_finalized"
    assert valid["memory_slice_kind"] == "implementation"
    assert valid["start_sequence"] == 4
    assert valid["end_sequence"] == 7
    assert valid["events"] == valid_events
    assert missing_files is not None
    assert missing_files["memory_slice_kind"] == "reasoning"
    assert missing_files["events"] == reasoning_events


def test_memory_slice_state_reads_artifacts_once() -> None:
    session = _session()
    db = QueueDB((40, 4))

    assert windows.memory_slice_state(db, session) == (40, 5)
    assert len(db.statements) == 1
    assert len(db.statements[0].selected_columns) == 2
    assert db.statements[0]._limit_clause.value == 1


def test_memory_materialization_takes_a_session_row_lock() -> None:
    session = _session()
    db = QueueDB(session.id)

    memory_artifacts._lock_memory_materialization_session(db, session)

    assert len(db.statements) == 1
    assert "FOR UPDATE" in str(db.statements[0])


def test_memory_slice_materialization_state_resumes_only_unfinished_group() -> None:
    session = _session()

    assert windows.memory_slice_materialization_state(QueueDB((40, 4, 70)), session) == (
        40,
        5,
        70,
    )
    assert windows.memory_slice_materialization_state(QueueDB((70, 5, 70)), session) == (
        70,
        6,
        None,
    )
    assert windows.memory_slice_runtime_state(QueueDB((70, 5, 70, True)), session) == (
        70,
        6,
        None,
        True,
    )
    assert windows.memory_slice_runtime_state(QueueDB(None), session) == (
        None,
        1,
        None,
        False,
    )


def test_long_prompt_gap_is_split_into_bounded_contiguous_slices(monkeypatch) -> None:
    session = _session()
    events = [
        _event("PromptSubmitted", 1),
        _event("CommitCreated", 2),
        _event("FilesChanged", 3),
        _event("ResponseReceived", 4, {"response": "first"}),
        _event("CommitCreated", 5),
        _event("FilesChanged", 6),
        _event("ResponseReceived", 7, {"response": "middle"}),
        _event("CommitCreated", 8),
        _event("FilesChanged", 9),
        _event("PromptSubmitted", 10),
        _event("ResponseReceived", 11, {"response": "final"}),
        _event("FilesChanged", 12),
    ]
    next_prompt = _event("PromptSubmitted", 13)
    monkeypatch.setattr(windows, "memory_slice_event_max_rows", lambda: 4)
    monkeypatch.setattr(windows, "memory_slice_prompt_target", lambda: 2)

    first_db = QueueDB(
        [events[0], events[9], next_prompt],
        (True, True),
        events[:4],
        events[4],
    )
    first = windows.due_memory_window(
        first_db,
        session,
        after_sequence=None,
        finalize=False,
    )
    assert first is not None

    second_db = QueueDB(events[0], events[4:8], events[8])
    second = windows.due_memory_window(
        second_db,
        session,
        after_sequence=first["end_sequence"],
        continuation_end_sequence=first["materialization_end_sequence"],
        finalize=False,
    )
    assert second is not None

    third_db = QueueDB(events[0], events[8:12], None)
    third = windows.due_memory_window(
        third_db,
        session,
        after_sequence=second["end_sequence"],
        continuation_end_sequence=second["materialization_end_sequence"],
        finalize=False,
    )
    assert third is not None

    slices = [first, second, third]
    covered_sequences = [event.sequence for item in slices for event in item["events"]]
    assert covered_sequences == list(range(1, 13))
    assert len(covered_sequences) == len(set(covered_sequences))
    assert [(item["start_sequence"], item["end_sequence"]) for item in slices] == [
        (1, 4),
        (5, 8),
        (9, 12),
    ]
    assert all(len(item["events"]) <= 4 for item in slices)
    assert [item["window_truncated"] for item in slices] == [True, True, False]
    assert [item["reason"] for item in slices] == [
        "prompt_count",
        "event_count_continuation",
        "event_count_continuation",
    ]
    assert first["context_prompt"] is None
    assert second["context_prompt"] is events[0]
    assert second["context_prompt"].sequence < second["start_sequence"]
    assert third["context_prompt"] is None
    for db in (first_db, second_db, third_db):
        limits = [
            statement._limit_clause.value
            for statement in db.statements
            if statement._limit_clause is not None
        ]
        assert limits
        assert max(limits) <= 4


def test_memory_slice_event_limit_env_aliases_and_prompt_target_clamp(
    monkeypatch,
) -> None:
    monkeypatch.delenv("PROMTY_MEMORY_SLICE_EVENT_MAX_ROWS", raising=False)
    monkeypatch.delenv("PROMTY_MEMORY_SLICE_EVENT_MAX_ROWS", raising=False)
    assert Settings().memory_slice_event_max_rows == 500

    monkeypatch.setenv("PROMTY_MEMORY_SLICE_EVENT_MAX_ROWS", "7")
    assert Settings().memory_slice_event_max_rows == 7

    monkeypatch.setenv("PROMTY_MEMORY_SLICE_EVENT_MAX_ROWS", "1")
    configured = Settings()
    assert configured.memory_slice_event_max_rows == 2
    monkeypatch.setattr(windows, "settings", configured)
    assert windows.memory_slice_prompt_target() == 1

    monkeypatch.delenv("PROMTY_MEMORY_SLICE_MAX_SLICES_PER_CALL", raising=False)
    monkeypatch.setenv("PROMTY_MEMORY_SLICE_MAX_SLICES_PER_CALL", "6")
    assert Settings().memory_slice_max_slices_per_call == 6


def test_materialization_call_has_a_hard_slice_ceiling(monkeypatch) -> None:
    session = _session()
    calls: list[int | None] = []

    def due_window(
        _db,
        _session,
        *,
        after_sequence,
        continuation_end_sequence,
        finalize,
    ):
        assert continuation_end_sequence is None
        assert finalize is False
        calls.append(after_sequence)
        start = 1 if after_sequence is None else after_sequence + 1
        event = _event("PromptSubmitted", start)
        return {
            "context_prompt": None,
            "end_sequence": start,
            "event_row_limit": 2,
            "events": [event],
            "materialization_end_sequence": start,
            "reason": "prompt_count",
            "selected_prompts": [event],
            "start_sequence": start,
            "window_truncated": False,
        }

    monkeypatch.setattr(
        memory_artifacts,
        "_memory_slice_runtime_state",
        lambda *_args: (None, 1, None, False),
    )
    monkeypatch.setattr(
        memory_artifacts,
        "_lock_memory_materialization_session",
        lambda *_args: None,
    )
    monkeypatch.setattr(memory_artifacts, "_due_memory_window", due_window)
    monkeypatch.setattr(
        memory_artifacts,
        "_build_session_memory_context",
        lambda *_args, **_kwargs: {"last_event_id": None},
    )
    monkeypatch.setattr(
        memory_artifacts,
        "_generate_pending_draft_for_context",
        lambda *_args, **_kwargs: SimpleNamespace(id=uuid4(), metadata_={}, updated_at=None),
    )
    monkeypatch.setattr(
        memory_artifacts,
        "settings",
        SimpleNamespace(memory_slice_max_slices_per_call=2),
    )

    generated = memory_artifacts.generate_due_memory_artifacts_for_session(
        object(),
        session,
    )

    assert len(generated) == 2
    assert calls == [None, 1]
    assert generated[-1].metadata_["memory_resume_required"] is True


def test_worker_resume_marker_covers_window_after_exact_slice_boundary(monkeypatch) -> None:
    session = _session()
    session.ended_at = datetime(2026, 7, 13, 13, 0, tzinfo=UTC)
    artifacts: list[SimpleNamespace] = []

    def load_state(_db, _session):
        if not artifacts:
            return None, 1, None, False
        covered_end = max(artifact.metadata_["end_sequence"] for artifact in artifacts)
        materialization_end = max(
            artifact.metadata_["materialization_end_sequence"] for artifact in artifacts
        )
        return (
            covered_end,
            max(artifact.metadata_["slice_index"] for artifact in artifacts) + 1,
            materialization_end if materialization_end > covered_end else None,
            any(artifact.metadata_.get("memory_resume_required") is True for artifact in artifacts),
        )

    def due_window(
        _db,
        _session,
        *,
        after_sequence,
        continuation_end_sequence,
        finalize,
    ):
        assert continuation_end_sequence is None
        assert finalize is True
        if after_sequence == 3:
            return None
        sequence = 1 if after_sequence is None else after_sequence + 1
        event = _event("PromptSubmitted", sequence)
        return {
            "context_prompt": None,
            "end_sequence": sequence,
            "event_row_limit": 2,
            "events": [event],
            "materialization_end_sequence": sequence,
            "reason": "prompt_count",
            "selected_prompts": [event],
            "start_sequence": sequence,
            "window_truncated": False,
        }

    def build_context(_db, _session, **kwargs):
        return {
            "last_event_id": None,
            "slice": kwargs["slice_metadata"],
        }

    def generate_pending(_db, *, context, **_kwargs):
        artifact = SimpleNamespace(
            id=uuid4(),
            metadata_={**context["slice"]},
            updated_at=None,
        )
        artifacts.append(artifact)
        return artifact

    monkeypatch.setattr(memory_artifacts, "_memory_slice_runtime_state", load_state)
    monkeypatch.setattr(
        memory_artifacts,
        "_lock_memory_materialization_session",
        lambda *_args: None,
    )
    monkeypatch.setattr(memory_artifacts, "_due_memory_window", due_window)
    monkeypatch.setattr(memory_artifacts, "_build_session_memory_context", build_context)
    monkeypatch.setattr(memory_artifacts, "_generate_pending_draft_for_context", generate_pending)
    monkeypatch.setattr(
        memory_artifacts,
        "settings",
        SimpleNamespace(memory_slice_max_slices_per_call=2),
    )

    first_call = memory_artifacts.generate_due_memory_artifacts_for_session(
        object(),
        session,
        finalize=True,
    )
    assert [artifact.metadata_["end_sequence"] for artifact in first_call] == [1, 2]
    assert first_call[-1].metadata_["memory_resume_required"] is True

    worker_db = QueueDB(session, first_call[-1])
    assert memory_artifacts.materialize_next_idle_memory_session(worker_db) is True
    assert [artifact.metadata_["end_sequence"] for artifact in artifacts] == [1, 2, 3]
    assert "memory_resume_required" not in first_call[-1].metadata_
    assert artifacts[-1].metadata_.get("memory_resume_required") is not True
    assert "bool_or" in str(worker_db.statements[0])

    assert memory_artifacts.materialize_next_idle_memory_session(QueueDB(None, None)) is False


def test_generate_due_windows_loads_slice_state_once_and_carries_local_cursor(
    monkeypatch,
) -> None:
    session = _session()
    first_events = [_event("PromptSubmitted", 6)]
    second_events = [_event("FilesChanged", 11)]
    state_calls: list[object] = []
    due_after_sequences: list[int | None] = []
    built_contexts: list[dict] = []
    generated: list[SimpleNamespace] = []

    def load_state(_db, target_session):
        state_calls.append(target_session)
        return 5, 7, None, False

    windows_by_cursor = {
        5: {
            "context_prompt": None,
            "end_sequence": 10,
            "event_row_limit": 500,
            "events": first_events,
            "materialization_end_sequence": 10,
            "reason": "prompt_count",
            "selected_prompts": first_events,
            "start_sequence": 6,
            "window_truncated": False,
        },
        10: {
            "context_prompt": first_events[0],
            "end_sequence": 20,
            "event_row_limit": 500,
            "events": second_events,
            "materialization_end_sequence": 20,
            "reason": "event_count_continuation",
            "selected_prompts": [],
            "start_sequence": 11,
            "window_truncated": False,
        },
        20: None,
    }

    def due_window(
        _db,
        _session,
        *,
        after_sequence,
        continuation_end_sequence,
        finalize,
    ):
        assert finalize is False
        assert continuation_end_sequence is None
        due_after_sequences.append(after_sequence)
        return windows_by_cursor[after_sequence]

    def build_context(_db, _session, **kwargs):
        built_contexts.append(kwargs)
        return {"last_event_id": None}

    def generate_pending(_db, **kwargs):
        artifact = SimpleNamespace(id=uuid4(), metadata_={}, updated_at=None)
        generated.append(artifact)
        assert kwargs["context"] == {"last_event_id": None}
        return artifact

    monkeypatch.setattr(memory_artifacts, "_memory_slice_runtime_state", load_state)
    monkeypatch.setattr(
        memory_artifacts,
        "_lock_memory_materialization_session",
        lambda *_args: None,
    )
    monkeypatch.setattr(memory_artifacts, "_due_memory_window", due_window)
    monkeypatch.setattr(memory_artifacts, "_memory_slice_prompt_target", lambda: 1)
    monkeypatch.setattr(memory_artifacts, "_build_session_memory_context", build_context)
    monkeypatch.setattr(memory_artifacts, "_generate_pending_draft_for_context", generate_pending)

    result = memory_artifacts.generate_due_memory_artifacts_for_session(
        object(),
        session,
    )

    assert result == generated
    assert state_calls == [session]
    assert due_after_sequences == [5, 10, 20]
    assert [context["event_rows"] for context in built_contexts] == [
        first_events,
        second_events,
    ]
    assert [context["slice_metadata"]["slice_index"] for context in built_contexts] == [
        7,
        8,
    ]
    assert [context["slice_metadata"]["prompt_count"] for context in built_contexts] == [
        1,
        0,
    ]
    assert built_contexts[1]["slice_metadata"]["start_prompt_sequence"] is None
    assert built_contexts[1]["slice_metadata"]["end_prompt_sequence"] is None


def test_preloaded_event_rows_build_the_same_session_context_without_query(
    monkeypatch,
) -> None:
    project_id = uuid4()
    session = SimpleNamespace(
        ended_at=None,
        id=uuid4(),
        model="gpt-5",
        project=SimpleNamespace(name="Promty"),
        project_id=project_id,
        started_at=datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
        tool="codex-cli",
    )
    event_rows = [
        _event("PromptSubmitted", 1, {"prompt": "Optimize memory", "turn_id": "one"}),
        _event(
            "ResponseReceived",
            2,
            {"response": "Implemented the bounded query", "turn_id": "one"},
        ),
        _event("FilesChanged", 3, {"files": ["backend/app/services/memory/windows.py"]}),
    ]
    monkeypatch.setattr(memory_context, "payload", lambda event: event.payload)
    queried_db = QueueDB(event_rows)

    queried = memory_context.build_session_memory_context(
        queried_db,
        session,
        end_sequence=3,
        start_sequence=1,
    )
    preloaded_db = QueueDB()
    preloaded = memory_context.build_session_memory_context(
        preloaded_db,
        session,
        end_sequence=3,
        event_rows=event_rows,
        start_sequence=1,
    )

    assert preloaded == queried
    assert len(queried_db.statements) == 1
    assert preloaded_db.statements == []


def test_context_only_anchor_does_not_duplicate_coverage_or_source_ids(monkeypatch) -> None:
    project_id = uuid4()
    session = SimpleNamespace(
        ended_at=None,
        id=uuid4(),
        model="gpt-5",
        project=SimpleNamespace(name="Promty"),
        project_id=project_id,
        started_at=datetime(2026, 7, 13, 12, 0, tzinfo=UTC),
        tool="codex-cli",
    )
    anchor = _event("PromptSubmitted", 1, {"prompt": "Anchor direction"})
    covered = _event("FilesChanged", 5, {"files": ["backend/app.py"]})
    monkeypatch.setattr(memory_context, "payload", lambda event: event.payload)

    context = memory_context.build_session_memory_context(
        QueueDB(),
        session,
        context_event_rows=[anchor],
        end_sequence=5,
        event_rows=[covered],
        slice_metadata={
            "window_reason": "event_count_continuation",
            "window_truncated": True,
        },
        start_sequence=2,
    )
    evidence = memory_context.pending_draft_evidence_from_context(context)
    payload = memory_context.build_pending_memory_draft_payload(context, evidence=evidence)

    assert context["event_count"] == 1
    assert [event["sequence"] for event in context["events"]] == [5]
    assert context["first_event_id"] == str(covered.id)
    assert context["last_event_id"] == str(covered.id)
    assert context["prompt_events"][0]["context_only"] is True
    assert evidence["prompts"][0]["context_only"] is True
    assert payload["prompt_event_ids"] == []
    assert "context-only" in payload["reason"]


def test_pending_draft_generation_builds_evidence_once(monkeypatch) -> None:
    evidence = {"changed_files": [], "events": [], "prompts": [], "responses": []}
    evidence_calls: list[dict] = []
    payload_evidence: list[dict] = []
    artifact = SimpleNamespace(id=uuid4())
    db = QueueDB(None)
    session = _session()
    context = {"commits": [], "last_event_id": None, "slice": None}

    def build_evidence(value):
        evidence_calls.append(value)
        return evidence

    def build_payload(value, *, evidence):
        assert value is context
        payload_evidence.append(evidence)
        return {
            "event_count": 0,
            "first_event_id": None,
            "last_event_id": None,
        }

    monkeypatch.setattr(memory_artifacts, "_pending_draft_evidence_from_context", build_evidence)
    monkeypatch.setattr(memory_artifacts, "_build_pending_memory_draft_payload", build_payload)
    monkeypatch.setattr(
        memory_artifacts, "_write_memory_artifact_payload", lambda *_args, **_kwargs: artifact
    )

    result = memory_artifacts._generate_pending_draft_for_context(
        db,
        context=context,
        session=session,
        storage_key="memory/session/test/pending/1-3",
    )

    assert result is artifact
    assert evidence_calls == [context]
    assert payload_evidence == [evidence]


def test_idle_session_materialization_claims_and_finalizes_one_session(
    monkeypatch,
) -> None:
    session = _session()
    db = QueueDB(None, session)
    completed_sessions: list[object] = []
    generated_sessions: list[object] = []

    def complete(_db, target_session, *, force):
        assert force is False
        completed_sessions.append(target_session)
        return {"completed": True}

    monkeypatch.setattr(memory_artifacts, "complete_session_if_ready", complete)
    monkeypatch.setattr(
        memory_artifacts,
        "generate_due_memory_artifacts_for_session",
        lambda _db, target_session, *, finalize: generated_sessions.append(
            (target_session, finalize)
        ),
    )

    assert memory_artifacts.materialize_next_idle_memory_session(db) is True
    assert completed_sessions == [session]
    assert generated_sessions == [(session, True)]
    assert db.statements[1]._limit_clause.value == 1
    assert db.statements[1]._for_update_arg.skip_locked is True
    assert "last_activity_at" in str(db.statements[1])
    assert "FROM events" not in str(db.statements[1])


def test_worker_resumes_unfinished_window_before_idle_scan(monkeypatch) -> None:
    session = _session()
    session.ended_at = datetime(2026, 7, 13, 13, 0, tzinfo=UTC)
    db = QueueDB(session, None)
    generated_sessions: list[tuple[object, bool]] = []
    monkeypatch.setattr(
        memory_artifacts,
        "complete_session_if_ready",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unfinished materialization must not repeat completion")
        ),
    )
    monkeypatch.setattr(
        memory_artifacts,
        "generate_due_memory_artifacts_for_session",
        lambda _db, target_session, *, finalize: generated_sessions.append(
            (target_session, finalize)
        ),
    )

    assert memory_artifacts.materialize_next_idle_memory_session(db) is True
    assert generated_sessions == [(session, True)]
    assert len(db.statements) == 2
    assert db.statements[0]._limit_clause.value == 1
    assert db.statements[0]._for_update_arg.skip_locked is True
    assert db.statements[1]._limit_clause.value == 1
    assert db.statements[1]._for_update_arg is not None


def test_idle_session_materialization_is_a_noop_when_none_are_due(monkeypatch) -> None:
    db = QueueDB(None, None)
    monkeypatch.setattr(
        memory_artifacts,
        "complete_session_if_ready",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("completion must not run")),
    )

    assert memory_artifacts.materialize_next_idle_memory_session(db) is False


def test_pending_range_read_does_not_materialize_sessions(monkeypatch) -> None:
    db = QueueDB([])
    monkeypatch.setattr(
        memory_artifacts,
        "materialize_project_memory_drafts",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("GET-compatible reads must stay read-only")
        ),
    )

    assert (
        memory_artifacts.list_project_memory_pending_ranges(
            db,
            project_id=uuid4(),
        )
        == []
    )
