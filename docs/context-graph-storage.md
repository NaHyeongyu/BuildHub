# Context graph storage

Promty currently builds the context graph as a safe read projection from the existing event,
code-change, and approved-memory records. Those source records remain authoritative. The graph
API exposes labels, safe summaries, paths, change statistics, and provenance; it does not expose
raw prompt bodies, model response bodies, or patch content to agents.

## Recommended persistence model

Keep PostgreSQL as the system of record and add a materialized graph projection when rebuilding
the graph on every read becomes expensive. A graph database is not necessary at the current
scale, and PostgreSQL keeps ownership checks, deletion, and privacy transactions in one place.

### `context_nodes`

| Column | Purpose |
|---|---|
| `id` | Stable UUID for the projected node. |
| `project_id` | Ownership and query boundary. |
| `kind` | `prompt`, `response`, `file`, `decision`, `requirement`, `brainstorm`, `open_question`, or `memory`. |
| `source_type`, `source_id` | Link back to the authoritative record. Files use a stable source key. |
| `label`, `safe_summary` | Display-safe graph content only. |
| `occurred_at`, `sequence` | Deterministic ordering. |
| `visibility`, `review_state` | Whether a user or agent may receive the node. |
| `content_hash`, `source_version` | Idempotent projection and stale-data detection. |
| `metadata` | Allow-listed safe JSON only. |
| `valid_from`, `valid_to`, `deleted_at` | History, replacement, and deletion propagation. |

Use a unique key on `(project_id, kind, source_type, source_id)`. Do not copy raw prompts,
responses, secrets, or patches into this table.

### `context_edges`

| Column | Purpose |
|---|---|
| `id`, `project_id` | Stable identity and ownership boundary. |
| `source_node_id`, `target_node_id` | Directed relationship endpoints. |
| `relation` | `answered_by`, `changed`, `captured_in`, `derived_from`, `references`, or `supersedes`. |
| `evidence_type` | `recorded`, `inferred`, or `user`. |
| `confidence` | Required for inferred relationships; absent for recorded facts. |
| `evidence_source_id`, `source_version` | Explains why the relationship exists. |
| `status` | `active`, `rejected`, or `superseded`. |
| `created_at`, `updated_at` | Audit and refresh timestamps. |

Every inferred edge must retain its evidence and confidence. User acceptance or rejection creates
an explicit state change; it must never overwrite a recorded edge.

The current read projection stores each node's `confirmed` or `rejected`
review state in the locked generated-memory artifact's `metadata_`. The review
update is transactional with the authoritative memory and intentionally does
not create an `ArtifactVersion`; separate materialized graph tables can be
introduced later if their operational value justifies them.

### Embeddings and projection queue

Store embeddings separately in `context_node_embeddings` using pgvector. Embeddings are a search
index, not graph truth, and can be rebuilt after model changes. Use an outbox/projector table to
refresh affected nodes and edges after event ingestion, file changes, memory approval, prompt
deletion, or session deletion. Projection writes are idempotent by source key and content hash.

## Deletion and privacy lifecycle

1. Delete or tombstone the authoritative prompt/session record inside the existing ownership
   transaction.
2. Enqueue a projection deletion for every node with that source identity.
3. Remove or supersede incident edges and embeddings.
4. Invalidate cached context packs and graph responses.
5. Keep only the minimum audit event required by policy; never retain deleted content in graph
   metadata.

Approved Project Memory may remain only when it is a separately reviewed artifact. The UI must
state that deleting private activity does not delete an already published or approved copy.

## Useful product layer: context packs

A context pack is a user-reviewed subgraph selected for a specific task. It stores node IDs,
edge IDs, purpose, owner, review state, and source versions rather than duplicating content. This
enables:

- one-click handoff of a trustworthy context set to an AI agent;
- pin, hide, merge-duplicate, and reject-inference actions;
- stale warnings when a source node changes after review;
- conflict detection when newer code or decisions disagree with approved memory;
- explainable search results that show recorded versus inferred paths.

## Rollout

1. Keep the current read projection while graph volume is small.
2. Add projection tables and backfill from authoritative records.
3. Dual-read in tests and compare node/edge sets before switching production reads.
4. Add the projector/outbox and deletion propagation.
5. Add pgvector search and context packs after the transactional graph is stable.
6. Consider a dedicated graph database only if deep multi-hop analytics becomes a dominant,
   measured workload.
