# Promty Database

Promty uses PostgreSQL with SQLAlchemy 2.x models and Alembic migrations.

## Stack

```text
PostgreSQL
SQLAlchemy 2.x
Alembic
UUID primary keys
JSONB event payloads
timezone-aware UTC timestamps
```

## Initialization

Start PostgreSQL:

```bash
docker compose up -d postgres
```

Install backend dependencies:

```bash
./.venv/bin/python -m pip install -r backend/requirements.txt
```

Run migrations:

```bash
./.venv/bin/alembic -c backend/alembic.ini upgrade head
```

The backend reads `DATABASE_URL` and falls back to:

```text
postgresql+psycopg://promty:promty@localhost:5432/promty
```

Optional security environment variables:

```text
PROMTY_API_TOKEN
PROMTY_CORS_ORIGINS
PROMTY_API_PUBLIC_URL
PROMTY_APP_URL
PROMTY_DATABASE_POOL_SIZE
PROMTY_DATABASE_MAX_OVERFLOW
PROMTY_DATABASE_POOL_TIMEOUT_SECONDS
PROMTY_DATABASE_POOL_RECYCLE_SECONDS
PROMTY_DATABASE_STATEMENT_TIMEOUT_MS
PROMTY_DATABASE_LOCK_TIMEOUT_MS
PROMTY_GITHUB_CLIENT_ID
PROMTY_GITHUB_CLIENT_SECRET
PROMTY_GITHUB_TOKEN_ENCRYPTION_KEY
PROMTY_GITHUB_TOKEN_ENCRYPTION_PREVIOUS_KEYS
PROMTY_APP_ENCRYPTION_KEY
PROMTY_APP_ENCRYPTION_PREVIOUS_KEYS
PROMTY_APP_ENCRYPTION_KEY_ID
PROMTY_ADMIN_GITHUB_IDS
PROMTY_AUTH_RATE_LIMIT_REQUESTS
PROMTY_AUTH_RATE_LIMIT_WINDOW_SECONDS
PROMTY_ADMIN_RATE_LIMIT_REQUESTS
PROMTY_ADMIN_RATE_LIMIT_WINDOW_SECONDS
PROMTY_COMMUNITY_RATE_LIMIT_REQUESTS
PROMTY_COMMUNITY_RATE_LIMIT_WINDOW_SECONDS
PROMTY_INGEST_RATE_LIMIT_REQUESTS
PROMTY_INGEST_RATE_LIMIT_WINDOW_SECONDS
PROMTY_TRUSTED_PROXY_CIDRS
PROMTY_ADMIN_AUDIT_RETENTION_DAYS
PROMTY_SUPPORT_EMAIL_PROVIDER
PROMTY_SUPPORT_NOTIFICATION_EMAILS
PROMTY_SUPPORT_FROM_EMAIL
PROMTY_SUPPORT_RATE_LIMIT_REQUESTS
PROMTY_SUPPORT_RATE_LIMIT_WINDOW_SECONDS
PROMTY_BUFFER_API_KEY
PROMTY_BUFFER_CHANNEL_IDS
PROMTY_DEVTO_API_KEY
PROMTY_DEVTO_ORGANIZATION_ID
PROMTY_GITHUB_MARKETING_TOKEN
PROMTY_GITHUB_MARKETING_REPOSITORY_ID
PROMTY_GITHUB_MARKETING_DISCUSSION_CATEGORY_ID
PROMTY_PROMPT_MAX_CHARS
PROMTY_RESPONSE_MAX_CHARS
PROMTY_EVENT_BATCH_MAX_BODY_BYTES
PROMTY_MEMORY_SLICE_EVENT_MAX_ROWS
PROMTY_MEMORY_SLICE_MAX_SLICES_PER_CALL
PROMTY_MEMORY_DRAFT_PROMPT_MAX_BYTES
PROMTY_MEMORY_DRAFT_EVIDENCE_MAX_BYTES
PROMTY_PROJECT_MEMORY_PROMPT_MAX_BYTES
PROMTY_MEMORY_PROVIDER_RESPONSE_MAX_BYTES
PROMTY_MEMORY_PROVIDER_OUTPUT_MAX_TOKENS
PROMTY_MEMORY_PROVIDER_WALL_DEADLINE_SECONDS
PROMTY_PROJECT_MEMORY_BATCH_MAX_DRAFTS
PROMTY_MEMORY_WORKER_POLL_SECONDS
PROMTY_MEMORY_WORKER_MAX_POLL_SECONDS
PROMTY_MEMORY_WORKER_HEARTBEAT_SECONDS
PROMTY_MEMORY_WORKER_CHUNK_CONCURRENCY
PROMTY_GEMINI_API_KEY
PROMTY_GEMINI_MODEL
PROMTY_GEMINI_TIMEOUT_SECONDS
PROMTY_MEMORY_GENERATOR
PROMTY_MEMORY_DRAFT_GENERATOR
PROMTY_PROJECT_MEMORY_GENERATOR
PROMTY_OAUTH_STATE_SECRET
PROMTY_JWT_SECRET
PROMTY_ACCESS_TOKEN_TTL_SECONDS
PROMTY_REFRESH_TOKEN_TTL_SECONDS
PROMTY_REFRESH_TOKEN_IDLE_TTL_SECONDS
PROMTY_REFRESH_TOKEN_ROTATION_GRACE_SECONDS
PROMTY_SESSION_COOKIE_NAME
PROMTY_REFRESH_COOKIE_NAME
PROMTY_SESSION_COOKIE_SECURE
PROMTY_SESSION_COOKIE_SAMESITE
PROMTY_OAUTH_STATE_COOKIE_NAME
```

