from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.models.projects import Project
from app.services import github_repositories
from app.services.github_repository_client import GithubJsonResponse


class _RecordingDB:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events if events is not None else []
        self.statements: list[Any] = []

    def rollback(self) -> None:
        self.events.append("rollback")

    def execute(self, statement: Any) -> None:
        self.events.append("execute")
        self.statements.append(statement)

    def commit(self) -> None:
        self.events.append("commit")


def _project(*, branch: str = "release/v2") -> Project:
    return Project(
        id=uuid4(),
        owner_id=uuid4(),
        name="Demo",
        slug="demo",
        description=None,
        git_remote="https://github.com/acme/demo",
        default_branch=branch,
    )


def _install_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = SimpleNamespace(access_token_encrypted="encrypted-token")
    monkeypatch.setattr(
        github_repositories,
        "_connection_for_user",
        lambda _db, _user: connection,
    )
    monkeypatch.setattr(
        github_repositories,
        "decrypt_github_token_with_rotation",
        lambda encrypted: (
            ("decrypted-token", False) if encrypted == "encrypted-token" else ("", False)
        ),
    )


def _github_response(
    payload: dict[str, Any] | None,
    *,
    etag: str | None = '"tree-etag"',
    not_modified: bool = False,
) -> GithubJsonResponse:
    return GithubJsonResponse(
        etag=etag,
        not_modified=not_modified,
        payload=payload,
        status_code=304 if not_modified else 200,
    )


def test_tree_uses_stored_branch_with_one_github_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_connection(monkeypatch)
    events: list[str] = []
    db = _RecordingDB(events)
    calls: list[tuple[str, str]] = []

    def request(
        path: str,
        *,
        if_none_match: str | None,
        token: str,
    ) -> GithubJsonResponse:
        assert events == ["rollback"]
        events.append("remote")
        calls.append((path, token))
        assert if_none_match is None
        return _github_response(
            {
                "tree": [
                    {"path": "src", "type": "tree"},
                    {"path": "src/main.py", "type": "blob"},
                ],
                "truncated": False,
            }
        )

    monkeypatch.setattr(github_repositories, "github_request_with_etag", request)

    response = github_repositories.read_github_repository_tree(
        db,  # type: ignore[arg-type]
        project=_project(),
        user=object(),  # type: ignore[arg-type]
    )

    assert calls == [("/repos/acme/demo/git/trees/release%2Fv2?recursive=1", "decrypted-token")]
    assert events == ["rollback", "remote"]
    assert response["default_branch"] == "release/v2"
    assert response["files"][0]["children"] == [
        {"name": "main.py", "path": "src/main.py", "type": "file"}
    ]


def test_file_uses_stored_branch_with_one_github_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_connection(monkeypatch)
    events: list[str] = []
    db = _RecordingDB(events)
    calls: list[tuple[str, str]] = []

    def request(path: str, *, token: str) -> dict[str, Any]:
        assert events == ["rollback"]
        events.append("remote")
        calls.append((path, token))
        return {
            "content": base64.b64encode(b"print('hello')\n").decode("ascii"),
            "encoding": "base64",
            "html_url": "https://github.com/acme/demo/blob/release/v2/src/main.py",
            "name": "main.py",
            "size": 15,
            "type": "file",
        }

    monkeypatch.setattr(github_repositories, "github_request", request)

    response = github_repositories.read_github_repository_file_content(
        db,  # type: ignore[arg-type]
        path="src/main.py",
        project=_project(),
        user=object(),  # type: ignore[arg-type]
    )

    assert calls == [
        (
            "/repos/acme/demo/contents/src/main.py?ref=release%2Fv2",
            "decrypted-token",
        )
    ]
    assert events == ["rollback", "remote"]
    assert response["branch"] == "release/v2"
    assert response["content"] == "print('hello')\n"


