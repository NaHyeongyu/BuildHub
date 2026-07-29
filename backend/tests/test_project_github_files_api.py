from __future__ import annotations

from uuid import uuid4

from fastapi import Response

from app.api import projects as projects_api
from app.services.github_repositories import GithubRepositoryTreeResult


def test_project_github_tree_returns_validator_headers_on_not_modified(
    monkeypatch,
) -> None:
    project = object()
    user = object()
    db = object()
    monkeypatch.setattr(
        projects_api,
        "_project_for_user",
        lambda *_args, **_kwargs: project,
    )
    monkeypatch.setattr(
        projects_api,
        "read_github_repository_tree_with_etag",
        lambda *_args, **_kwargs: GithubRepositoryTreeResult(
            etag='"tree-v1"',
            not_modified=True,
            payload=None,
            tree_key="repository-key",
        ),
    )

    result = projects_api.read_project_github_files(
        uuid4(),
        Response(),
        if_none_match='"tree-v1"',
        x_promty_repository_tree_key="repository-key",
        current_user=user,  # type: ignore[arg-type]
        db=db,  # type: ignore[arg-type]
    )

    assert isinstance(result, Response)
    assert result.status_code == 304
    assert result.headers["etag"] == '"tree-v1"'
    assert result.headers["x-promty-repository-tree-key"] == "repository-key"


def test_project_github_tree_exposes_validators_with_fresh_payload(
    monkeypatch,
) -> None:
    payload = {
        "available": True,
        "files": [],
        "message": None,
        "repository": "acme/demo",
        "status": "ok",
    }
    monkeypatch.setattr(
        projects_api,
        "_project_for_user",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        projects_api,
        "read_github_repository_tree_with_etag",
        lambda *_args, **_kwargs: GithubRepositoryTreeResult(
            etag='"tree-v2"',
            not_modified=False,
            payload=payload,
            tree_key="repository-key",
        ),
    )
    response = Response()

    result = projects_api.read_project_github_files(
        uuid4(),
        response,
        if_none_match=None,
        x_promty_repository_tree_key=None,
        current_user=object(),  # type: ignore[arg-type]
        db=object(),  # type: ignore[arg-type]
    )

    assert result == payload
    assert response.headers["etag"] == '"tree-v2"'
    assert response.headers["x-promty-repository-tree-key"] == "repository-key"
