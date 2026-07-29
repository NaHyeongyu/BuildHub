from __future__ import annotations

import logging

import pytest
from fastapi import HTTPException
from urllib3.exceptions import HTTPError as Urllib3HTTPError

from app.services import github_repository_client
from app.services.github_repository_metrics import logger as metrics_logger


class _Response:
    def __init__(
        self,
        *,
        data: bytes = b"{}",
        headers: dict[str, str] | None = None,
        status: int = 200,
    ) -> None:
        self.data = data
        self.headers = headers or {}
        self.status = status


class _Pool:
    def __init__(self, responses: list[_Response | Exception]) -> None:
        self.calls: list[tuple[str, str, dict[str, str]]] = []
        self.responses = iter(responses)

    def request(self, method: str, url: str, *, headers: dict[str, str]) -> _Response:
        self.calls.append((method, url, headers))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def test_github_requests_share_the_module_connection_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _Pool([_Response(data=b'{"id": 1}'), _Response(data=b"[]")])
    monkeypatch.setattr(github_repository_client, "_GITHUB_HTTP", pool)

    assert github_repository_client.github_request("/repos/acme/demo", token="secret") == {"id": 1}
    assert github_repository_client.github_list_request("/user/repos", token="secret") == []

    assert [call[:2] for call in pool.calls] == [
        ("GET", "https://api.github.com/repos/acme/demo"),
        ("GET", "https://api.github.com/user/repos"),
    ]
    assert all(call[2]["Authorization"] == "Bearer secret" for call in pool.calls)


def test_conditional_request_forwards_etag_and_accepts_not_modified(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _Pool([_Response(data=b"", status=304)])
    monkeypatch.setattr(github_repository_client, "_GITHUB_HTTP", pool)
    caplog.set_level(logging.INFO, logger=metrics_logger.name)

    response = github_repository_client.github_request_with_etag(
        "/repos/acme/demo/git/trees/main?recursive=1",
        if_none_match='"tree-v1"',
        token="secret",
    )

    assert response.not_modified is True
    assert response.payload is None
    assert response.etag == '"tree-v1"'
    assert pool.calls[0][2]["If-None-Match"] == '"tree-v1"'
    message = next(
        record.getMessage()
        for record in caplog.records
        if record.name == metrics_logger.name
    )
    assert "conditional=true" in message
    assert "outcome=not_modified" in message
    assert "status=304" in message


def test_github_request_logs_low_cardinality_call_metrics(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _Pool([
        _Response(
            data=b'{"tree": []}',
            headers={"ETag": '"tree-v2"'},
        )
    ])
    monkeypatch.setattr(github_repository_client, "_GITHUB_HTTP", pool)
    caplog.set_level(logging.INFO, logger=metrics_logger.name)

    response = github_repository_client.github_request_with_etag(
        "/repos/acme/demo/git/trees/main?recursive=1",
        if_none_match=None,
        token="secret",
    )

    assert response.etag == '"tree-v2"'
    records = [record for record in caplog.records if record.name == metrics_logger.name]
    assert len(records) == 1
    message = records[0].getMessage()
    assert "github_operation=repository_tree" in message
    assert "conditional=false" in message
    assert "outcome=success" in message
    assert "status=200" in message
    assert "etag_received=true" in message
    assert "acme/demo" not in message
    assert "secret" not in message


@pytest.mark.parametrize(
    ("github_status", "detail"),
    [
        (401, "access token is invalid"),
        (403, "access is forbidden"),
        (404, "was not found or is not accessible"),
        (429, "request failed"),
    ],
)
def test_github_http_errors_preserve_public_error_mapping(
    monkeypatch: pytest.MonkeyPatch,
    github_status: int,
    detail: str,
) -> None:
    monkeypatch.setattr(
        github_repository_client,
        "_GITHUB_HTTP",
        _Pool([_Response(status=github_status)]),
    )

    with pytest.raises(HTTPException) as exc_info:
        github_repository_client.github_request_json("/repos/acme/demo", token="secret")

    assert exc_info.value.status_code == 502
    assert detail in str(exc_info.value.detail)
    assert f"HTTP {github_status}" in str(exc_info.value.detail)


@pytest.mark.parametrize(
    "response",
    [_Response(data=b"not-json"), Urllib3HTTPError("connection failed")],
)
def test_github_transport_and_json_errors_are_reported_as_bad_gateway(
    monkeypatch: pytest.MonkeyPatch,
    response: _Response | Exception,
) -> None:
    monkeypatch.setattr(github_repository_client, "_GITHUB_HTTP", _Pool([response]))

    with pytest.raises(HTTPException) as exc_info:
        github_repository_client.github_request_json("/repos/acme/demo", token="secret")

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "GitHub repository request failed"


def test_github_request_rejects_non_object_response(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _Pool([_Response(data=b"[]")])
    monkeypatch.setattr(github_repository_client, "_GITHUB_HTTP", pool)

    with pytest.raises(HTTPException) as exc_info:
        github_repository_client.github_request("/repos/acme/demo", token="secret")

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "GitHub repository response was invalid"
