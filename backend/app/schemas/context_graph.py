from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ContextGraphNodeKind = Literal[
    "prompt",
    "response",
    "file",
    "decision",
    "requirement",
    "brainstorm",
    "open_question",
    "memory",
]
ContextGraphEdgeKind = Literal[
    "answered_by",
    "changed",
    "captured_in",
    "derived_from",
    "references",
    "supersedes",
]


class StrictResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContextGraphNodeResponse(StrictResponse):
    id: str
    kind: ContextGraphNodeKind
    label: str
    summary: str | None
    occurred_at: str | None
    session_id: str | None
    sequence: int | None
    agent_visible: bool
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextGraphEdgeResponse(StrictResponse):
    id: str
    source: str
    target: str
    kind: ContextGraphEdgeKind
    inferred: bool


class ContextGraphResponse(StrictResponse):
    nodes: list[ContextGraphNodeResponse]
    edges: list[ContextGraphEdgeResponse]
    facets: dict[str, int]
    query: str | None
    truncated: bool
    safety_notice: str


class ContextGraphNodeReviewRequest(StrictResponse):
    action: Literal["confirm", "reject", "reset"]


class ContextGraphNodeReviewResponse(StrictResponse):
    node_id: str
    review_state: Literal["confirmed", "rejected", "unreviewed"]
    reviewed_at: str | None
