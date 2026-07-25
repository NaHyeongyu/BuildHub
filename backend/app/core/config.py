from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _load_local_env() -> None:
    """Load ignored local env files for development without overriding shell env."""
    root_dir = Path(__file__).resolve().parents[3]
    for env_path in (root_dir / ".env.local", root_dir / "backend" / ".env.local"):
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            value = value.strip().strip("\"'")
            if name and name not in os.environ:
                os.environ[name] = value


_load_local_env()


def _promote_legacy_environment() -> None:
    """Expose legacy PromptHub settings under the canonical Promty prefix."""

    legacy_prefix = "PROMPTHUB_"
    canonical_prefix = "PROMTY_"
    for name, value in tuple(os.environ.items()):
        if not name.startswith(legacy_prefix):
            continue
        canonical_name = f"{canonical_prefix}{name[len(legacy_prefix):]}"
        os.environ.setdefault(canonical_name, value)


_promote_legacy_environment()


def _optional_env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    return value


def _optional_env_any(*names: str) -> str | None:
    for name in names:
        value = _optional_env(name)
        if value is not None:
            return value
    return None


def _csv_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.environ.get(name)
    if value is None:
        return default

    parsed = tuple(item.strip() for item in value.split(",") if item.strip())
    return parsed or default


def _bool_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _bounded_int_env(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    value = _int_env(name, default)
    if value < minimum or (maximum is not None and value > maximum):
        return default
    return value


def _str_env_any(names: tuple[str, ...], default: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def _int_env_any(names: tuple[str, ...], default: int) -> int:
    for name in names:
        value = os.environ.get(name)
        if value is None:
            continue
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _float_env_any(names: tuple[str, ...], default: float) -> float:
    for name in names:
        value = os.environ.get(name)
        if value is None:
            continue
        try:
            return float(value)
        except ValueError:
            return default
    return default


@dataclass(frozen=True)
class Settings:
    database_url: str = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://promty:promty@localhost:5432/promty",
    )
    database_pool_size: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_DATABASE_POOL_SIZE",
            5,
            minimum=1,
        )
    )
    database_max_overflow: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_DATABASE_MAX_OVERFLOW",
            2,
            minimum=0,
        )
    )
    database_pool_timeout_seconds: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_DATABASE_POOL_TIMEOUT_SECONDS",
            5,
            minimum=1,
        )
    )
    database_pool_recycle_seconds: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_DATABASE_POOL_RECYCLE_SECONDS",
            300,
            minimum=1,
        )
    )
    database_statement_timeout_ms: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_DATABASE_STATEMENT_TIMEOUT_MS",
            0,
            minimum=0,
        )
    )
    database_lock_timeout_ms: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_DATABASE_LOCK_TIMEOUT_MS",
            0,
            minimum=0,
        )
    )
    api_public_url: str = os.environ.get("PROMTY_API_PUBLIC_URL", "http://127.0.0.1:8011")
    app_url: str = os.environ.get("PROMTY_APP_URL", "http://127.0.0.1:5173")
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: _csv_env(
            "PROMTY_CORS_ORIGINS",
            ("http://127.0.0.1:5173", "http://localhost:5173"),
        )
    )
    api_token: str | None = field(default_factory=lambda: _optional_env("PROMTY_API_TOKEN"))
    allow_anonymous_ingest: bool = field(
        default_factory=lambda: _bool_env("PROMTY_ALLOW_ANONYMOUS_INGEST", False)
    )
    github_client_id: str | None = field(
        default_factory=lambda: _optional_env("PROMTY_GITHUB_CLIENT_ID")
    )
    github_client_secret: str | None = field(
        default_factory=lambda: _optional_env("PROMTY_GITHUB_CLIENT_SECRET")
    )
    github_token_encryption_key: str | None = field(
        default_factory=lambda: _optional_env("PROMTY_GITHUB_TOKEN_ENCRYPTION_KEY")
    )
    github_token_encryption_previous_keys: tuple[str, ...] = field(
        default_factory=lambda: _csv_env(
            "PROMTY_GITHUB_TOKEN_ENCRYPTION_PREVIOUS_KEYS",
            (),
        )
    )
    app_encryption_key: str | None = field(
        default_factory=lambda: _optional_env("PROMTY_APP_ENCRYPTION_KEY")
    )
    app_encryption_previous_keys: tuple[str, ...] = field(
        default_factory=lambda: _csv_env(
            "PROMTY_APP_ENCRYPTION_PREVIOUS_KEYS",
            (),
        )
    )
    app_encryption_key_id: str = os.environ.get("PROMTY_APP_ENCRYPTION_KEY_ID", "local")
    prompt_max_chars: int = field(
        default_factory=lambda: _int_env("PROMTY_PROMPT_MAX_CHARS", 50000)
    )
    response_max_chars: int = field(
        default_factory=lambda: _int_env("PROMTY_RESPONSE_MAX_CHARS", 50000)
    )
    event_batch_max_body_bytes: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_EVENT_BATCH_MAX_BODY_BYTES",
            8_388_608,
            minimum=1,
            maximum=8_388_608,
        )
    )
    gemini_api_key: str | None = field(
        default_factory=lambda: _optional_env_any(
            "PROMTY_GEMINI_API_KEY",
            "GEMINI_API_KEY",
        )
    )
    gemini_model: str = field(
        default_factory=lambda: _str_env_any(
            ("PROMTY_GEMINI_MODEL",),
            "gemini-2.5-flash",
        )
    )
    gemini_input_usd_per_million_tokens: float = field(
        default_factory=lambda: _float_env_any(
            (
                "PROMTY_GEMINI_INPUT_USD_PER_MILLION_TOKENS",
            ),
            0.30,
        )
    )
    gemini_output_usd_per_million_tokens: float = field(
        default_factory=lambda: _float_env_any(
            (
                "PROMTY_GEMINI_OUTPUT_USD_PER_MILLION_TOKENS",
            ),
            2.50,
        )
    )
    gemini_timeout_seconds: int = field(
        default_factory=lambda: _int_env_any(
            ("PROMTY_GEMINI_TIMEOUT_SECONDS",),
            30,
        )
    )
    openai_api_key: str | None = field(
        default_factory=lambda: _optional_env_any(
            "PROMTY_OPENAI_API_KEY",
            "OPENAI_API_KEY",
        )
    )
    openai_model: str = field(
        default_factory=lambda: _str_env_any(
            ("PROMTY_OPENAI_MODEL",),
            "gpt-5-mini",
        )
    )
    openai_input_usd_per_million_tokens: float = field(
        default_factory=lambda: _float_env_any(
            (
                "PROMTY_OPENAI_INPUT_USD_PER_MILLION_TOKENS",
            ),
            0.25,
        )
    )
    openai_output_usd_per_million_tokens: float = field(
        default_factory=lambda: _float_env_any(
            (
                "PROMTY_OPENAI_OUTPUT_USD_PER_MILLION_TOKENS",
            ),
            2.00,
        )
    )
    openai_timeout_seconds: int = field(
        default_factory=lambda: _int_env_any(
            ("PROMTY_OPENAI_TIMEOUT_SECONDS",),
            90,
        )
    )
    openai_reasoning_effort: str = field(
        default_factory=lambda: _str_env_any(
            ("PROMTY_OPENAI_REASONING_EFFORT",),
            "minimal",
        )
    )
    memory_provider_response_max_bytes: int = field(
        default_factory=lambda: max(
            1,
            _int_env_any(
                (
                    "PROMTY_MEMORY_PROVIDER_RESPONSE_MAX_BYTES",
                ),
                1_048_576,
            ),
        )
    )
    memory_provider_output_max_tokens: int = field(
        default_factory=lambda: max(
            1,
            _int_env_any(
                (
                    "PROMTY_MEMORY_PROVIDER_OUTPUT_MAX_TOKENS",
                ),
                8_192,
            ),
        )
    )
    memory_provider_wall_deadline_seconds: float = field(
        default_factory=lambda: max(
            0.001,
            _float_env_any(
                (
                    "PROMTY_MEMORY_PROVIDER_WALL_DEADLINE_SECONDS",
                ),
                120.0,
            ),
        )
    )
    memory_draft_generator: str = field(
        default_factory=lambda: _str_env_any(
            ("PROMTY_MEMORY_DRAFT_GENERATOR",),
            _str_env_any(("PROMTY_MEMORY_GENERATOR",), "openai"),
        )
    )
    project_memory_generator: str = field(
        default_factory=lambda: _str_env_any(
            ("PROMTY_PROJECT_MEMORY_GENERATOR",),
            _str_env_any(("PROMTY_MEMORY_GENERATOR",), "openai"),
        )
    )
    memory_slice_prompt_count: int = field(
        default_factory=lambda: _int_env_any(
            ("PROMTY_MEMORY_SLICE_PROMPT_COUNT",),
            20,
        )
    )
    memory_slice_event_max_rows: int = field(
        default_factory=lambda: max(
            2,
            _int_env_any(
                (
                    "PROMTY_MEMORY_SLICE_EVENT_MAX_ROWS",
                ),
                500,
            ),
        )
    )
    memory_slice_max_slices_per_call: int = field(
        default_factory=lambda: max(
            1,
            _int_env_any(
                (
                    "PROMTY_MEMORY_SLICE_MAX_SLICES_PER_CALL",
                ),
                4,
            ),
        )
    )
    memory_slice_max_minutes: int = field(
        default_factory=lambda: _int_env_any(
            ("PROMTY_MEMORY_SLICE_MAX_MINUTES",),
            120,
        )
    )
    memory_draft_prompt_max_bytes: int = field(
        default_factory=lambda: _int_env_any(
            (
                "PROMTY_MEMORY_DRAFT_PROMPT_MAX_BYTES",
            ),
            131_072,
        )
    )
    memory_draft_evidence_max_bytes: int = field(
        default_factory=lambda: _int_env_any(
            (
                "PROMTY_MEMORY_DRAFT_EVIDENCE_MAX_BYTES",
            ),
            98_304,
        )
    )
    project_memory_prompt_max_bytes: int = field(
        default_factory=lambda: _int_env_any(
            (
                "PROMTY_PROJECT_MEMORY_PROMPT_MAX_BYTES",
            ),
            262_144,
        )
    )
    project_memory_batch_max_drafts: int = field(
        default_factory=lambda: max(
            1,
            _int_env_any(
                (
                    "PROMTY_PROJECT_MEMORY_BATCH_MAX_DRAFTS",
                ),
                60,
            ),
        )
    )
    memory_worker_poll_seconds: float = field(
        default_factory=lambda: _float_env_any(
            (
                "PROMTY_MEMORY_WORKER_POLL_SECONDS",
            ),
            2.0,
        )
    )
    memory_worker_max_poll_seconds: float = field(
        default_factory=lambda: _float_env_any(
            (
                "PROMTY_MEMORY_WORKER_MAX_POLL_SECONDS",
            ),
            10.0,
        )
    )
    memory_worker_heartbeat_seconds: float = field(
        default_factory=lambda: _float_env_any(
            (
                "PROMTY_MEMORY_WORKER_HEARTBEAT_SECONDS",
            ),
            60.0,
        )
    )
    memory_worker_health_file: str = os.environ.get(
        "PROMTY_MEMORY_WORKER_HEALTH_FILE",
        "/tmp/promty-memory-worker.heartbeat",
    )
    memory_worker_health_timeout_seconds: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_MEMORY_WORKER_HEALTH_TIMEOUT_SECONDS",
            180,
            minimum=10,
        )
    )
    memory_worker_chunk_concurrency: int = field(
        default_factory=lambda: max(
            1,
            _int_env_any(
                (
                    "PROMTY_MEMORY_WORKER_CHUNK_CONCURRENCY",
                ),
                1,
            ),
        )
    )
    published_flows_enabled: bool = field(
        default_factory=lambda: _bool_env("PROMTY_PUBLISHED_FLOWS_ENABLED", False)
    )
    published_flow_asset_root: str = os.environ.get(
        "PROMTY_PUBLISHED_FLOW_ASSET_ROOT",
        "~/.promty/published-flow-assets",
    )
    published_flow_asset_storage: str = os.environ.get(
        "PROMTY_PUBLISHED_FLOW_ASSET_STORAGE",
        "local",
    )
    published_flow_asset_max_bytes: int = field(
        default_factory=lambda: _int_env("PROMTY_PUBLISHED_FLOW_ASSET_MAX_BYTES", 5_242_880)
    )
    published_flow_asset_max_count_per_flow: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_PUBLISHED_FLOW_ASSET_MAX_COUNT_PER_FLOW",
            20,
            minimum=1,
        )
    )
    published_flow_asset_max_total_bytes_per_user: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_PUBLISHED_FLOW_ASSET_MAX_TOTAL_BYTES_PER_USER",
            104_857_600,
            minimum=1,
        )
    )
    aws_region: str | None = field(default_factory=lambda: _optional_env("PROMTY_AWS_REGION"))
    aws_s3_bucket: str | None = field(
        default_factory=lambda: _optional_env("PROMTY_AWS_S3_BUCKET")
    )
    aws_s3_prefix: str = os.environ.get(
        "PROMTY_AWS_S3_PREFIX",
        "published-flow-assets",
    )
    aws_s3_endpoint_url: str | None = field(
        default_factory=lambda: _optional_env("PROMTY_AWS_S3_ENDPOINT_URL")
    )
    oauth_state_secret: str | None = field(
        default_factory=lambda: _optional_env("PROMTY_OAUTH_STATE_SECRET")
    )
    jwt_secret: str | None = field(default_factory=lambda: _optional_env("PROMTY_JWT_SECRET"))
    jwt_issuer: str = os.environ.get("PROMTY_JWT_ISSUER", "promty")
    jwt_audience: str = os.environ.get("PROMTY_JWT_AUDIENCE", "promty-web")
    access_token_ttl_seconds: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_ACCESS_TOKEN_TTL_SECONDS",
            3600,
            minimum=300,
            maximum=28_800,
        )
    )
    refresh_token_ttl_seconds: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_REFRESH_TOKEN_TTL_SECONDS",
            15_552_000,
            minimum=86_400,
            maximum=31_536_000,
        )
    )
    refresh_token_idle_ttl_seconds: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_REFRESH_TOKEN_IDLE_TTL_SECONDS",
            2_592_000,
            minimum=3600,
            maximum=15_552_000,
        )
    )
    refresh_token_rotation_grace_seconds: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_REFRESH_TOKEN_ROTATION_GRACE_SECONDS",
            30,
            minimum=5,
            maximum=300,
        )
    )
    session_cookie_name: str = os.environ.get("PROMTY_SESSION_COOKIE_NAME", "promty_session")
    refresh_cookie_name: str = os.environ.get(
        "PROMTY_REFRESH_COOKIE_NAME",
        "promty_refresh",
    )
    oauth_state_cookie_name: str = os.environ.get(
        "PROMTY_OAUTH_STATE_COOKIE_NAME",
        "promty_oauth_state",
    )
    session_cookie_secure: bool = field(
        default_factory=lambda: _bool_env("PROMTY_SESSION_COOKIE_SECURE", False)
    )
    session_cookie_samesite: str = os.environ.get("PROMTY_SESSION_COOKIE_SAMESITE", "lax")
    admin_github_ids: tuple[str, ...] = field(
        default_factory=lambda: _csv_env("PROMTY_ADMIN_GITHUB_IDS", ())
    )
    auth_rate_limit_requests: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_AUTH_RATE_LIMIT_REQUESTS",
            30,
            minimum=1,
        )
    )
    auth_rate_limit_window_seconds: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_AUTH_RATE_LIMIT_WINDOW_SECONDS",
            60,
            minimum=1,
        )
    )
    admin_rate_limit_requests: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_ADMIN_RATE_LIMIT_REQUESTS",
            120,
            minimum=1,
        )
    )
    admin_rate_limit_window_seconds: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_ADMIN_RATE_LIMIT_WINDOW_SECONDS",
            60,
            minimum=1,
        )
    )
    community_rate_limit_requests: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_COMMUNITY_RATE_LIMIT_REQUESTS",
            120,
            minimum=1,
        )
    )
    community_rate_limit_window_seconds: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_COMMUNITY_RATE_LIMIT_WINDOW_SECONDS",
            60,
            minimum=1,
        )
    )
    ingest_rate_limit_requests: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_INGEST_RATE_LIMIT_REQUESTS",
            120,
            minimum=1,
        )
    )
    ingest_rate_limit_window_seconds: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_INGEST_RATE_LIMIT_WINDOW_SECONDS",
            60,
            minimum=1,
        )
    )
    trusted_proxy_cidrs: tuple[str, ...] = field(
        default_factory=lambda: _csv_env(
            "PROMTY_TRUSTED_PROXY_CIDRS",
            ("127.0.0.0/8", "::1/128", "172.16.0.0/12"),
        )
    )
    support_rate_limit_requests: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_SUPPORT_RATE_LIMIT_REQUESTS",
            5,
            minimum=1,
        )
    )
    support_rate_limit_window_seconds: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_SUPPORT_RATE_LIMIT_WINDOW_SECONDS",
            300,
            minimum=1,
        )
    )
    support_email_provider: str = (
        os.environ.get(
            "PROMTY_SUPPORT_EMAIL_PROVIDER",
            "ses",
        )
        .strip()
        .lower()
    )
    support_notification_emails: tuple[str, ...] = field(
        default_factory=lambda: _csv_env("PROMTY_SUPPORT_NOTIFICATION_EMAILS", ())
    )
    support_from_email: str | None = field(
        default_factory=lambda: _optional_env("PROMTY_SUPPORT_FROM_EMAIL")
    )
    buffer_api_key: str | None = field(
        default_factory=lambda: _optional_env("PROMTY_BUFFER_API_KEY")
    )
    buffer_channel_ids_json: str = os.environ.get(
        "PROMTY_BUFFER_CHANNEL_IDS",
        "{}",
    )
    devto_api_key: str | None = field(
        default_factory=lambda: _optional_env("PROMTY_DEVTO_API_KEY")
    )
    devto_organization_id: int | None = field(
        default_factory=lambda: _int_env("PROMTY_DEVTO_ORGANIZATION_ID", 0) or None
    )
    github_marketing_token: str | None = field(
        default_factory=lambda: _optional_env("PROMTY_GITHUB_MARKETING_TOKEN")
    )
    github_marketing_repository_id: str | None = field(
        default_factory=lambda: _optional_env("PROMTY_GITHUB_MARKETING_REPOSITORY_ID")
    )
    github_marketing_discussion_category_id: str | None = field(
        default_factory=lambda: _optional_env("PROMTY_GITHUB_MARKETING_DISCUSSION_CATEGORY_ID")
    )
    admin_audit_retention_days: int = field(
        default_factory=lambda: _bounded_int_env(
            "PROMTY_ADMIN_AUDIT_RETENTION_DAYS",
            180,
            minimum=1,
        )
    )


settings = Settings()