Web login uses a short-lived access cookie and a rotating refresh cookie. Access
tokens default to one hour. Refresh sessions have a 180-day absolute lifetime,
expire after 30 days without a successful refresh, and retain the immediately
previous refresh token for a 30-second concurrency grace period. Refresh token
hashes and revocation state are stored in `web_sessions`; plaintext refresh
credentials are only sent through `HttpOnly` cookies.

PostgreSQL connections use a bounded SQLAlchemy queue pool. Defaults are a pool
size of 5, maximum overflow of 2, a 5-second checkout timeout, and a 300-second
connection recycle interval. Pool-only options are omitted for SQLite so
in-memory tests keep their native pool behavior.

`PROMTY_DATABASE_STATEMENT_TIMEOUT_MS` and
`PROMTY_DATABASE_LOCK_TIMEOUT_MS` optionally set PostgreSQL session-level
timeouts for application connections. Both default to `0` (PostgreSQL's
disabled behavior) so deployments can introduce limits after measuring their
slowest legitimate queries.

Memory materialization reads at most 500 event rows per slice by default.
Configure this with `PROMTY_MEMORY_SLICE_EVENT_MAX_ROWS` (or its `PROMTY_`
alias). Values below 2 are clamped to 2, and the effective prompt target is
clamped to one less than this ceiling so the prompt look-ahead query is bounded
by the same setting. Oversized prompt windows are persisted as deterministic,
contiguous continuation slices; their logical end sequence is checkpointed so
later transactions resume without skipping or duplicating event coverage.
One materialization call persists at most four slices by default; configure
this with `PROMTY_MEMORY_SLICE_MAX_SLICES_PER_CALL` (or its `PROMTY_`
alias). The worker detects unfinished groups from the persisted aggregate
`max(materialization_end_sequence) > max(end_sequence)`. When the call ceiling
lands exactly on a completed logical-window boundary, the last current
artifact carries the operational `memory_resume_required` marker instead.
The worker clears that marker inside the resume transaction and reapplies it
only if the next call also reaches its ceiling, including for ended sessions.
Continuation slices load one latest prompt as bounded, context-only guidance;
that anchor is excluded from the slice event timeline, event count, and source
prompt IDs so coverage remains non-overlapping.
Every slice writer locks the session row before reading or advancing this
monotonic cursor. Runtime state is projected from the latest indexed slice row,
while unfinished slice groups retain the aggregate/marker recovery check. Idle
session selection reads the collector-maintained `sessions.last_activity_at`
column through a partial index instead of grouping the complete events table on
every scan. A failed artifact-generation job rolls back memory changes to a
nested savepoint while preserving its failed job status.

