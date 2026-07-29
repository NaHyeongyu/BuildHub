from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from fastapi import HTTPException, status
from urllib3 import PoolManager
from urllib3.exceptions import HTTPError as Urllib3HTTPError
from urllib3.util import Retry, Timeout

from app.services.github_repository_metrics import (
    GithubRepositoryRequestAttempt,
    github_operation,
)

GITHUB_API_URL = "https://api.github.com"
_GITHUB_HTTP = PoolManager(
    maxsize=10,
    num_pools=4,
    timeout=Timeout(connect=3.0, read=10.0),
    retries=Retry(
        connect=0,
        read=0,
        redirect=3,
        status=0,
        other=0,
    ),
)


@dataclass(frozen=True)
class GithubJsonResponse:
    etag: str | None
    not_modified: bool
    payload: Any | None
    status_code: int


def _github_http_error(status_code: int) -> HTTPException:
    detail = "GitHub repository request failed"
    if status_code == 401:
        detail = "GitHub repository access token is invalid"
    elif status_code == 403:
        detail = "GitHub repository access is forbidden"
    elif status_code == 404:
        detail = "GitHub repository was not found or is not accessible"
    return HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"{detail}: HTTP {status_code}",
    )


def _response_etag(response: object) -> str | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get("ETag")
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or len(value) > 256 or "\r" in value or "\n" in value:
        return None
    return value


def github_request_json_with_etag(
    path: str,
    *,
    if_none_match: str | None,
    token: str,
) -> GithubJsonResponse:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "Promty",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if if_none_match:
        headers["If-None-Match"] = if_none_match
    metrics = GithubRepositoryRequestAttempt(
        conditional=if_none_match is not None,
        operation=github_operation(path),
    )
    try:
        response = _GITHUB_HTTP.request(
            "GET",
            f"{GITHUB_API_URL}{path}",
            headers=headers,
        )
    except Urllib3HTTPError as exc:
        metrics.finish(
            etag_received=False,
            outcome="failure",
            response_bytes=0,
            status="transport_error",
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub repository request failed",
        ) from exc

    response_bytes = len(response.data)
    etag = _response_etag(response)
    if response.status == status.HTTP_304_NOT_MODIFIED:
        metrics.finish(
            etag_received=etag is not None,
            outcome="not_modified",
            response_bytes=response_bytes,
            status=response.status,
        )
        return GithubJsonResponse(
            etag=etag or if_none_match,
            not_modified=True,
            payload=None,
            status_code=response.status,
        )
    if response.status >= 400:
        metrics.finish(
            etag_received=etag is not None,
            outcome="failure",
            response_bytes=response_bytes,
            status=response.status,
        )
        raise _github_http_error(response.status)

    try:
        payload = json.loads(response.data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        metrics.finish(
            etag_received=etag is not None,
            outcome="invalid_response",
            response_bytes=response_bytes,
            status=response.status,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub repository request failed",
        ) from exc

    metrics.finish(
        etag_received=etag is not None,
        outcome="success",
        response_bytes=response_bytes,
        status=response.status,
    )
    return GithubJsonResponse(
        etag=etag,
        not_modified=False,
        payload=payload,
        status_code=response.status,
    )


def github_request_json(path: str, *, token: str) -> Any:
    response = github_request_json_with_etag(
        path,
        if_none_match=None,
        token=token,
    )
    return response.payload


def github_request_with_etag(
    path: str,
    *,
    if_none_match: str | None,
    token: str,
) -> GithubJsonResponse:
    response = github_request_json_with_etag(
        path,
        if_none_match=if_none_match,
        token=token,
    )
    if response.not_modified:
        return response
    if not isinstance(response.payload, dict):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub repository response was invalid",
        )
    return response


def github_request(path: str, *, token: str) -> dict[str, Any]:
    payload = github_request_json(path, token=token)
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub repository response was invalid",
        )
    return payload


def github_list_request(path: str, *, token: str) -> list[dict[str, Any]]:
    payload = github_request_json(path, token=token)
    if not isinstance(payload, list):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="GitHub repository list response was invalid",
        )
    return [item for item in payload if isinstance(item, dict)]
