from __future__ import annotations

from pathlib import Path

import change_tracking
from change_tracking import ChangeBaselineStore
from events import BaseEvent, PromptSubmittedPayload


def prompt_event(event_id: str, sequence: int) -> BaseEvent:
    return BaseEvent(
        id=event_id,
        tool="codex-cli",
        event_type="PromptSubmitted",
        payload=PromptSubmittedPayload(prompt=f"prompt {sequence}"),
        project_id="project-id",
        session_id="session-id",
        sequence=sequence,
    )


def test_continuation_inherits_root_snapshot_and_consumes_ancestors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    snapshot = {
        "git_root": str(tmp_path),
        "head_commit": "abc123",
        "captured_at": "2026-07-25T00:00:00+00:00",
    }
    monkeypatch.setattr(change_tracking, "capture_git_snapshot", lambda _cwd: snapshot)
    monkeypatch.setattr(change_tracking, "resolve_git_root", lambda _cwd: str(tmp_path))
    store = ChangeBaselineStore(tmp_path / "baselines.json")

    first = prompt_event("prompt-1", 1)
    store.observe_prompt(
        tool="codex-cli",
        event=first,
        raw_payload={"turn_id": "turn-1"},
        external_session_id="external-session",
        cwd=str(tmp_path),
    )
    first_baseline = store.find_latest(
        tool="codex-cli",
        external_session_id="external-session",
        cwd=str(tmp_path),
    )
    assert first_baseline is not None

    second = prompt_event("prompt-2", 2)
    store.observe_prompt(
        tool="codex-cli",
        event=second,
        raw_payload={"turn_id": "turn-2"},
        external_session_id="external-session",
        cwd=str(tmp_path),
        previous_baseline=first_baseline,
    )

    second_baseline = store.find_for_turn(
        tool="codex-cli",
        external_session_id="external-session",
        cwd=str(tmp_path),
        turn_id="turn-2",
    )
    assert second_baseline is not None
    assert second_baseline["continuation_of"] == "prompt-1"
    assert second_baseline["root_prompt_event_id"] == "prompt-1"
    assert second_baseline["snapshot"] == snapshot

    first_for_stop = store.find_for_turn(
        tool="codex-cli",
        external_session_id="external-session",
        cwd=str(tmp_path),
        turn_id="turn-1",
    )
    assert first_for_stop is not None
    store.mark_consumed_with_ancestors(str(first_for_stop["id"]))
    assert store.find_latest(
        tool="codex-cli",
        external_session_id="external-session",
        cwd=str(tmp_path),
    )["id"] == "prompt-2"

    store.mark_consumed_with_ancestors(str(second_baseline["id"]))
    assert store.find_latest(
        tool="codex-cli",
        external_session_id="external-session",
        cwd=str(tmp_path),
    ) is None


def test_pending_prompt_does_not_continue_across_external_sessions(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(change_tracking, "capture_git_snapshot", lambda _cwd: None)
    monkeypatch.setattr(change_tracking, "resolve_git_root", lambda _cwd: str(tmp_path))
    store = ChangeBaselineStore(tmp_path / "baselines.json")
    store.observe_prompt(
        tool="codex-cli",
        event=prompt_event("prompt-1", 1),
        raw_payload={"turn_id": "turn-1"},
        external_session_id="session-a",
        cwd=str(tmp_path),
    )

    assert store.find_latest(
        tool="codex-cli",
        external_session_id="session-b",
        cwd=str(tmp_path),
    ) is None
