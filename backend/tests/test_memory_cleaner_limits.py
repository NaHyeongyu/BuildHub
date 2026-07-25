from __future__ import annotations

from types import SimpleNamespace

from app.core.text_limits import PROJECT_MEMORY_BODY_MAX_BYTES
from app.schemas.memory import MemoryDraftGeneration
from app.services.memory import draft_payloads
from app.services.memory.cleaners import (
    MAX_SEMANTIC_LIST_ITEMS,
    MAX_SOURCE_ID_CHARS,
    MAX_SOURCE_IDS,
    clean_memory_drafts_response,
    clean_project_memory_response,
)


def _draft_with_large_semantic_lists() -> dict:
    ids = [f"event-{index}" for index in range(MAX_SOURCE_IDS + 20)]
    return {
        "confidence": 0.8,
        "details": {
            "decisions": [
                {
                    "decision": f"Decision {index}",
                    "reason": "Reason",
                    "source_event_ids": ids,
                }
                for index in range(MAX_SEMANTIC_LIST_ITEMS + 20)
            ],
            "follow_ups": [f"Follow-up {index}" for index in range(MAX_SEMANTIC_LIST_ITEMS + 20)],
            "open_questions": [
                {"question": f"Question {index}", "source_event_ids": ids}
                for index in range(MAX_SEMANTIC_LIST_ITEMS + 20)
            ],
            "requirements": [
                {
                    "confidence": 0.9,
                    "reason": "Acceptance condition",
                    "requirement": f"Requirement {index}",
                    "source_event_ids": ids,
                }
                for index in range(MAX_SEMANTIC_LIST_ITEMS + 20)
            ],
            "tasks": [f"Task {index}" for index in range(MAX_SEMANTIC_LIST_ITEMS + 20)],
        },
        "evidence": {
            "based_on": ["pending_draft"],
            "source_chunk_ids": [f"chunk-{index}" for index in range(MAX_SOURCE_IDS + 20)],
            "source_event_ids": [*ids, "x" * (MAX_SOURCE_ID_CHARS + 1)],
        },
        "needs_user_verification": False,
        "suggested_user_action": "save",
        "summary": "Summary",
        "title": "Title",
        "type": "work_log",
        "why_it_matters": "Reason",
    }


def test_draft_cleaner_hard_caps_semantic_lists_and_source_ids() -> None:
    cleaned = clean_memory_drafts_response(
        {"memory_drafts": [_draft_with_large_semantic_lists()]},
        {"pending_drafts": [], "prompt_events": []},
    )
    draft = cleaned["memory_drafts"][0]

    assert len(draft["details"]["tasks"]) == MAX_SEMANTIC_LIST_ITEMS
    assert len(draft["details"]["decisions"]) == MAX_SEMANTIC_LIST_ITEMS
    assert len(draft["details"]["open_questions"]) == MAX_SEMANTIC_LIST_ITEMS
    assert len(draft["details"]["requirements"]) == MAX_SEMANTIC_LIST_ITEMS
    assert len(draft["evidence"]["source_event_ids"]) == MAX_SOURCE_IDS
    assert len(draft["evidence"]["source_chunk_ids"]) == MAX_SOURCE_IDS
    assert all(len(value) <= MAX_SOURCE_ID_CHARS for value in draft["evidence"]["source_event_ids"])


def test_draft_cleaner_preserves_complete_display_content() -> None:
    raw_draft = _draft_with_large_semantic_lists()
    raw_draft["title"] = "A complete generated memory title " + "title " * 80
    raw_draft["summary"] = "Complete summary. " + "summary detail " * 200
    raw_draft["outcome"] = "Implemented the final direction. " + "detail " * 500
    raw_draft["why_it_matters"] = "This matters because " + "reason detail " * 200
    raw_draft["details"]["tasks"][0] = "Task 0: " + "task detail " * 200

    cleaned = clean_memory_drafts_response(
        {"memory_drafts": [raw_draft]},
        {"pending_drafts": [], "prompt_events": []},
    )
    draft = cleaned["memory_drafts"][0]

    assert draft["title"] == raw_draft["title"].strip()
    assert draft["summary"] == raw_draft["summary"].strip()
    assert draft["outcome"] == raw_draft["outcome"].strip()
    assert draft["why_it_matters"] == raw_draft["why_it_matters"].strip()
    assert draft["details"]["tasks"][0] == raw_draft["details"]["tasks"][0].strip()
    assert not draft["summary"].endswith("...")
    assert not draft["outcome"].endswith("...")

    validated = MemoryDraftGeneration.model_validate(cleaned).model_dump()
    assert validated["memory_drafts"][0]["outcome"] == raw_draft["outcome"].strip()