Each API or worker process owns a separate pool. The checked-in Compose and EC2
configuration budgets at most 7 connections for the API process (`5 + 2`) and
3 for the Project Memory worker (`2 + 1`), for a combined application maximum
of 10 connections per deployment. Keep `max_connections` headroom for Alembic,
backups, health checks, and operator sessions when changing these values.

The Project Memory worker generates one draft chunk at a time by default so the
API, worker, and local PostgreSQL can safely share a small production instance.
Set `PROMTY_MEMORY_WORKER_CHUNK_CONCURRENCY` to tune this independently of the
database pool on a larger worker host. Each source draft is
marked `sent_to_ai_at` before provider work starts. Provider calls, failed
batches, and interrupted worker leases are never retried; a new batch can only
claim draft sources that have never been sent to AI.

Memory provider responses are capped at 1 MiB and generation requests ask for
at most 8192 output tokens by default. The single provider attempt has a
120-second wall deadline, including bounded response reads.
Configure these with `PROMTY_MEMORY_PROVIDER_RESPONSE_MAX_BYTES`,
`PROMTY_MEMORY_PROVIDER_OUTPUT_MAX_TOKENS`, and
`PROMTY_MEMORY_PROVIDER_WALL_DEADLINE_SECONDS`; equivalent `PROMTY_`
aliases are also supported.
The generation preview estimates provider cost from the configured prompt and
output ceilings. Keep the `*_INPUT_USD_PER_MILLION_TOKENS` and
`*_OUTPUT_USD_PER_MILLION_TOKENS` OpenAI/Gemini rate settings aligned with the
provider price sheet; these values affect only the preview and never billing.

Each Project Memory batch claims at most 60 pending drafts by default, ordered
deterministically by creation time and artifact ID. Configure this with
`PROMTY_PROJECT_MEMORY_BATCH_MAX_DRAFTS` (or its `PROMTY_` alias); values
below 1 are clamped to 1. Excess drafts remain pending for a later batch. This
also bounds each batch's queued chunk futures and durable chunk checkpoints.

The worker starts with a 2-second poll interval and exponentially backs off to
10 seconds while no work is available. It resets to the base interval as soon
as work is processed and heartbeats a running batch lease every 60 seconds.
Configure these with `PROMTY_MEMORY_WORKER_POLL_SECONDS`,
`PROMTY_MEMORY_WORKER_MAX_POLL_SECONDS`, and
`PROMTY_MEMORY_WORKER_HEARTBEAT_SECONDS`; keep the heartbeat comfortably
below the 10-minute batch lease.

The worker also updates a process heartbeat file used by the Compose and AWS
container health checks. Configure its path and maximum age with
`PROMTY_MEMORY_WORKER_HEALTH_FILE` and
`PROMTY_MEMORY_WORKER_HEALTH_TIMEOUT_SECONDS`.

Project list counters are incrementally maintained in `project_stats`. Check for
drift or repair the rollup after an operational incident with:

```bash
cd backend
../.venv/bin/python -m scripts.reconcile_project_stats --check
../.venv/bin/python -m scripts.reconcile_project_stats
```

Health endpoints have distinct operational meanings:

```text
GET /health        compatibility check; does not query PostgreSQL
GET /health/live   process liveness; does not query PostgreSQL
GET /health/ready  readiness; returns 503 when SELECT 1 fails
```

Browser reads require GitHub login and a valid Promty JWT session cookie. The session cookie is HttpOnly; JavaScript does not read the token directly. JWTs include a server-side session identifier, logout revokes that session immediately, and production sessions are capped at eight hours.

