from __future__ import annotations

from io import BytesIO
from typing import Any
from urllib import error

import pytest

from uploader import client
from uploader.client import EventBatchConflict, PromtyUploader
from version import COLLECTOR_VERSION


class ResponseStub:
    def __enter__(self) -> "ResponseStub":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return b'{"status":"ok"}'


def test_heartbeat_sends_authentication_and_collector_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def urlopen(req: object, *, timeout: float) -> ResponseStub:
        seen["authorization"] = req.get_header("Authorization")  # type: ignore[attr-defined]
        seen["body"] = req.data  # type: ignore[attr-defined]
        seen["method"] = req.method  # type: ignore[attr-defined]
        seen["timeout"] = timeout
        seen["url"] = req.full_url  # type: ignore[attr-defined]
        seen["version"] = req.get_header("X-promty-collector-version")  # type: ignore[attr-defined]
        return ResponseStub()

    monkeypatch.setattr(client.request, "urlopen", urlopen)

    PromtyUploader(
        "https://api.example.test/",
        token="collector-secret",
        timeout=3,
    ).heartbeat()

    assert seen == {
        "authorization": "Bearer collector-secret",
        "body": b"{}",
        "method": "POST",
        "timeout": 3,
        "url": "https://api.example.test/api/events/heartbeat",
        "version": COLLECTOR_VERSION,
    }


def test_upload_events_exposes_conflict_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def urlopen(_req: object, *, timeout: float) -> ResponseStub:
        assert timeout == 10
        raise error.HTTPError(
            "https://api.example.test/api/events/batch",
            409,
            "Conflict",
            {},
            BytesIO(b'{"detail":"Event sequence already exists"}'),
        )

    monkeypatch.setattr(client.request, "urlopen", urlopen)

    with pytest.raises(EventBatchConflict) as exc_info:
        PromtyUploader("https://api.example.test").upload_events(
            [{"id": "event-1"}]
        )

    assert exc_info.value.detail == "Event sequence already exists"