def test_project_cleaner_caps_utf8_body_and_ids_without_truncating_sections() -> None:
    items = [f"Item {index}" for index in range(MAX_SEMANTIC_LIST_ITEMS + 20)]
    ids = [f"memory-{index}" for index in range(MAX_SOURCE_IDS + 20)]
    current_direction = "d" * 4_100
    product_goal = "g" * 4_100
    cleaned = clean_project_memory_response(
        {
            "body_markdown": "한" * PROJECT_MEMORY_BODY_MAX_BYTES,
            "sections": {
                "core_workflow": items,
                "current_direction": current_direction,
                "important_decisions": [
                    {
                        "decision": f"Decision {index}",
                        "reason": "Reason",
                        "source_memory_ids": ids,
                    }
                    for index in range(MAX_SEMANTIC_LIST_ITEMS + 20)
                ],
                "instructions_for_future_ai_agents": items,
                "open_questions": items,
                "product_goal": product_goal,
                "requirements": items,
                "rejected_directions": [],
                "superseded_decisions": [
                    {
                        "decision": f"Old decision {index}",
                        "superseded_by": f"New decision {index}",
                        "reason": "Newer approved direction",
                        "source_memory_ids": ids,
                    }
                    for index in range(MAX_SEMANTIC_LIST_ITEMS + 20)
                ],
                "technical_assumptions": items,
            },
            "source_memory_ids": ids,
            "warnings": items,
        },
        {"source_memories": []},
    )

    assert len(cleaned["body_markdown"].encode("utf-8")) <= PROJECT_MEMORY_BODY_MAX_BYTES
    assert len(cleaned["sections"]["core_workflow"]) == MAX_SEMANTIC_LIST_ITEMS
    assert len(cleaned["sections"]["important_decisions"]) == MAX_SEMANTIC_LIST_ITEMS
    assert len(cleaned["sections"]["requirements"]) == MAX_SEMANTIC_LIST_ITEMS
    assert (
        len(cleaned["sections"]["superseded_decisions"])
        == MAX_SEMANTIC_LIST_ITEMS
    )
    assert cleaned["sections"]["current_direction"] == current_direction
    assert cleaned["sections"]["product_goal"] == product_goal
    assert cleaned["sections"]["instructions_for_future_ai_agents"] == []
    assert len(cleaned["source_memory_ids"]) == MAX_SOURCE_IDS
    assert len(cleaned["warnings"]) == MAX_SEMANTIC_LIST_ITEMS
    assert "removed pending user review" in cleaned["warnings"][-1]


def test_draft_payload_metadata_stores_generation_summary_not_full_response(
    monkeypatch,
) -> None:
    response = {
        "draft_generation_reason": "Generated from bounded evidence.",
        "memory_drafts": [_draft_with_large_semantic_lists()],
        "overall_uncertainties": [{"content": "uncertain"}] * 7,
        "source_chunk_ids": ["chunk-1"],
        "source_event_ids": ["event-1", "event-2"],
        "summary_level": 2,
    }
    response["memory_drafts"][0]["outcome"] = "Implemented the approved final result."
    response["memory_drafts"][0]["details"]["what_happened"] = [
        "PromptSubmitted event with raw conversation history."
    ]
    monkeypatch.setattr(
        draft_payloads,
        "settings",
        SimpleNamespace(memory_draft_generator="openai"),
    )
    monkeypatch.setattr(
        draft_payloads,
        "compile_memory_drafts",
        lambda *_args, **_kwargs: response,
    )
    monkeypatch.setattr(
        draft_payloads,
        "model_metadata_for_provider",
        lambda _provider: {"openai_model": "gpt-test"},
    )
    context = {
        "changed_files": [],
        "commits": [],
        "event_count": 2,
        "first_event_id": "event-1",
        "last_event_id": "event-2",
        "model": "gpt-test",
        "pending_drafts": [{"id": "chunk-1"}],
        "prompt_events": [{"id": "event-1"}],
        "tool": "codex",
    }

    payloads, generation_metadata = draft_payloads.build_memory_draft_payloads_from_context(
        context,
        trigger_reason="project_batch",
    )

    summary = generation_metadata["draft_generation"]
    assert "memory_drafts" not in summary
    assert "overall_uncertainties" not in summary
    assert summary["draft_count"] == 1
    assert summary["overall_uncertainty_count"] == 7
    assert len(payloads) == 1
    assert payloads[0][0]["outcome"] == "Implemented the approved final result."
    draft_metadata = payloads[0][1]
    assert "overall_uncertainties" not in draft_metadata
    assert draft_metadata["overall_uncertainty_count"] == 7
    assert draft_metadata["knowledge_projection"]["schema_version"] == 1
    assert {node["kind"] for node in draft_metadata["knowledge_projection"]["nodes"]} == {
        "decision",
        "open_question",
        "requirement",
    }


def test_draft_payload_keeps_complete_generated_detail() -> None:
    task_details = [f"Task {index}: " + (f"detail-{index} " * 40) for index in range(6)]
    outcome = "Implemented the complete result. " + ("outcome detail " * 80)
    summary = "Complete generated summary. " + ("summary detail " * 80)
    draft = _draft_with_large_semantic_lists()
    draft["details"]["tasks"] = task_details
    draft["outcome"] = outcome
    draft["summary"] = summary
    context = {
        "changed_files": [],
        "commits": [],
        "event_count": 1,
        "first_event_id": "event-1",
        "last_event_id": "event-1",
        "model": "gpt-test",
        "tool": "codex",
    }

    payload = draft_payloads._payload_from_memory_draft(
        context,
        draft,
        generator="openai:test",
    )

    tasks_section = next(section for section in payload["sections"] if section["title"] == "Tasks")
    assert payload["outcome"] == outcome.strip()
    assert payload["summary"] == summary
    assert tasks_section["summary"] == " / ".join(detail.strip() for detail in task_details)
    assert not tasks_section["summary"].endswith("...")
