from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.services.memory.context import (
    string_or_none,
    tags_for_session,
    technologies_for_session,
    truncate,
)
from app.services.memory.errors import MemoryGenerationError
from app.services.memory.providers import (
    generator_for_provider,
    model_metadata_for_provider,
    provider_name,
)
from app.services.memory_pipeline import compile_memory_drafts


def source_event_ids_for_context(context: dict[str, Any]) -> list[str]:
    ids = [
        prompt["id"]
        for prompt in context["prompt_events"]
        if prompt.get("context_only") is not True
        and isinstance(prompt.get("id"), str)
        and prompt.get("id")
    ]
    if not ids and context.get("first_event_id"):
        ids.append(context["first_event_id"])
    if context.get("last_event_id") and context["last_event_id"] not in ids:
        ids.append(context["last_event_id"])
    return ids


def source_draft_ids_for_context(context: dict[str, Any]) -> list[str]:
    drafts = (
        context.get("pending_drafts") if isinstance(context.get("pending_drafts"), list) else []
    )
    return [
        draft.get("id")
        for draft in drafts
        if isinstance(draft, dict) and isinstance(draft.get("id"), str) and draft.get("id")
    ]


def source_chunk_ids_for_context(context: dict[str, Any]) -> list[str]:
    return source_draft_ids_for_context(context)


def _section_from_strings(title: str, values: list[str]) -> dict[str, str] | None:
    summaries = [value.strip() for value in values if value.strip()]
    if not summaries:
        return None
    return {"summary": " / ".join(summaries), "title": title}


def _sections_from_memory_draft(draft: dict[str, Any]) -> list[dict[str, str]]:
    details = draft.get("details") if isinstance(draft.get("details"), dict) else {}
    tasks = details.get("tasks") if isinstance(details.get("tasks"), list) else []
    if not tasks:
        tasks = (
            details.get("what_happened") if isinstance(details.get("what_happened"), list) else []
        )
    follow_ups = details.get("follow_ups") if isinstance(details.get("follow_ups"), list) else []
    if not follow_ups:
        follow_ups = (
            details.get("next_steps") if isinstance(details.get("next_steps"), list) else []
        )
    open_questions = (
        [
            item.get("question")
            for item in details.get("open_questions", [])
            if isinstance(item, dict) and isinstance(item.get("question"), str)
        ]
        if isinstance(details.get("open_questions"), list)
        else []
    )
    rejected_directions = (
        [
            item.get("content")
            for item in details.get("rejected_directions", [])
            if isinstance(item, dict) and isinstance(item.get("content"), str)
        ]
        if isinstance(details.get("rejected_directions"), list)
        else []
    )
    sections: list[dict[str, str]] = []
    for section in (
        _section_from_strings(
            "Summary",
            [
                value
                for value in (
                    draft.get("summary"),
                    details.get("summary"),
                    details.get("problem"),
                    details.get("why_started"),
                )
                if isinstance(value, str)
            ],
        ),
        _section_from_strings(
            "Tasks",
            tasks,
        ),
        _section_from_strings(
            "Decisions",
            [
                item.get("decision")
                for item in details.get("decisions", [])
                if isinstance(item, dict) and isinstance(item.get("decision"), str)
            ]
            if isinstance(details.get("decisions"), list)
            else [],
        ),
        _section_from_strings(
            "Requirements",
            [
                item.get("requirement")
                for item in details.get("requirements", [])
                if isinstance(item, dict) and isinstance(item.get("requirement"), str)
            ]
            if isinstance(details.get("requirements"), list)
            else [],
        ),
        _section_from_strings(
            "Follow-ups",
            [*rejected_directions, *follow_ups, *open_questions],
        ),
    ):
        if section is not None:
            sections.append(section)
    return sections[:5]


def _draft_generation_summary(response: dict[str, Any]) -> dict[str, Any]:
    drafts = response.get("memory_drafts")
    uncertainties = response.get("overall_uncertainties")
    source_chunk_ids = response.get("source_chunk_ids")
    source_event_ids = response.get("source_event_ids")
    return {
        "draft_count": len(drafts) if isinstance(drafts, list) else 0,
        "overall_uncertainty_count": (len(uncertainties) if isinstance(uncertainties, list) else 0),
        "reason": truncate(response.get("draft_generation_reason"), 240),
        "source_chunk_count": (len(source_chunk_ids) if isinstance(source_chunk_ids, list) else 0),
        "source_event_count": (len(source_event_ids) if isinstance(source_event_ids, list) else 0),
        "summary_level": response.get("summary_level"),
    }


