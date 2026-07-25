from __future__ import annotations

import re
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.services.memory.text import clean_text, truncate


KNOWLEDGE_PROJECTION_SCHEMA_VERSION = 1
MAX_KNOWLEDGE_NODES_PER_KIND = 12
MAX_KNOWLEDGE_NODES_PER_MEMORY = 40
MAX_KNOWLEDGE_NODES_PER_BATCH = 120
MAX_KNOWLEDGE_SOURCE_IDS = 8
KNOWLEDGE_REVIEW_STATES = {"confirmed", "rejected"}


def _normalized_identity(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _clean_source_ids(value: Any, fallback: list[str]) -> list[str]:
    values = value if isinstance(value, list) else fallback
    return list(
        dict.fromkeys(
            item.strip()
            for item in values
            if isinstance(item, str) and item.strip() and len(item.strip()) <= 200
        )
    )[:MAX_KNOWLEDGE_SOURCE_IDS]


def _candidate_id(
    *,
    kind: str,
    label: str,
    source_event_ids: list[str],
) -> str:
    evidence_key = "\x1f".join(source_event_ids)
    seed = f"promty:memory-knowledge:v1:{kind}:{_normalized_identity(label)}:{evidence_key}"
    return str(uuid5(NAMESPACE_URL, seed))


def build_memory_knowledge_projection(draft: dict[str, Any]) -> dict[str, Any]:
    """Build a bounded, evidence-linked semantic projection from one generated memory.

    The provider already returns structured decisions, rejected directions, open
    questions, and the draft type. This projector reuses that output instead of
    issuing another provider call or treating vector similarity as graph truth.
    """

    details = draft.get("details") if isinstance(draft.get("details"), dict) else {}
    evidence = draft.get("evidence") if isinstance(draft.get("evidence"), dict) else {}
    fallback_event_ids = _clean_source_ids(evidence.get("source_event_ids"), [])
    fallback_chunk_ids = _clean_source_ids(evidence.get("source_chunk_ids"), [])
    draft_confidence = (
        max(0.0, min(float(draft.get("confidence")), 1.0))
        if isinstance(draft.get("confidence"), (int, float))
        else 0.5
    )
    nodes: list[dict[str, Any]] = []
    seen: set[tuple[str, str, tuple[str, ...]]] = set()

    def append_node(
        *,
        confidence: Any,
        kind: str,
        label: Any,
        source_chunk_ids: Any,
        source_event_ids: Any,
        status: str,
        summary: Any = None,
    ) -> None:
        cleaned_label = truncate(clean_text(label), 320)
        if not cleaned_label or len(nodes) >= MAX_KNOWLEDGE_NODES_PER_MEMORY:
            return
        cleaned_event_ids = _clean_source_ids(source_event_ids, fallback_event_ids)
        cleaned_chunk_ids = _clean_source_ids(source_chunk_ids, fallback_chunk_ids)
        identity = (
            kind,
            _normalized_identity(cleaned_label),
            tuple(cleaned_event_ids),
        )
        if identity in seen:
            return
        seen.add(identity)
        cleaned_confidence = (
            max(0.0, min(float(confidence), 1.0))
            if isinstance(confidence, (int, float))
            else draft_confidence
        )
        nodes.append(
            {
                "confidence": cleaned_confidence,
                "evidence_type": "inferred",
                "id": _candidate_id(
                    kind=kind,
                    label=cleaned_label,
                    source_event_ids=cleaned_event_ids,
                ),
                "kind": kind,
                "label": cleaned_label,
                "source_chunk_ids": cleaned_chunk_ids,
                "source_event_ids": cleaned_event_ids,
                "status": status,
                "summary": truncate(clean_text(summary), 640) or None,
            }
        )

    decisions = details.get("decisions") if isinstance(details.get("decisions"), list) else []
    for item in decisions[:MAX_KNOWLEDGE_NODES_PER_KIND]:
        if not isinstance(item, dict):
            continue
        append_node(
            confidence=item.get("confidence"),
            kind="decision",
            label=item.get("decision"),
            source_chunk_ids=item.get("source_chunk_ids"),
            source_event_ids=item.get("source_event_ids"),
            status="active",
            summary=item.get("reason"),
        )

    rejected = (
        details.get("rejected_directions")
        if isinstance(details.get("rejected_directions"), list)
        else []
    )
    for item in rejected[:MAX_KNOWLEDGE_NODES_PER_KIND]:
        if not isinstance(item, dict):
            continue
        append_node(
            confidence=item.get("confidence"),
            kind="brainstorm",
            label=item.get("content"),
            source_chunk_ids=item.get("source_chunk_ids"),
            source_event_ids=item.get("source_event_ids"),
            status="discarded",
            summary=item.get("reason"),
        )

    open_questions = (
        details.get("open_questions") if isinstance(details.get("open_questions"), list) else []
    )
    for item in open_questions[:MAX_KNOWLEDGE_NODES_PER_KIND]:
        if not isinstance(item, dict):
            continue
        append_node(
            confidence=item.get("confidence"),
            kind="open_question",
            label=item.get("question"),
            source_chunk_ids=item.get("source_chunk_ids"),
            source_event_ids=item.get("source_event_ids"),
            status="open",
        )

    requirements = (
        details.get("requirements") if isinstance(details.get("requirements"), list) else []
    )
    for item in requirements[:MAX_KNOWLEDGE_NODES_PER_KIND]:
        if not isinstance(item, dict):
            continue
        append_node(
            confidence=item.get("confidence"),
            kind="requirement",
            label=item.get("requirement"),
            source_chunk_ids=item.get("source_chunk_ids"),
            source_event_ids=item.get("source_event_ids"),
            status="active",
            summary=item.get("reason"),
        )

    if draft.get("type") == "thinking_note":
        append_node(
            confidence=draft_confidence,
            kind="brainstorm",
            label=draft.get("title"),
            source_chunk_ids=fallback_chunk_ids,
            source_event_ids=fallback_event_ids,
            status="active",
            summary=draft.get("summary"),
        )

    return {
        "nodes": nodes,
        "schema_version": KNOWLEDGE_PROJECTION_SCHEMA_VERSION,
        "source": "memory_generation",
    }


def memory_knowledge_projection_from_metadata(
    metadata: Any,
    *,
    summary: Any = None,
    title: Any = None,
) -> dict[str, Any]:
    """Read a stored projection or derive one from older structured draft metadata."""

    values = metadata if isinstance(metadata, dict) else {}
    stored = values.get("knowledge_projection")
    if (
        isinstance(stored, dict)
        and stored.get("schema_version") == KNOWLEDGE_PROJECTION_SCHEMA_VERSION
        and isinstance(stored.get("nodes"), list)
    ):
        return stored
    details = values.get("draft_details")
    if not isinstance(details, dict):
        return {
            "nodes": [],
            "schema_version": KNOWLEDGE_PROJECTION_SCHEMA_VERSION,
            "source": "memory_generation",
        }
    return build_memory_knowledge_projection(
        {
            "confidence": values.get("draft_confidence"),
            "details": details,
            "evidence": values.get("draft_evidence"),
            "summary": summary,
            "title": title,
            "type": values.get("draft_type"),
        }
    )


def review_memory_knowledge_projection(
    metadata: Any,
    *,
    candidate_id: str,
    review_state: str,
    reviewed_at: str,
    reviewed_by: str,
    summary: Any = None,
    title: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if review_state not in {*KNOWLEDGE_REVIEW_STATES, "unreviewed"}:
        raise ValueError("Unsupported knowledge review state")
    projection = memory_knowledge_projection_from_metadata(
        metadata,
        summary=summary,
        title=title,
    )
    candidates = projection.get("nodes")
    if not isinstance(candidates, list):
        raise ValueError("Knowledge projection is unavailable")

    reviewed_candidate: dict[str, Any] | None = None
    updated_candidates: list[Any] = []
    for value in candidates:
        if not isinstance(value, dict) or value.get("id") != candidate_id:
            updated_candidates.append(value)
            continue
        candidate = dict(value)
        if review_state == "unreviewed":
            candidate.pop("review_state", None)
            candidate.pop("reviewed_at", None)
            candidate.pop("reviewed_by", None)
        else:
            candidate.update(
                {
                    "review_state": review_state,
                    "reviewed_at": reviewed_at,
                    "reviewed_by": reviewed_by,
                }
            )
        reviewed_candidate = candidate
        updated_candidates.append(candidate)

    if reviewed_candidate is None:
        raise ValueError("Knowledge node not found")
    metadata_values = metadata if isinstance(metadata, dict) else {}
    return (
        {
            **metadata_values,
            "knowledge_projection": {
                **projection,
                "nodes": updated_candidates,
            },
        },
        reviewed_candidate,
    )


def merge_memory_knowledge_projections(
    projections: list[Any],
    *,
    limit: int = MAX_KNOWLEDGE_NODES_PER_BATCH,
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for projection in projections:
        if not isinstance(projection, dict):
            continue
        candidates = projection.get("nodes")
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if len(nodes) >= max(limit, 0):
                break
            if not isinstance(candidate, dict):
                continue
            candidate_id = candidate.get("id")
            if not isinstance(candidate_id, str) or not candidate_id or candidate_id in seen:
                continue
            seen.add(candidate_id)
            nodes.append(candidate)
    return {
        "nodes": nodes,
        "schema_version": KNOWLEDGE_PROJECTION_SCHEMA_VERSION,
        "source": "memory_generation",
    }
