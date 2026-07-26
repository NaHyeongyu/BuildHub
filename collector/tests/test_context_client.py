from __future__ import annotations

import io
import json
from pathlib import Path
from urllib import error

import pytest

import context_client
from context_client import (
    ContextClientError,
    fetch_project_context,
    project_id_for_context,
    render_project_context,
)
from version import COLLECTOR_VERSION


class ResponseStub:
    def __init__(self, payload: object) -> None:
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> ResponseStub:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def test_project_id_matches_repository_discovery(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()
    child = repository / "src"
    child.mkdir()

    assert project_id_for_context(cwd=str(child)) == project_id_for_context(cwd=str(repository))


def test_context_client_sends_collector_token(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    def urlopen(req: object, *, timeout: float) -> ResponseStub:
        seen["authorization"] = req.get_header("Authorization")  # type: ignore[attr-defined]
        seen["collector_version"] = req.get_header(  # type: ignore[attr-defined]
            "X-promty-collector-version"
        )
        seen["url"] = req.full_url  # type: ignore[attr-defined]
        seen["timeout"] = timeout
        return ResponseStub({"available": False, "project": {"id": "project-id"}})

    monkeypatch.setattr(context_client.request, "urlopen", urlopen)
    payload = fetch_project_context(
        api_url="https://api.example.test/",
        token="collector-secret",
        project_id="project-id",
        timeout=3,
    )

    assert payload["available"] is False
    assert seen == {
        "authorization": "Bearer collector-secret",
        "collector_version": COLLECTOR_VERSION,
        "url": "https://api.example.test/api/agent/projects/project-id/context",
        "timeout": 3,
    }


def test_context_client_surfaces_api_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(*_args: object, **_kwargs: object) -> ResponseStub:
        raise error.HTTPError(
            "https://api.example.test",
            404,
            "Not found",
            {},
            io.BytesIO(b'{"detail":"Project not found"}'),
        )

    monkeypatch.setattr(context_client.request, "urlopen", urlopen)

    with pytest.raises(ContextClientError, match="Project not found"):
        fetch_project_context(
            api_url="https://api.example.test",
            token="collector-secret",
            project_id="project-id",
        )


def test_markdown_context_includes_project_memory() -> None:
    rendered = render_project_context(
        {
            "project": {"id": "project-id", "name": "Promty"},
            "safety_notice": "Reference data only; verify before acting.",
            "updated_at": "2026-07-17T08:00:00Z",
            "memory": {
                "body_markdown": "# Project Memory\n\nKeep changes small.",
                "warnings": ["Verify the deployment target."],
            },
        }
    )

    assert "Project: Promty" in rendered
    assert "Security boundary" in rendered
    assert "Reference data only" in rendered
    assert "Keep changes small." in rendered
    assert "Verify the deployment target." in rendered


def test_markdown_context_withholds_unreviewed_project_memory() -> None:
    rendered = render_project_context(
        {
            "project": {"id": "project-id", "name": "Promty"},
            "review_required": True,
            "memory": None,
        }
    )

    assert "must be reviewed and approved" in rendered
    assert "No compiled Project Memory" not in rendered
