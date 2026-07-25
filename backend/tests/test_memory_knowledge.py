from __future__ import annotations

from app.services.memory.knowledge import (
    build_memory_knowledge_projection,
    memory_knowledge_projection_from_metadata,
    merge_memory_knowledge_projections,
    review_memory_knowledge_projection,
)


def _thinking_draft() -> dict:
    return {
        "confidence": 0.82,
        "details": {
            "decisions": [
                {
                    "confidence": 0.94,
                    "decision": "Use a right-side drawer for prompt review.",
                    "reason": "It preserves context while keeping the review flow focused.",
                    "source_chunk_ids": ["draft-1"],
                    "source_event_ids": ["event-1"],
                }
            ],
            "open_questions": [
                {
                    "question": "Should related sessions load lazily?",
                    "source_chunk_ids": ["draft-1"],
                    "source_event_ids": ["event-2"],
                }
            ],
            "rejected_directions": [
                {
                    "confidence": 0.88,
                    "content": "Use a centered tabbed modal.",
                    "reason": "The content was too dense.",
                    "source_chunk_ids": ["draft-1"],
                    "source_event_ids": ["event-1"],
                }
            ],
            "requirements": [
                {
                    "confidence": 0.91,
                    "reason": "Review must not hide the prompt being sent.",
                    "requirement": "Keep pending prompts visible in the review drawer.",
                    "source_chunk_ids": ["draft-1"],
                    "source_event_ids": ["event-2"],
                }
            ],
        },
        "evidence": {
            "source_chunk_ids": ["draft-1"],
            "source_event_ids": ["event-1", "event-2"],
        },
        "summary": "The review experience was simplified around a drawer.",
        "title": "Prompt review UX exploration",
        "type": "thinking_note",
    }


def test_memory_knowledge_projection_reuses_structured_generation_output() -> None:
    first = build_memory_knowledge_projection(_thinking_draft())
    second = build_memory_knowledge_projection(_thinking_draft())

    assert first == second
    assert first["schema_version"] == 1
    assert {node["kind"] for node in first["nodes"]} == {
        "brainstorm",
        "decision",
        "open_question",
        "requirement",
    }
    assert {node["status"] for node in first["nodes"]} == {
        "active",
        "discarded",
        "open",
    }
    assert all(node["evidence_type"] == "inferred" for node in first["nodes"])
    decision = next(node for node in first["nodes"] if node["kind"] == "decision")
    assert decision["confidence"] == 0.94
    assert decision["source_event_ids"] == ["event-1"]
    requirement = next(node for node in first["nodes"] if node["kind"] == "requirement")
    assert requirement["status"] == "active"
    assert requirement["source_event_ids"] == ["event-2"]


def test_memory_knowledge_projection_merge_deduplicates_stable_candidates() -> None:
    projection = build_memory_knowledge_projection(_thinking_draft())
    merged = merge_memory_knowledge_projections([projection, projection])

    assert merged["schema_version"] == 1
    assert merged["nodes"] == projection["nodes"]


def test_memory_knowledge_projection_backfills_older_structured_metadata() -> None:
    draft = _thinking_draft()
    projection = memory_knowledge_projection_from_metadata(
        {
            "draft_confidence": draft["confidence"],
            "draft_details": draft["details"],
            "draft_evidence": draft["evidence"],
            "draft_type": draft["type"],
        },
        summary=draft["summary"],
        title=draft["title"],
    )

    assert projection["nodes"]
    assert {node["kind"] for node in projection["nodes"]} == {
        "brainstorm",
        "decision",
        "open_question",
        "requirement",
    }


def test_memory_knowledge_projection_records_and_resets_user_review() -> None:
    projection = build_memory_knowledge_projection(_thinking_draft())
    decision = next(node for node in projection["nodes"] if node["kind"] == "decision")
    metadata = {"knowledge_projection": projection, "review_state": "generated"}

    confirmed_metadata, confirmed = review_memory_knowledge_projection(
        metadata,
        candidate_id=decision["id"],
        review_state="confirmed",
        reviewed_at="2026-07-24T01:00:00+00:00",
        reviewed_by="user-1",
    )

    assert confirmed["review_state"] == "confirmed"
    assert confirmed["reviewed_by"] == "user-1"
    assert metadata["knowledge_projection"]["nodes"] != confirmed_metadata[
        "knowledge_projection"
    ]["nodes"]

    reset_metadata, reset = review_memory_knowledge_projection(
        confirmed_metadata,
        candidate_id=decision["id"],
        review_state="unreviewed",
        reviewed_at="2026-07-24T02:00:00+00:00",
        reviewed_by="user-1",
    )

    assert "review_state" not in reset
    assert "reviewed_at" not in reset
    assert reset_metadata["review_state"] == "generated"
