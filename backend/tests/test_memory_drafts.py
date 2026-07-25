from types import SimpleNamespace

from app.services.memory.cleaners import clean_memory_drafts_response
from app.services.memory.prompts import MAX_MEMORY_DRAFTS
from app.services.memory.windows import (
    MEMORY_SLICE_KIND_IMPLEMENTATION,
    MEMORY_SLICE_KIND_REASONING,
    events_have_generation_inputs,
    generation_input_kind,
)


def _raw_draft(index: int) -> dict:
    return {
        "confidence": 0.8,
        "details": {
            "decisions": [],
            "follow_ups": [],
            "open_questions": [],
            "summary": f"Summary section {index}",
            "tasks": [f"Task {index}"],
        },
        "evidence": {
            "based_on": ["pending_draft"],
            "source_chunk_ids": ["pending-draft-1"],
            "source_event_ids": [f"event-{index}"],
        },
        "needs_user_verification": False,
        "suggested_user_action": "save",
        "summary": f"Draft summary {index}",
        "title": f"Draft {index}",
        "type": "work_log",
        "why_it_matters": f"Reason {index}",
    }


def test_memory_draft_cleaner_caps_drafts_for_review_ux() -> None:
    context = {
        "events": [{"event_type": "PromptSubmitted", "id": "event-fallback"}],
        "pending_drafts": [{"id": "pending-draft-1"}],
        "prompt_events": [{"id": "event-fallback"}],
    }
    generated = {
        "draft_generation_reason": "Model split the batch too much.",
        "memory_drafts": [_raw_draft(index) for index in range(MAX_MEMORY_DRAFTS + 2)],
        "overall_uncertainties": [],
        "source_chunk_ids": ["pending-draft-1"],
        "source_event_ids": ["event-fallback"],
    }

    cleaned = clean_memory_drafts_response(generated, context)

    assert len(cleaned["memory_drafts"]) == MAX_MEMORY_DRAFTS
    assert [draft["title"] for draft in cleaned["memory_drafts"]] == [
        f"Draft {index}" for index in range(MAX_MEMORY_DRAFTS)
    ]


def test_memory_draft_cleaner_preserves_detail_item_counts() -> None:
    item_count = 24
    context = {
        "events": [{"event_type": "PromptSubmitted", "id": "event-fallback"}],
        "pending_drafts": [{"id": "pending-draft-1"}],
        "prompt_events": [{"id": "event-fallback"}],
    }
    draft = _raw_draft(1)
    draft["details"] = {
        "decisions": [
            {
                "confidence": 0.7,
                "decision": f"Decision {index}",
                "reason": f"Reason {index}",
                "source_chunk_ids": ["pending-draft-1"],
                "source_event_ids": [f"event-{index}"],
            }
            for index in range(item_count)
        ],
        "follow_ups": [f"Follow-up {index}" for index in range(item_count)],
        "open_questions": [
            {
                "question": f"Question {index}",
                "source_chunk_ids": ["pending-draft-1"],
                "source_event_ids": [f"event-{index}"],
            }
            for index in range(item_count)
        ],
        "summary": "Detailed summary",
        "tasks": [f"Task {index}" for index in range(item_count)],
    }
    generated = {
        "draft_generation_reason": "Large batch should keep proportionally rich detail.",
        "memory_drafts": [draft],
        "overall_uncertainties": [],
        "source_chunk_ids": ["pending-draft-1"],
        "source_event_ids": ["event-fallback"],
    }

    cleaned = clean_memory_drafts_response(generated, context)
    details = cleaned["memory_drafts"][0]["details"]

    assert len(details["tasks"]) == item_count
    assert len(details["decisions"]) == item_count
    assert len(details["follow_ups"]) == item_count
    assert len(details["open_questions"]) == item_count


def test_memory_generation_inputs_classify_implementation_and_reasoning_after_latest_prompt() -> None:
    def event(
        event_type: str,
        sequence: int,
        payload: dict | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(event_type=event_type, payload=payload or {}, sequence=sequence)

    assert not events_have_generation_inputs([event("PromptSubmitted", 1)])
    reasoning_events = [
        event("PromptSubmitted", 1),
        event("ResponseReceived", 2, {"response": "done"}),
    ]
    assert events_have_generation_inputs(reasoning_events)
    assert generation_input_kind(reasoning_events) == MEMORY_SLICE_KIND_REASONING
    assert not events_have_generation_inputs(
        [
            event("PromptSubmitted", 1),
            event("ResponseReceived", 2),
            event("FilesChanged", 3),
        ]
    )
    implementation_events = [
        event("PromptSubmitted", 1),
        event("ResponseReceived", 2, {"response": "done"}),
        event("FilesChanged", 3),
    ]
    assert events_have_generation_inputs(implementation_events)
    assert generation_input_kind(implementation_events) == MEMORY_SLICE_KIND_IMPLEMENTATION
    assert not events_have_generation_inputs(
        [
            event("PromptSubmitted", 1),
            event("ResponseReceived", 2, {"response": "done"}),
            event("FilesChanged", 3),
            event("PromptSubmitted", 4),
        ]
    )
