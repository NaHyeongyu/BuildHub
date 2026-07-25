from __future__ import annotations

import json
from typing import Any

from app.core.text_limits import (
    PROJECT_MEMORY_BODY_MAX_BYTES,
    truncate_utf8_bytes,
)
from app.services.memory.errors import MemoryGenerationError
from app.services.memory.prompts import MAX_MEMORY_DRAFTS
from app.services.memory.text import clean_text, truncate


MAX_SEMANTIC_LIST_ITEMS = 32
MAX_SOURCE_IDS = 128
MAX_SOURCE_ID_CHARS = 200


def parse_json_text(
    text: str,
    *,
    error_cls: type[Exception] = MemoryGenerationError,
    provider: str = "Model",
) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise error_cls(f"{provider} response was not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise error_cls(f"{provider} response JSON must be an object.")
    return parsed


def _source_event_ids_from_context(context: dict[str, Any]) -> list[str]:
    ids = [
        prompt.get("id")
        for prompt in context.get("prompt_events", [])
        if isinstance(prompt, dict) and isinstance(prompt.get("id"), str)
    ]
    if not ids and isinstance(context.get("first_event_id"), str):
        ids.append(context["first_event_id"])
    if isinstance(context.get("last_event_id"), str) and context["last_event_id"] not in ids:
        ids.append(context["last_event_id"])
    return ids


def _clean_confidence(value: Any, fallback: float = 0.5) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(float(value), 1.0))
    return fallback