The administrator-only bilingual marketing studio stores source briefs, Korean and
English channel variants, approval state, schedules, and external delivery results
in `marketing_content`. Publisher credentials remain server-side. See
`docs/marketing-content-studio.md` for the Buffer, DEV.to, GitHub Discussions, and
manual community-posting boundaries.

Gemini-backed memory generation reads `PROMTY_GEMINI_API_KEY` from `.env.local`, `backend/.env.local`, or the process environment. The backend must be restarted after changing this key. `GET /api/projects/_memory/generator` reports whether Gemini is configured without exposing the key.

`POST /api/events/batch` requires `Authorization: Bearer <token>` by default. GitHub CLI login issues per-user collector tokens stored as hashes in `collector_tokens`. If `PROMTY_API_TOKEN` is set, the endpoint also accepts that global token. Web JWTs and collector tokens are intentionally separate. Anonymous ingest is available only when `PROMTY_ALLOW_ANONYMOUS_INGEST=true` is explicitly set for isolated local development.

The web OAuth flow stores a short-lived HttpOnly nonce cookie and verifies it against the signed OAuth state in the callback. `PROMTY_CORS_ORIGINS` is a comma-separated allowlist and defaults to the local Vite origins.

Application-level encryption protects sensitive development context at rest. `PROMTY_APP_ENCRYPTION_KEY` is the preferred dedicated key for prompt text, AI response text, and unified diff patch text. During rotation, keep old decrypt-only keys in the comma-separated `PROMTY_APP_ENCRYPTION_PREVIOUS_KEYS` value until stored data has been migrated. If a dedicated key is not configured, the backend falls back to the JWT/OAuth/API secret chain for local compatibility. Production deployments should always set a dedicated application key and key id.

## ERD

```text
users
  |
  | 1:N
  v
devices

users
  |
  | 1:N
  v
collector_tokens

users
  |
  | 1:N
  v
projects
  |
  | 1:N
  v
sessions
  |
  | 1:N
  v
events
  |
  | 1:N
  v
artifacts
```

`artifacts.event_id` is nullable. Artifacts are deleted when their project is deleted. If an event is deleted directly, the artifact is preserved and `event_id` is set to null.

## Tables

### users

```text
id UUID PK
github_id string unique
email string unique nullable
username string unique
avatar_url string nullable
created_at timestamptz
updated_at timestamptz
```

### devices

```text
id UUID PK
user_id UUID FK -> users.id on delete cascade
hostname string
os string
collector_version string nullable
last_seen timestamptz
created_at timestamptz
```

### collector_tokens

```text
id UUID PK
user_id UUID FK -> users.id on delete cascade
token_hash string unique
name string
created_at timestamptz
last_used_at timestamptz nullable
revoked_at timestamptz nullable
```

Only token hashes are persisted. Raw collector tokens are returned once to the CLI login callback.

### support_inquiries

```text
id UUID PK
user_id UUID FK -> users.id on delete cascade
requester_username string
requester_email string
category string
subject encrypted text
message encrypted text
status new | in_progress | resolved
notification_status pending | sent | failed | disabled
notification_message_id string nullable
notification_error string nullable
notified_at timestamptz nullable
created_at timestamptz
updated_at timestamptz
```

The inquiry row is committed before email delivery. Subject and message use the
application text-encryption envelope with dedicated purposes. SES delivery
results are stored separately so a notification outage never removes the
submitted inquiry.

### projects

```text
id UUID PK
owner_id UUID FK -> users.id on delete cascade
name string
slug string
description text nullable
visibility string public/private
git_remote string nullable
local_path_hash string nullable
default_branch string
created_at timestamptz
updated_at timestamptz
```

Constraints:

```text
visibility in ('public', 'private')
unique(owner_id, slug)
```

### public_project_saves

```text
user_id UUID PK/FK -> users.id on delete cascade
project_id UUID PK/FK -> projects.id on delete cascade
created_at timestamptz
```

### public_project_views

```text
id UUID PK
project_id UUID FK -> projects.id on delete cascade
viewer_id UUID nullable FK -> users.id on delete set null
source string
viewed_at timestamptz
```