def _payload_from_memory_draft(
    context: dict[str, Any],
    draft: dict[str, Any],
    *,
    generator: str,
) -> dict[str, Any]:
    changed_files = context["changed_files"]
    draft_type = string_or_none(draft.get("type")) or "thinking_note"
    suggested_action = string_or_none(draft.get("suggested_user_action")) or "edit"
    return {
        "changed_files": changed_files[:100],
        "commit_sha": context["commits"][-1]["hash"] if context["commits"] else None,
        "event_count": context["event_count"],
        "first_event_id": context["first_event_id"],
        "generator": generator,
        "last_event_id": context["last_event_id"],
        "model": context["model"],
        "outcome": string_or_none(draft.get("outcome")) or draft["summary"],
        "prompt_event_ids": draft["evidence"]["source_event_ids"],
        "reason": draft["why_it_matters"],
        "sections": _sections_from_memory_draft(draft),
        "summary": draft["summary"],
        "tags": sorted(
            set(
                [
                    *tags_for_session(
                        changed_files=changed_files,
                        model=context["model"],
                        tool=context["tool"],
                    ),
                    draft_type,
                    suggested_action,
                ]
            )
        )[:12],
        "technologies": technologies_for_session(changed_files),
        "title": draft["title"],
        "tool": context["tool"],
    }


def build_memory_draft_payloads_from_context(
    context: dict[str, Any],
    *,
    trigger_reason: str,
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], dict[str, Any]]:
    context = {**context, "trigger_reason": trigger_reason}
    source_chunk_ids = source_chunk_ids_for_context(context)
    source_draft_ids = source_draft_ids_for_context(context)
    source_event_ids = source_event_ids_for_context(context)
    provider = provider_name(settings.memory_draft_generator)
    if provider not in {"gemini", "openai"}:
        return [], {
            "fallback_reason": f"{provider}_disabled",
            "source_draft_ids": source_draft_ids,
            "source_chunk_ids": source_chunk_ids,
            "source_event_ids": source_event_ids,
        }

    try:
        response = compile_memory_drafts(context, provider=provider)
        generator = generator_for_provider(provider, stage="draft")
        generation_metadata = {
            "draft_generation": _draft_generation_summary(response),
            "draft_generator": generator,
            **model_metadata_for_provider(provider),
        }
    except MemoryGenerationError as exc:
        return [], {
            "fallback_reason": str(exc),
            "requested_generator": generator_for_provider(provider, stage="draft"),
            "source_draft_ids": source_draft_ids,
            "source_chunk_ids": source_chunk_ids,
            "source_event_ids": source_event_ids,
        }

    drafts = (
        response.get("memory_drafts") if isinstance(response.get("memory_drafts"), list) else []
    )
    if not drafts:
        return [], {
            **generation_metadata,
            "fallback_reason": "Second-pass generator returned no usable drafts.",
            "source_draft_ids": source_draft_ids,
            "source_chunk_ids": source_chunk_ids,
            "source_event_ids": source_event_ids,
        }

    payloads: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for index, draft in enumerate(drafts, start=1):
        if not isinstance(draft, dict):
            continue
        draft_metadata = {
            **generation_metadata,
            "draft_confidence": draft.get("confidence"),
            "draft_details": draft.get("details"),
            "draft_evidence": draft.get("evidence"),
            "draft_index": index,
            "draft_type": draft.get("type"),
            "needs_user_verification": draft.get("needs_user_verification") is True,
            "overall_uncertainty_count": len(
                response.get("overall_uncertainties", [])
                if isinstance(response.get("overall_uncertainties"), list)
                else []
            ),
            "source_draft_ids": source_draft_ids,
            "suggested_user_action": draft.get("suggested_user_action"),
        }
        payloads.append(
            (
                _payload_from_memory_draft(context, draft, generator=generator),
                draft_metadata,
            )
        )
    return payloads, generation_metadata