def _clean_source_ids(value: Any, fallback_ids: list[str]) -> list[str]:
    def clean(values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        return list(
            dict.fromkeys(
                item.strip()
                for item in values
                if isinstance(item, str)
                and item.strip()
                and len(item.strip()) <= MAX_SOURCE_ID_CHARS
            )
        )[:MAX_SOURCE_IDS]

    return clean(value) or clean(fallback_ids)


def _source_chunk_ids_from_context(context: dict[str, Any]) -> list[str]:
    pending_drafts = (
        context.get("pending_drafts") if isinstance(context.get("pending_drafts"), list) else []
    )
    return [
        draft.get("id")
        for draft in pending_drafts
        if isinstance(draft, dict) and isinstance(draft.get("id"), str) and draft.get("id")
    ]


def _clean_string_list(
    value: Any,
    *,
    limit: int | None = 12,
) -> list[str]:
    if not isinstance(value, list):
        return []
    effective_limit = (
        MAX_SEMANTIC_LIST_ITEMS if limit is None else min(max(limit, 0), MAX_SEMANTIC_LIST_ITEMS)
    )
    items = value[:effective_limit]
    return [
        cleaned
        for item in items
        if isinstance(item, str) and (cleaned := clean_text(item))
    ]


def _clean_draft_type(value: Any) -> str:
    allowed = {"decision_note", "issue_note", "process_note", "thinking_note", "work_log"}
    return value if isinstance(value, str) and value in allowed else "thinking_note"


def _clean_suggested_action(value: Any, *, confidence: float, needs_verification: bool) -> str:
    allowed = {"edit", "ignore", "save"}
    if isinstance(value, str) and value in allowed:
        return value
    if confidence < 0.35:
        return "ignore"
    return "edit" if needs_verification else "save"


def _clean_based_on(value: Any) -> list[str]:
    allowed = {
        "changed_files",
        "commit_metadata",
        "paired_ai_output",
        "pending_draft",
        "remaining_event_preview",
        "user_direction",
    }
    if not isinstance(value, list):
        return ["pending_draft"]
    cleaned = list(
        dict.fromkeys(item for item in value if isinstance(item, str) and item in allowed)
    )
    return cleaned or ["pending_draft"]


def _clean_draft_nested_items(
    value: Any,
    *,
    fallback_chunk_ids: list[str],
    fallback_event_ids: list[str],
    required_text_key: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for item in value[:MAX_SEMANTIC_LIST_ITEMS]:
        if not isinstance(item, dict):
            continue
        text = clean_text(item.get(required_text_key))
        if not text:
            continue
        cleaned_item: dict[str, Any] = {
            required_text_key: text,
            "source_chunk_ids": _clean_source_ids(
                item.get("source_chunk_ids"),
                fallback_chunk_ids,
            ),
            "source_event_ids": _clean_source_ids(
                item.get("source_event_ids"),
                fallback_event_ids,
            ),
        }
        if "confidence" in item:
            cleaned_item["confidence"] = _clean_confidence(item.get("confidence"))
        if "reason" in item:
            cleaned_item["reason"] = clean_text(item.get("reason")) or None
        cleaned.append(cleaned_item)
    return cleaned


def clean_memory_drafts_response(value: Any, context: dict[str, Any]) -> dict[str, Any]:
    fallback_event_ids = _source_event_ids_from_context(context)
    fallback_chunk_ids = _source_chunk_ids_from_context(context)
    parsed = value if isinstance(value, dict) else {}
    raw_drafts = (
        parsed.get("memory_drafts") if isinstance(parsed.get("memory_drafts"), list) else []
    )
    memory_drafts: list[dict[str, Any]] = []
    for raw in raw_drafts[:MAX_MEMORY_DRAFTS]:
        if not isinstance(raw, dict):
            continue
        title = clean_text(raw.get("title"))
        summary = clean_text(raw.get("summary"))
        outcome = clean_text(raw.get("outcome"))
        why_it_matters = clean_text(raw.get("why_it_matters"))
        if not title or not summary or not why_it_matters:
            continue
        details = raw.get("details") if isinstance(raw.get("details"), dict) else {}
        evidence = raw.get("evidence") if isinstance(raw.get("evidence"), dict) else {}
        confidence = _clean_confidence(raw.get("confidence"))
        needs_verification = raw.get("needs_user_verification") is True
        draft_event_ids = _clean_source_ids(
            evidence.get("source_event_ids") or raw.get("source_event_ids"),
            fallback_event_ids,
        )
        draft_chunk_ids = _clean_source_ids(
            evidence.get("source_chunk_ids") or raw.get("source_chunk_ids"),
            fallback_chunk_ids,
        )
        cleaned_details = {
            "decisions": _clean_draft_nested_items(
                details.get("decisions"),
                fallback_chunk_ids=draft_chunk_ids,
                fallback_event_ids=draft_event_ids,
                required_text_key="decision",
            ),
            "follow_ups": _clean_string_list(
                details.get("follow_ups") or details.get("next_steps"),
                limit=None,
            ),
            "next_steps": _clean_string_list(details.get("next_steps"), limit=None),
            "open_questions": _clean_draft_nested_items(
                details.get("open_questions"),
                fallback_chunk_ids=draft_chunk_ids,
                fallback_event_ids=draft_event_ids,
                required_text_key="question",
            ),
            "problem": clean_text(details.get("problem")) or None,
            "rejected_directions": _clean_draft_nested_items(
                details.get("rejected_directions"),
                fallback_chunk_ids=draft_chunk_ids,
                fallback_event_ids=draft_event_ids,
                required_text_key="content",
            ),
            "requirements": _clean_draft_nested_items(
                details.get("requirements"),
                fallback_chunk_ids=draft_chunk_ids,
                fallback_event_ids=draft_event_ids,
                required_text_key="requirement",
            ),
            "summary": clean_text(details.get("summary")) or None,
            "tasks": _clean_string_list(
                details.get("tasks") or details.get("what_happened"),
                limit=None,
            ),
            "what_happened": _clean_string_list(details.get("what_happened"), limit=None),
            "why_started": clean_text(details.get("why_started")) or None,
        }
        memory_drafts.append(
            {
                "confidence": confidence,
                "details": cleaned_details,
                "evidence": {
                    "based_on": _clean_based_on(evidence.get("based_on")),
                    "source_chunk_ids": draft_chunk_ids,
                    "source_event_ids": draft_event_ids,
                },
                "needs_user_verification": needs_verification,
                "outcome": outcome or summary,
                "suggested_user_action": _clean_suggested_action(
                    raw.get("suggested_user_action"),
                    confidence=confidence,
                    needs_verification=needs_verification,
                ),
                "summary": summary,
                "title": title,
                "type": _clean_draft_type(raw.get("type")),
                "why_it_matters": why_it_matters,
            }
        )

    return {
        "draft_generation_reason": truncate(
            parsed.get("draft_generation_reason"),
            500,
        )
        or "Memory draft generation ran from pending draft evidence packages.",
        "memory_drafts": memory_drafts,
        "overall_uncertainties": _clean_draft_nested_items(
            parsed.get("overall_uncertainties"),
            fallback_chunk_ids=fallback_chunk_ids,
            fallback_event_ids=fallback_event_ids,
            required_text_key="content",
        ),
        "source_chunk_ids": _clean_source_ids(parsed.get("source_chunk_ids"), fallback_chunk_ids),
        "source_event_ids": _clean_source_ids(parsed.get("source_event_ids"), fallback_event_ids),
        "summary_level": 2,
    }


def _source_memory_ids_from_context(context: dict[str, Any]) -> list[str]:
    memories = (
        context.get("source_memories")
        if isinstance(context.get("source_memories"), list)
        else context.get("verified_memories")
        if isinstance(context.get("verified_memories"), list)
        else []
    )
    return [
        memory.get("id")
        for memory in memories
        if isinstance(memory, dict) and isinstance(memory.get("id"), str) and memory.get("id")
    ]


def _clean_memory_id_items(
    value: Any,
    *,
    fallback_ids: list[str],
    key: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for item in value[:MAX_SEMANTIC_LIST_ITEMS]:
        if not isinstance(item, dict):
            continue
        text = clean_text(item.get(key))
        reason = clean_text(item.get("reason"))
        if not text:
            continue
        cleaned.append(
            {
                key: text,
                "reason": reason or "",
                "source_memory_ids": _clean_source_ids(
                    item.get("source_memory_ids"),
                    fallback_ids,
                ),
            }
        )
    return cleaned


def _clean_superseded_decisions(
    value: Any,
    *,
    fallback_ids: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    cleaned: list[dict[str, Any]] = []
    for item in value[:MAX_SEMANTIC_LIST_ITEMS]:
        if not isinstance(item, dict):
            continue
        decision = clean_text(item.get("decision"))
        superseded_by = clean_text(item.get("superseded_by"))
        if not decision or not superseded_by:
            continue
        cleaned.append(
            {
                "decision": decision,
                "reason": clean_text(item.get("reason")) or "",
                "source_memory_ids": _clean_source_ids(
                    item.get("source_memory_ids"),
                    fallback_ids,
                ),
                "superseded_by": superseded_by,
            }
        )
    return cleaned


def clean_project_memory_response(value: Any, context: dict[str, Any]) -> dict[str, Any]:
    fallback_ids = _source_memory_ids_from_context(context)
    parsed = value if isinstance(value, dict) else {}
    sections = parsed.get("sections") if isinstance(parsed.get("sections"), dict) else {}
    cleaned_sections = {
        "core_workflow": _clean_string_list(sections.get("core_workflow"), limit=None),
        "current_direction": clean_text(sections.get("current_direction")),
        "important_decisions": _clean_memory_id_items(
            sections.get("important_decisions"),
            fallback_ids=fallback_ids,
            key="decision",
        ),
        # Model-authored operational instructions must not cross the trust
        # boundary into a future tool-capable coding agent.
        "instructions_for_future_ai_agents": [],
        "open_questions": _clean_string_list(sections.get("open_questions"), limit=None),
        "product_goal": clean_text(sections.get("product_goal")),
        "requirements": _clean_string_list(sections.get("requirements"), limit=None),
        "rejected_directions": _clean_memory_id_items(
            sections.get("rejected_directions"),
            fallback_ids=fallback_ids,
            key="direction",
        ),
        "superseded_decisions": _clean_superseded_decisions(
            sections.get("superseded_decisions"),
            fallback_ids=fallback_ids,
        ),
        "technical_assumptions": _clean_string_list(
            sections.get("technical_assumptions"),
            limit=None,
        ),
    }
    body_markdown = clean_text(parsed.get("body_markdown"))
    if not body_markdown:
        body_markdown = "\n\n".join(
            part
            for part in (
                "# Project Memory",
                f"## Product Goal\n{cleaned_sections['product_goal']}",
                f"## Current Direction\n{cleaned_sections['current_direction']}",
            )
            if part.strip()
        )
    body_markdown = truncate_utf8_bytes(
        body_markdown,
        max_bytes=PROJECT_MEMORY_BODY_MAX_BYTES,
    )
    warnings = _clean_string_list(parsed.get("warnings"), limit=None)
    if _clean_string_list(sections.get("instructions_for_future_ai_agents"), limit=1):
        warnings = [
            *warnings[: MAX_SEMANTIC_LIST_ITEMS - 1],
            "Model-generated future-agent instructions were removed pending user review.",
        ]
    return {
        "body_markdown": body_markdown,
        "confidence": _clean_confidence(parsed.get("confidence"), fallback=0.45),
        "sections": cleaned_sections,
        "snapshot_type": "project_memory",
        "source_memory_ids": _clean_source_ids(parsed.get("source_memory_ids"), fallback_ids),
        "warnings": warnings,
    }
