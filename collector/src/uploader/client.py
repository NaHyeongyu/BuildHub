from __future__ import annotations

import json
from typing import Any
from urllib import error, request

from version import COLLECTOR_VERSION


class EventBatchConflict(RuntimeError):
    """A permanent server-side conflict for one or more queued events."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _http_error_detail(exc: error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "Event batch conflicts with activity already stored by Promty."
    detail = payload.get("detail") if isinstance(payload, dict) else None
    return (
        detail
        if isinstance(detail, str) and detail.strip()
        else "Event batch conflicts with activity already stored by Promty."
    )


class PromtyUploader:
    def __init__(self, api_url: str, token: str | None = None, timeout: float = 10) -> None:
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def upload_events(self, events: list[dict[str, Any]]) -> list[str]:
        if not events:
            return []

        body = json.dumps({"events": events}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Promty-Collector-Version": COLLECTOR_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        req = request.Request(
            f"{self.api_url}/api/events/batch",
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            if exc.code == 409:
                raise EventBatchConflict(_http_error_detail(exc)) from exc
            raise

        event_ids = payload.get("event_ids")
        if isinstance(event_ids, list):
            return [event_id for event_id in event_ids if isinstance(event_id, str)]
        return [event["id"] for event in events if isinstance(event.get("id"), str)]

    def heartbeat(self) -> None:
        headers = {
            "Content-Type": "application/json",
            "X-Promty-Collector-Version": COLLECTOR_VERSION,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        req = request.Request(
            f"{self.api_url}/api/events/heartbeat",
            data=b"{}",
            headers=headers,
            method="POST",
        )
        with request.urlopen(req, timeout=self.timeout) as response:
            response.read()
