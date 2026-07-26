from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response

from app.api import account as account_api
from app.core.security import require_external_ai_consent
from app.models.tokens import CollectorToken
from app.models.users import User
from app.schemas.account import (
    AccountDeletionRequest,
    AccountDeletionResponse,
    AccountExternalAIConsentRequest,
    AccountPolicyAcceptanceRequest,
    AccountPolicyConsentsResponse,
)
from app.services import account_settings
from app.services.account_settings import (
    LATEST_COLLECTOR_VERSION,
    accept_current_policies_response,
    account_overview_response,
    create_collector_token_response,
    update_account_preferences_response,
    update_external_ai_consent_response,
)


def test_latest_collector_version_fallback_never_exceeds_package_manifest() -> None:
    manifest_path = Path(__file__).resolve().parents[2] / "collector" / "package.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fallback = tuple(int(part) for part in LATEST_COLLECTOR_VERSION.split("."))
    package = tuple(int(part) for part in manifest["version"].split("."))

    assert fallback <= package


def test_account_routes_publish_response_contracts() -> None:
    from app.main import app

    schema = app.openapi()
    paths = schema["paths"]

    assert paths["/api/account/overview"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]["$ref"].endswith("AccountOverviewResponse")
    assert paths["/api/account/collector-tokens"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("CollectorTokenCreateResponse")
    assert paths["/api/account"]["delete"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"].endswith("AccountDeletionResponse")
    assert paths["/api/account/policy-acceptance"]["put"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]["$ref"].endswith(
        "AccountPolicyConsentsResponse"
    )
    assert paths["/api/account/external-ai-consent"]["put"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"]["$ref"].endswith(
        "AccountPolicyConsentsResponse"
    )


class FakeScalarResult:
    def __init__(self, items: list[object]) -> None:
        self.items = items

    def all(self) -> list[object]:
        return self.items


class FakeSession:
    def __init__(
        self,
        *,
        tokens: list[CollectorToken] | None = None,
    ) -> None:
        self.tokens = tokens or []

    def add(self, item: object) -> None:
        if isinstance(item, CollectorToken):
            self.tokens.append(item)

    def flush(self) -> None:
        return None

    def get(self, model: type[CollectorToken], item_id: object) -> CollectorToken | None:
        if model is not CollectorToken:
            return None
        return next((token for token in self.tokens if token.id == item_id), None)

    def scalar(self, _statement: object) -> None:
        return None

    def scalars(self, _statement: object) -> FakeScalarResult:
        return FakeScalarResult(self.tokens)


def _user() -> User:
    return User(
        email="member@example.com",
        github_id="github-member",
        id=uuid4(),
        username="member",
    )


def test_create_collector_token_response_returns_secret_once() -> None:
    user = _user()
    db = FakeSession()

    response = create_collector_token_response(db, name="Local laptop", user=user)

    assert response["token"].startswith("ph_")
    assert response["collector_token"]["name"] == "Local laptop"
    assert response["collector_token"]["status"] == "active"
    assert len(db.tokens) == 1
    assert db.tokens[0].token_hash != response["token"]


def test_account_overview_response_includes_connection_and_tokens(monkeypatch) -> None:
    monkeypatch.setattr(
        account_settings,
        "get_latest_collector_version",
        lambda *, fallback: fallback,
    )
    user = _user()
    db = FakeSession(
        tokens=[
            CollectorToken(
                id=uuid4(),
                name="Local laptop",
                token_hash="hash",
                user_id=user.id,
            )
        ]
    )

    response = account_overview_response(db, user=user)

    assert response["user"]["id"] == str(user.id)
    assert response["user"]["preferred_locale"] == "en"
    assert response["github_connection"]["connected"] is False
    assert response["collector_tokens"][0]["name"] == "Local laptop"
    assert response["latest_collector_version"] == "0.1.7"
    assert response["policy_consents"]["policy_accepted"] is False


def test_account_language_preference_is_saved() -> None:
    user = _user()
    db = FakeSession()

    response = update_account_preferences_response(
        db,
        preferred_locale="zh",
        user=user,
    )

    assert response == {"preferred_locale": "zh"}
    assert user.preferred_locale == "zh"


def test_policy_acceptance_and_external_ai_choice_are_recorded_separately() -> None:
    user = _user()
    db = FakeSession()

    response = accept_current_policies_response(db, user=user)  # type: ignore[arg-type]

    AccountPolicyConsentsResponse.model_validate(response)
    assert response["policy_accepted"] is True
    assert response["eligibility_confirmed"] is True
    assert response["external_ai_allowed"] is False
    assert user.policy_version == response["current_policy_version"]
    policy_accepted_at = user.policy_accepted_at

    allowed = update_external_ai_consent_response(
        db,  # type: ignore[arg-type]
        allow_external_ai=True,
        user=user,
    )
    assert allowed["policy_accepted"] is True
    assert allowed["external_ai_allowed"] is True
    assert user.policy_accepted_at == policy_accepted_at
    assert user.external_ai_consent_version == response["current_policy_version"]

    revoked = update_external_ai_consent_response(
        db,  # type: ignore[arg-type]
        allow_external_ai=False,
        user=user,
    )
    assert revoked["policy_accepted"] is True
    assert revoked["external_ai_allowed"] is False
    assert user.external_ai_consented_at is None


def test_policy_request_requires_explicit_required_confirmations() -> None:
    with pytest.raises(ValueError):
        AccountPolicyAcceptanceRequest.model_validate(
            {
                "accept_privacy_notice": True,
                "accept_terms": False,
                "confirm_age_and_business_use": True,
            }
        )
    assert AccountExternalAIConsentRequest(allow_external_ai=False).allow_external_ai is False


def test_external_ai_generation_requires_current_separate_consent() -> None:
    user = _user()
    with pytest.raises(HTTPException) as not_accepted:
        require_external_ai_consent(user)
    assert not_accepted.value.status_code == 403

    accept_current_policies_response(FakeSession(), user=user)  # type: ignore[arg-type]
    with pytest.raises(HTTPException) as disabled:
        require_external_ai_consent(user)
    assert disabled.value.status_code == 403

    update_external_ai_consent_response(
        FakeSession(),  # type: ignore[arg-type]
        allow_external_ai=True,
        user=user,
    )
    assert require_external_ai_consent(user) is user


def test_account_deletion_requires_exact_username() -> None:
    with pytest.raises(HTTPException) as exc_info:
        account_api.delete_current_account(
            AccountDeletionRequest(
                acknowledge_permanent_deletion=True,
                confirmation="different-user",
            ),
            Response(),
            current_user=_user(),
            db=object(),  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 400


def test_account_deletion_clears_session_cookies(monkeypatch) -> None:
    user = _user()
    response = Response()
    committed: list[object] = []
    monkeypatch.setattr(
        account_api,
        "delete_user_account_data",
        lambda _db, *, user: {
            "collector_tokens": 1,
            "projects": 2,
            "published_flows": 3,
        },
    )
    monkeypatch.setattr(
        account_api,
        "_commit_or_conflict",
        lambda db, *, detail: committed.append((db, detail)),
    )
    tombstones: list[object] = []
    monkeypatch.setattr(
        account_api,
        "record_account_deletion_tombstone",
        lambda user_id: tombstones.append(user_id) or True,
    )

    payload = account_api.delete_current_account(
        AccountDeletionRequest(
            acknowledge_permanent_deletion=True,
            confirmation=user.username,
        ),
        response,
        current_user=user,
        db=object(),  # type: ignore[arg-type]
    )

    AccountDeletionResponse.model_validate(payload)
    assert payload["status"] == "deleted"
    assert len(committed) == 1
    assert tombstones == [user.id]
    set_cookie_headers = response.headers.getlist("set-cookie")
    assert any("promty_session=" in header and "Max-Age=0" in header for header in set_cookie_headers)
    assert any("promty_refresh=" in header and "Max-Age=0" in header for header in set_cookie_headers)
