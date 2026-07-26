import logging

from app.core.access_logging import (
    SensitiveQueryFilter,
    install_sensitive_access_log_filter,
)


def _access_record(target: str) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1234", "GET", target, "1.1", 302),
        None,
    )


def test_sensitive_query_filter_redacts_github_callback_query() -> None:
    record = _access_record(
        "/api/auth/github/callback?code=secret-code&state=secret-state"
    )

    assert SensitiveQueryFilter().filter(record) is True

    assert record.args[2] == "/api/auth/github/callback?[REDACTED]"
    assert "secret-code" not in record.getMessage()
    assert "secret-state" not in record.getMessage()


def test_sensitive_query_filter_redacts_project_context_queries() -> None:
    targets = (
        "/api/projects/123/prompt-activities?q=private+architecture",
        "/api/projects/123/prompt-activities/?q=private+architecture",
    )

    for target in targets:
        record = _access_record(target)

        assert SensitiveQueryFilter().filter(record) is True
        assert record.args[2].endswith("?[REDACTED]")
        assert "private+architecture" not in record.getMessage()


def test_sensitive_query_filter_leaves_regular_query_unchanged() -> None:
    target = "/api/projects?limit=20"
    record = _access_record(target)

    SensitiveQueryFilter().filter(record)

    assert record.args[2] == target


def test_install_sensitive_access_log_filter_is_idempotent() -> None:
    logger = logging.getLogger("uvicorn.access")
    original_filters = list(logger.filters)
    try:
        logger.filters = [
            item for item in logger.filters if not isinstance(item, SensitiveQueryFilter)
        ]
        install_sensitive_access_log_filter()
        install_sensitive_access_log_filter()

        assert sum(
            isinstance(item, SensitiveQueryFilter) for item in logger.filters
        ) == 1
    finally:
        logger.filters = original_filters
