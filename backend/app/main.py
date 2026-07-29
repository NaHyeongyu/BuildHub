import logging

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.account import router as account_router
from app.api.agent_context import router as agent_context_router
from app.api.admin import router as admin_router
from app.api.admin_marketing import router as admin_marketing_router
from app.api.auth import router as auth_router
from app.api.events import router as events_router
from app.api.memory import router as memory_router
from app.api.projects import router as projects_router
from app.api.published_flows import router as published_flows_router
from app.api.support import router as support_router
from app.core.access_logging import install_sensitive_access_log_filter
from app.core.config import settings
from app.core.encryption import EncryptionError
from app.core.text_limits import PROJECT_MEMORY_UPDATE_REQUEST_MAX_BYTES
from app.db.session import engine
from app.middleware.request_body_limit import (
    EventBatchBodyLimitMiddleware,
    ProjectMemoryBodyLimitMiddleware,
)
from app.middleware.admin_audit import AdminAuditMiddleware
from app.middleware.security_headers import APISecurityHeadersMiddleware
from app.middleware.security_rate_limit import SecurityRateLimitMiddleware

install_sensitive_access_log_filter()

logger = logging.getLogger("promty.encryption")
ENCRYPTED_DATA_UNAVAILABLE_CODE = "encrypted_data_unavailable"
ENCRYPTED_DATA_UNAVAILABLE_DETAIL = (
    "Some activity data is temporarily unavailable. Please try again shortly."
)

app = FastAPI(title="Promty API")
app.add_middleware(
    ProjectMemoryBodyLimitMiddleware,
    max_body_bytes=PROJECT_MEMORY_UPDATE_REQUEST_MAX_BYTES,
)
app.add_middleware(
    EventBatchBodyLimitMiddleware,
    max_body_bytes=settings.event_batch_max_body_bytes,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_methods=["DELETE", "GET", "PATCH", "POST", "PUT", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "If-None-Match",
        "X-Promty-Repository-Tree-Key",
    ],
    expose_headers=["ETag", "X-Promty-Repository-Tree-Key"],
    allow_credentials=True,
)
app.add_middleware(
    SecurityRateLimitMiddleware,
    admin_requests=settings.admin_rate_limit_requests,
    admin_window_seconds=settings.admin_rate_limit_window_seconds,
    auth_requests=settings.auth_rate_limit_requests,
    auth_window_seconds=settings.auth_rate_limit_window_seconds,
    community_requests=settings.community_rate_limit_requests,
    community_window_seconds=settings.community_rate_limit_window_seconds,
    ingest_requests=settings.ingest_rate_limit_requests,
    ingest_window_seconds=settings.ingest_rate_limit_window_seconds,
    support_requests=settings.support_rate_limit_requests,
    support_window_seconds=settings.support_rate_limit_window_seconds,
    trusted_proxy_cidrs=settings.trusted_proxy_cidrs,
)
app.add_middleware(AdminAuditMiddleware)
app.add_middleware(APISecurityHeadersMiddleware)
app.include_router(auth_router)
app.include_router(account_router)
app.include_router(agent_context_router)
app.include_router(admin_router)
app.include_router(admin_marketing_router)
app.include_router(events_router)
app.include_router(memory_router)
if settings.published_flows_enabled:
    app.include_router(published_flows_router)
app.include_router(projects_router)
app.include_router(support_router)


@app.exception_handler(EncryptionError)
async def encryption_error_handler(
    request: Request,
    exc: EncryptionError,
) -> JSONResponse:
    logger.error(
        "Encryption operation failed for %s %s",
        request.method,
        request.url.path,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "code": ENCRYPTED_DATA_UNAVAILABLE_CODE,
            "detail": ENCRYPTED_DATA_UNAVAILABLE_DETAIL,
        },
    )


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/live")
def liveness_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", response_model=None)
def readiness_check() -> dict[str, str] | JSONResponse:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "unavailable", "database": "unavailable"},
        )
    return {"status": "ok", "database": "ok"}
