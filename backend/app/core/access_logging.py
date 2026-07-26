import logging
from urllib.parse import urlsplit


_SENSITIVE_QUERY_PATHS = frozenset(
    {
        "/api/auth/github/callback",
        "/api/auth/github/web/callback",
        "/api/auth/github/web/repository/callback",
    }
)

_SENSITIVE_PROJECT_QUERY_SUFFIXES = ("/prompt-activities",)


def _is_sensitive_query_path(path: str) -> bool:
    normalized_path = path.rstrip("/") or "/"
    if normalized_path in _SENSITIVE_QUERY_PATHS:
        return True
    if normalized_path.startswith("/api/projects/") and normalized_path.endswith(
        _SENSITIVE_PROJECT_QUERY_SUFFIXES
    ):
        return True
    return False


def _redact_sensitive_query(target: object) -> object:
    if not isinstance(target, str):
        return target

    parsed = urlsplit(target)
    if not _is_sensitive_query_path(parsed.path) or not parsed.query:
        return target
    return f"{parsed.path}?[REDACTED]"


class SensitiveQueryFilter(logging.Filter):
    """Remove OAuth credentials from Uvicorn's formatted access-log arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            args = list(record.args)
            args[2] = _redact_sensitive_query(args[2])
            record.args = tuple(args)
        return True


def install_sensitive_access_log_filter() -> None:
    logger = logging.getLogger("uvicorn.access")
    if any(isinstance(item, SensitiveQueryFilter) for item in logger.filters):
        return
    logger.addFilter(SensitiveQueryFilter())