Public community detail opens are counted for non-owners. Repeated opens by the
same signed-in viewer within 30 minutes are collapsed into one view. The viewer
reference enables unique-viewer analytics without storing an IP address or raw
browser fingerprint.

### project_stats

```text
project_id UUID PK/FK -> projects.id on delete cascade
session_count int
event_count int
prompt_count int
tracked_files int
latest_event_at timestamptz nullable
updated_at timestamptz
```

The collector updates this activity rollup once per affected project and event
batch. Project list reads join it instead of regrouping the complete sessions,
events, and project-files tables. Migration backfill initializes existing
projects; memory and pending-draft counts continue to use their bounded artifact
indexes.

### sessions

```text
id UUID PK
project_id UUID FK -> projects.id on delete cascade
device_id UUID FK -> devices.id on delete set null
tool string
tool_version string nullable
model string nullable
cwd string nullable
branch string nullable
started_at timestamptz
last_activity_at timestamptz nullable
ended_at timestamptz nullable
```

`last_activity_at` is updated transactionally with event ingestion and is
backfilled from `max(events.created_at)` during migration. Open-session worker
scans use the partial `(last_activity_at, started_at, id)` index.

### events

```text
id UUID PK
project_id UUID FK -> projects.id on delete cascade
session_id UUID FK -> sessions.id on delete cascade
sequence int
schema_version int
tool string
event_type string
payload JSONB
created_at timestamptz
```

Storage policy:

```text
PromptSubmitted.payload.prompt is encrypted before persistence.
Prompt text is capped by PROMTY_PROMPT_MAX_CHARS, default 50000 characters.
Prompt truncation metadata is stored as prompt_truncated, prompt_original_length, and prompt_storage_limit.
ResponseReceived.payload.response is encrypted before persistence.
Response text is capped by PROMTY_RESPONSE_MAX_CHARS, default 50000 characters.
Response truncation metadata is stored as response_truncated, response_original_length, and response_storage_limit.
FilesChanged.payload.changes[].patch is encrypted before persistence.
Event batch request bodies are capped by PROMTY_EVENT_BATCH_MAX_BODY_BYTES, default 8388608 bytes.
Event batches accept at most 100 events and 2000 aggregate file-change entries.
POST /api/events/batch is rate limited by a SHA-256 credential fingerprint and by source address; raw bearer tokens are never used as limiter keys.
Queryable metadata such as project_id, session_id, event_type, timestamps, file paths, and line counts remains plaintext.
```

Indexes:

```text
unique(project_id, session_id, sequence)
(project_id, created_at)
(created_at, sequence)
(event_type, created_at)
(session_id, created_at)
(project_id, session_id, created_at)
GIN(payload)
```

Constraints:

```text
sequence > 0
schema_version >= 1
tool in supported tools
event_type in supported event types
```

### code_change_patches

Prompt/turn-level file diffs extracted from `FilesChanged` events. This table is optimized for showing GitHub-style code review UI without scanning every event payload.

```text
id UUID PK
project_id UUID FK -> projects.id on delete cascade
session_id UUID FK -> sessions.id on delete cascade
event_id UUID FK -> events.id on delete cascade
prompt_event_id UUID nullable
path string
old_path string nullable
status string
additions int nullable
deletions int nullable
patch text nullable
patch_truncated boolean
binary boolean
metadata JSONB
created_at timestamptz
```

Indexes:

```text
(project_id, prompt_event_id)
(project_id, created_at)
event_id
session_id
```

Patch policy:

```text
Store unified diffs per PromptSubmitted -> FilesChanged turn.
Do not store full before/after file bodies.
Encrypt stored patch text with application-level encryption.
Do not store patch text for sensitive paths, excluded directories, binary files, or oversized files.
Use metadata.patch_omitted_reason when patch text is omitted.
```

### session constraints

```text
ended_at is null or ended_at >= started_at
```

### artifacts

