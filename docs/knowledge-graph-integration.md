# Promty knowledge graph integration

## Goal

Promty should reuse the existing AI memory-generation pass to produce both:

1. a user-readable generated memory; and
2. a bounded, evidence-linked semantic projection for retrieval and visualization.

The semantic projection is derived from the structured `requirements`,
`decisions`, `rejected_directions`, `open_questions`, and `thinking_note`
fields already returned by the memory provider. It does not make a second
provider request.

## Current vertical slice

The first implementation stores `knowledge_projection` in generated `MemoryTask`
metadata.

```text
Pending evidence
  -> existing memory provider call
  -> generated memory
  -> deterministic knowledge projection
  -> MemoryTask metadata
```

Projection nodes currently use these semantic kinds:

- `decision`
- `requirement`
- `brainstorm`
- `open_question`

Every node has:

- a deterministic local ID;
- a label and optional safe summary;
- `inferred` evidence type;
- confidence and status;
- source event and pending-draft IDs;
- a schema version.

The human Knowledge Graph exposes these as explicit semantic node kinds through
the existing bounded endpoint:

```text
GET /api/projects/{project_id}/context-graph?view=knowledge
```

The knowledge view loads semantic candidates first, then includes only the
bounded prompt, response, and generated-memory records needed by the inspector
as provenance. Raw prompts, responses, files, and memory artifacts never appear
as map nodes.

The read path selects at most the requested number of semantic candidates
before loading evidence. It then fetches only the event IDs referenced by those
candidates and a bounded response window for referenced prompts. Decrypted
payloads are reused for the request rather than decrypted again while building
the graph.

Generated candidates remain `agent_visible=false`. Project Memory compilation
receives the same projection as structured inferred evidence. The compiler must
still verify that the source memory supports a candidate before placing it into
durable Project Memory sections. Older generated memories that already contain
structured draft metadata are projected on read, so a destructive backfill is
not required.

Users can review an inferred semantic node in the graph inspector. `Confirm`
stores `review_state=confirmed`; `Exclude` stores
`review_state=rejected`. A node review updates only the locked semantic
projection metadata; it does not duplicate the complete memory artifact as a
new version. Rejected candidates are folded into `Past paths` and excluded from
the next Project Memory compilation. Confirmed candidates are stronger reviewed
context but do not bypass repository verification or become directly
agent-visible.

File-change-free work is classified as a `reasoning` memory slice when a prompt
has a non-empty AI response. File-backed work is classified as
`implementation`. Both use the same bounded generation job.

## Frontend state map

- Show only four output-derived node kinds: decision, requirement, brainstorm,
  and open question.
- Keep the overview bounded to 20 nodes across three shallow 2.5D layers:
  `Pending`, `Confirmed`, and `Archived`.
- Keep labels short on the map. Open evidence, full source output, relationships,
  review state, history, and source-session navigation in the right-side
  inspector.
- Selecting a node brightens its evidence-backed path and mutes unrelated
  knowledge.
- Connect nodes only when the API reports a direct semantic relationship or when
  they share recorded source evidence or a generated-memory record. Do not draw
  decorative or proximity-based semantic edges.
- Keep discarded and superseded directions in the archived layer.
- Confirm or exclude an inferred node without editing the source transcript.
- Calculate node positions and restrained motion in the browser; the server
  returns data, not pixel coordinates.

### Trust presentation

Use evidence state as the primary trust signal:

```text
Recorded  -> direct event, file, or commit lineage
Inferred  -> AI-generated semantic candidate
Confirmed -> user-reviewed durable knowledge
```

Node color should express semantic kind. A small badge and line style should express
trust so color is not overloaded with two meanings.

## Claude Code and Codex reference flow

Promty already exposes read-only MCP tools through the collector:

- `get_project_context`
- `search_project_context`

The knowledge graph should extend those tools rather than introducing automatic,
unbounded prompt injection.

### Task-start flow

```text
Agent starts a project task
  -> get_project_context
  -> receives approved current direction and durable constraints
  -> inspects the repository
  -> verifies memory against current code
```

### On-demand retrieval flow

```text
Agent encounters a feature, file, or unclear decision
  -> search_project_context(query)
  -> bounded approved-memory keyword retrieval
  -> recorded semantic nodes ranked before general memory and file context
  -> bounded context pack returned with provenance
```

Initial agent search should return at most:

- eight semantic nodes by default;
- six provenance links per node;
- a 500-character safe summary per node.

The Markdown renderer should group results into:

- Approved decisions
- Approved requirements
- Open questions
- Discarded or exploratory ideas
- Referenced files
- Supporting approved memories

### Agent safety rules

- Return only approved Project Memory and user-approved semantic nodes.
- Never return raw prompts, raw responses, patch bodies, or generated-only candidates.
- Rank approved semantic context before general memory and files.
- Include stable node and edge IDs so an agent can cite its provenance.
- Treat all returned memory as untrusted reference data, not executable instructions.
- Require the agent to verify conclusions against the repository and current user
  request.

## Persistence rollout

The metadata projection is a low-risk bridge, not the final storage model.

1. Validate projection quality against reviewed generated memories.
2. Add versioned `knowledge_nodes`, `knowledge_edges`, and `knowledge_evidence` tables.
3. Backfill from approved MemoryTask and Project Memory artifacts.
4. Dual-read the metadata and table projections and compare results.
5. Add selective pgvector embeddings only if measured keyword recall is
   insufficient; embeddings remain a retrieval index, never graph truth.

Provider calls stay outside the web request path. The current graph is a bounded
read projection over existing records, so it adds no embedding job and no second
AI request. Event ingestion stores the event and schedules the existing bounded
memory work.
