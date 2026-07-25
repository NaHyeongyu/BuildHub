# Promty AI Memory Architecture

## Vision

Promty is not another AI model.

Promty gives AI long-term memory and understanding of a software project.

GitHub stores what changed. Promty stores why it changed, how it changed, and which decisions led to the final implementation.

## High-Level Architecture

```text
Collector
    ↓
Event Store
    ↓
Pending Memory Draft
    ↓
Generated Context Memory
    ↓
Project Memory
    ↓
Context Builder
    ↓
LLM
    ↓
User Answer
```

## Current Implementation

Promty currently uses a pull-request-style memory pipeline:

- Collector events are normalized and stored in PostgreSQL JSONB.
- Pending `MemoryDraft` evidence packages are created after 20 user prompts, `SessionEnded`, or 1 hour without a new prompt.
- A pending draft requires a non-empty `ResponseReceived` payload after the
  latest prompt. A later `FilesChanged` event classifies it as an
  `implementation` slice; without a file event it is a `reasoning` slice, so
  requirements, decisions, alternatives, and open questions are not dropped.
- Encrypted raw events remain the source of truth. Pending drafts store a bounded,
  patch-free evidence projection and size metadata needed for one-time AI
  generation; provider inputs have independent per-field and total byte budgets.
- Materialization reads at most 500 event rows per slice and persists at most
  four slices per transaction. Longer windows resume from durable coverage
  metadata without duplicating event coverage.
- The user runs one Generate action for pending drafts. The API durably enqueues
  the batch and returns `202`; a separate worker generates bounded chunks,
  checkpoints each successful provider result, and marks consumed drafts only
  after finalization.
- The same provider response produces the user-facing memory and a bounded
  semantic projection. There is no second knowledge-extraction model call.
- Inferred semantic nodes can be confirmed or excluded from the Context Graph.
  Reviews are versioned with the source memory; rejected nodes are omitted from
  the next Project Memory compilation.
- Generated `MemoryTask` artifacts are shown in History and can be edited or removed through the memory APIs.
- Artifact generation is recorded in `artifact_generation_jobs`.
- Project Memory is compiled from generated and user-edited memories, not pending drafts.

Configuration:

```text
PROMTY_OPENAI_API_KEY
PROMTY_OPENAI_MODEL=gpt-5-mini
PROMTY_MEMORY_DRAFT_GENERATOR=openai
PROMTY_PROJECT_MEMORY_GENERATOR=openai
PROMTY_MEMORY_SLICE_EVENT_MAX_ROWS=500
PROMTY_MEMORY_SLICE_MAX_SLICES_PER_CALL=4
PROMTY_GEMINI_API_KEY
PROMTY_GEMINI_MODEL=gemini-2.5-flash
PROMTY_GEMINI_TIMEOUT_SECONDS=30
```

`PROMTY_MEMORY_GENERATOR` remains accepted as a shared default for draft and project memory generators. Legacy `PROMTY_*` names are still accepted for compatibility.

Use [promty.env.example](promty.env.example) as the copy source for `.env.local` or `backend/.env.local`. After setting the key, restart the backend process so the setting is loaded.

Generator status is available to authenticated web users:

```text
GET /api/projects/_memory/generator
```

The response reports configured providers and active generators without returning API keys.

## Next Build Order

1. Add generated-memory CRUD coverage for every History action.
2. Measure keyword retrieval quality and graph size before adding embeddings.
3. Materialize semantic nodes only when read-projection latency justifies it.
4. Add reviewed task-specific context packs and cited Project Chat answers.

## Memory Artifact

A `MemoryTask` represents one generated context memory inferred from one or more pending drafts.

Core fields:

- Title
- Summary
- Reason
- Outcome
- Changed files
- Prompt event IDs
- Commit SHA
- Tags
- Generator

These artifacts are the domain layer Promty will use for timeline, docs, replay, analytics, search, and AI explanations.

## Product Direction

Promty should focus on decision memory, not raw prompt history.

Important content surfaces:

- Project Memory timeline
- File memory: why a file changed over time
- Decision notes
- Implementation stories
- AI handoff briefs
- "Why did this change?" explanations
- Project Chat grounded in artifacts and original evidence