```text
id UUID PK
schema_version int
project_id UUID FK -> projects.id on delete cascade
session_id UUID nullable FK -> sessions.id on delete set null
event_id UUID nullable FK -> events.id on delete set null
type string
title string
summary text nullable
reason text nullable
outcome text nullable
storage_key string
tags JSONB array
changed_files JSONB array
prompt_event_ids JSONB array
commit_sha string nullable
model string nullable
generator string nullable
metadata JSONB object
created_at timestamptz
updated_at timestamptz
```

`MemoryTask` artifacts are generated from completed sessions and form Promty's project memory layer.

### artifact_generation_jobs

```text
id UUID PK
project_id UUID FK -> projects.id on delete cascade
session_id UUID FK -> sessions.id on delete cascade
artifact_id UUID nullable FK -> artifacts.id on delete set null
status pending/running/succeeded/failed
reason string
generator string
error text nullable
metadata JSONB object
created_at timestamptz
updated_at timestamptz
completed_at timestamptz nullable
```

## Current Ingest Behavior

The current Event API contract does not include user, device, project, or session setup fields beyond `project_id` and `session_id`.

To keep the API contract unchanged, the persistence layer currently:

```text
creates a deterministic system user when needed
creates a placeholder project when project_id is new
assigns projects uploaded with collector tokens to the token owner
creates a session when session_id is new
stores the incoming event payload as JSONB
requires Bearer auth by default
accepts per-user collector tokens issued through GitHub OAuth
optionally accepts a global PROMTY_API_TOKEN
requires web JWT auth for browser event reads
rejects empty batches and batches larger than 500 events
accepts exact event-id replays as idempotent no-ops
rejects event-id replays with changed content
rejects project/session mismatches
rejects duplicate sequences within a project/session
```

This keeps backend logic tool-independent. Codex, Claude Code, Cursor, and Gemini-specific normalization remains in the collector.

Future authentication and device registration work should replace the system user and null device behavior without changing the Event contract.

## Migrations

```text
0001_initial_schema
0002_event_security_indexes
0003_collector_tokens
```

`0002_event_security_indexes` adds event sequence uniqueness, event check constraints, session time ordering, latest/event-type/session query indexes, and a JSONB GIN index for future payload filtering.

`0003_collector_tokens` adds hashed per-user collector tokens for CLI login and upload authentication.

`0011_promty_memory_artifacts` extends artifacts for generated project memory and adds artifact generation jobs.

`0027_admin_audit_logs` adds a durable, retention-managed record of administrator console
requests and cross-owner project access. Audit rows contain actor and request metadata but
never session tokens, emails, prompt text, response text, or diff content.

`0028_at_most_once_memory` adds lease and attempt fencing for Project Memory batches so an
administrator can invalidate an in-flight attempt without allowing its late provider result
to write to PostgreSQL.

`0029_admin_user_lifecycle` adds `users.suspended_at` and `users.suspension_reason`.
Suspended users are rejected by both browser-session and collector-token authentication;
the sole configured administrator cannot suspend or delete their own account.

`0032_public_project_saves` adds per-user saved public projects.

`0033_support_inquiries` stores encrypted support inquiries and notification delivery state.

`0034_public_project_views` adds privacy-conscious public project view events and time-series indexes.
### `admin_alert_states`

관리자별 운영 알림 처리 상태를 저장합니다. `alert_key`와 현재 조건의 해시를 함께 기록해 읽음, 24시간 보류, 해결 상태를 유지하며 조건이 달라지면 다시 읽지 않은 알림으로 노출합니다.

## Weekly project popularity

커뮤니티의 `이번 주 인기` 정렬은 최근 7일의 사용자 행동을 요청 시점에 집계합니다.

```text
score = unique_viewers × 2
      + repeat_views × 0.25
      + new_active_saves × 8
```

프로젝트 소유자의 조회와 저장은 점수에서 제외됩니다. 조회는 사용자·프로젝트별 30분 중복 방지를 적용하고, 저장은 사용자·프로젝트별 활성 저장 한 건만 인정합니다. 동점이면 신규 저장, 고유 조회자, 최근 프로젝트 활동 순으로 정렬합니다.