def test_empty_stored_branch_falls_back_to_main(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_connection(monkeypatch)
    db = _RecordingDB()
    paths: list[str] = []

    def request(
        path: str,
        *,
        if_none_match: str | None,
        token: str,
    ) -> GithubJsonResponse:
        paths.append(path)
        assert if_none_match is None
        return _github_response({"tree": [], "truncated": False})

    monkeypatch.setattr(github_repositories, "github_request_with_etag", request)

    response = github_repositories.read_github_repository_tree(
        db,  # type: ignore[arg-type]
        project=_project(branch="   "),
        user=object(),  # type: ignore[arg-type]
    )

    assert paths == ["/repos/acme/demo/git/trees/main?recursive=1"]
    assert response["default_branch"] == "main"


def test_repository_list_releases_transaction_before_remote_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_connection(monkeypatch)
    events: list[str] = []
    db = _RecordingDB(events)

    def request(path: str, *, token: str) -> list[dict[str, Any]]:
        assert events == ["rollback"]
        events.append("remote")
        assert path == "/user/repos?type=all&sort=updated&per_page=100"
        assert token == "decrypted-token"
        return [
            {
                "default_branch": "main",
                "full_name": "acme/demo",
                "html_url": "https://github.com/acme/demo",
                "name": "demo",
                "owner": {"login": "acme"},
                "private": False,
            }
        ]

    monkeypatch.setattr(github_repositories, "github_list_request", request)

    response = github_repositories.list_github_repositories(
        db,  # type: ignore[arg-type]
        user=object(),  # type: ignore[arg-type]
    )

    assert response["status"] == "ok"
    assert response["repositories"][0]["full_name"] == "acme/demo"
    assert events == ["rollback", "remote"]


def test_repository_metadata_releases_transaction_before_remote_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_connection(monkeypatch)
    events: list[str] = []
    db = _RecordingDB(events)

    def request(path: str, *, token: str) -> dict[str, Any]:
        assert events == ["rollback"]
        events.append("remote")
        assert path == "/repos/acme/demo"
        assert token == "decrypted-token"
        return {
            "default_branch": "trunk",
            "description": "Demo repository",
            "full_name": "acme/demo",
            "html_url": "https://github.com/acme/demo",
            "name": "demo",
            "owner": {"login": "acme"},
            "private": True,
        }

    monkeypatch.setattr(github_repositories, "github_request", request)

    response = github_repositories.repository_metadata_from_url(
        db,  # type: ignore[arg-type]
        remote_url="https://github.com/acme/demo",
        user=object(),  # type: ignore[arg-type]
    )

    assert response["default_branch"] == "trunk"
    assert response["private"] is True
    assert events == ["rollback", "remote"]


def test_missing_connection_keeps_existing_early_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        github_repositories,
        "_connection_for_user",
        lambda _db, _user: None,
    )
    db = _RecordingDB()

    response = github_repositories.list_github_repositories(
        db,  # type: ignore[arg-type]
        user=object(),  # type: ignore[arg-type]
    )

    assert response == {
        "available": False,
        "message": "Sign in again with GitHub repository access to choose repositories.",
        "repositories": [],
        "status": "github_repository_access_required",
    }
    assert db.events == []


def test_tree_refreshes_stale_default_branch_and_persists_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_connection(monkeypatch)
    events: list[str] = []
    db = _RecordingDB(events)
    project = _project(branch="master")

    def tree_request(
        path: str,
        *,
        if_none_match: str | None,
        token: str,
    ) -> GithubJsonResponse:
        assert token == "decrypted-token"
        assert if_none_match is None
        events.append(f"remote:{path}")
        if path.endswith("/git/trees/master?recursive=1"):
            raise HTTPException(status_code=502, detail="GitHub not found: HTTP 404")
        assert path.endswith("/git/trees/main?recursive=1")
        return _github_response(
            {
                "tree": [{"path": "README.md", "type": "blob"}],
                "truncated": False,
            }
        )

    def request(path: str, *, token: str) -> dict[str, Any]:
        assert token == "decrypted-token"
        events.append(f"remote:{path}")
        assert path == "/repos/acme/demo"
        return {"default_branch": "main"}

    monkeypatch.setattr(github_repositories, "github_request", request)
    monkeypatch.setattr(github_repositories, "github_request_with_etag", tree_request)

    response = github_repositories.read_github_repository_tree(
        db,  # type: ignore[arg-type]
        project=project,
        user=object(),  # type: ignore[arg-type]
    )

    assert response["default_branch"] == "main"
    assert response["files"] == [{"name": "README.md", "path": "README.md", "type": "file"}]
    assert events == [
        "rollback",
        "remote:/repos/acme/demo/git/trees/master?recursive=1",
        "remote:/repos/acme/demo",
        "remote:/repos/acme/demo/git/trees/main?recursive=1",
        "execute",
        "commit",
    ]
    assert len(db.statements) == 1
    values = set(db.statements[0].compile().params.values())
    assert {project.id, "master", "main"}.issubset(values)


def test_file_refreshes_stale_default_branch_and_persists_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_connection(monkeypatch)
    events: list[str] = []
    db = _RecordingDB(events)
    project = _project(branch="master")

    def request(path: str, *, token: str) -> dict[str, Any]:
        assert token == "decrypted-token"
        events.append(f"remote:{path}")
        if path.endswith("?ref=master"):
            raise HTTPException(status_code=502, detail="GitHub not found: HTTP 404")
        if path == "/repos/acme/demo":
            return {"default_branch": "main"}
        assert path.endswith("?ref=main")
        return {
            "content": base64.b64encode(b"updated\n").decode("ascii"),
            "encoding": "base64",
            "name": "main.py",
            "size": 8,
            "type": "file",
        }

    monkeypatch.setattr(github_repositories, "github_request", request)

    response = github_repositories.read_github_repository_file_content(
        db,  # type: ignore[arg-type]
        path="src/main.py",
        project=project,
        user=object(),  # type: ignore[arg-type]
    )

    assert response["branch"] == "main"
    assert response["content"] == "updated\n"
    assert events == [
        "rollback",
        "remote:/repos/acme/demo/contents/src/main.py?ref=master",
        "remote:/repos/acme/demo",
        "remote:/repos/acme/demo/contents/src/main.py?ref=main",
        "execute",
        "commit",
    ]
    assert len(db.statements) == 1


def test_non_404_tree_error_does_not_refresh_branch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_connection(monkeypatch)
    db = _RecordingDB()
    paths: list[str] = []

    def request(
        path: str,
        *,
        if_none_match: str | None,
        token: str,
    ) -> GithubJsonResponse:
        paths.append(path)
        assert if_none_match is None
        raise HTTPException(status_code=502, detail="GitHub request failed: HTTP 500")

    monkeypatch.setattr(github_repositories, "github_request_with_etag", request)

    with pytest.raises(HTTPException) as exc_info:
        github_repositories.read_github_repository_tree(
            db,  # type: ignore[arg-type]
            project=_project(),
            user=object(),  # type: ignore[arg-type]
        )

    assert exc_info.value.detail == "GitHub request failed: HTTP 500"
    assert paths == ["/repos/acme/demo/git/trees/release%2Fv2?recursive=1"]
    assert db.events == ["rollback"]


def test_tree_forwards_etag_and_returns_not_modified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_connection(monkeypatch)
    db = _RecordingDB()
    calls: list[tuple[str, str | None]] = []

    def request(
        path: str,
        *,
        if_none_match: str | None,
        token: str,
    ) -> GithubJsonResponse:
        assert token == "decrypted-token"
        calls.append((path, if_none_match))
        return _github_response(
            None,
            etag='"tree-v1"',
            not_modified=True,
        )

    monkeypatch.setattr(github_repositories, "github_request_with_etag", request)
    tree_key = github_repositories._repository_tree_key(
        branch="release/v2",
        owner="acme",
        repo="demo",
    )

    response = github_repositories.read_github_repository_tree_with_etag(
        db,  # type: ignore[arg-type]
        if_none_match='"tree-v1"',
        project=_project(),
        tree_key=tree_key,
        user=object(),  # type: ignore[arg-type]
    )

    assert response.not_modified is True
    assert response.payload is None
    assert response.etag == '"tree-v1"'
    assert response.tree_key == tree_key
    assert calls == [
        (
            "/repos/acme/demo/git/trees/release%2Fv2?recursive=1",
            '"tree-v1"',
        )
    ]


def test_tree_does_not_forward_etag_for_a_different_repository_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_connection(monkeypatch)
    db = _RecordingDB()
    forwarded_etags: list[str | None] = []

    def request(
        path: str,
        *,
        if_none_match: str | None,
        token: str,
    ) -> GithubJsonResponse:
        assert path.endswith("/git/trees/release%2Fv2?recursive=1")
        assert token == "decrypted-token"
        forwarded_etags.append(if_none_match)
        return _github_response({"tree": [], "truncated": False})

    monkeypatch.setattr(github_repositories, "github_request_with_etag", request)

    response = github_repositories.read_github_repository_tree_with_etag(
        db,  # type: ignore[arg-type]
        if_none_match='"tree-from-another-repository"',
        project=_project(),
        tree_key="different-repository-key",
        user=object(),  # type: ignore[arg-type]
    )

    assert forwarded_etags == [None]
    assert response.not_modified is False
    assert response.payload is not None
