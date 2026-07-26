from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.core.locales import normalize_app_locale
from app.core.policies import CURRENT_POLICY_VERSION, serialize_policy_consents
from app.core.security import hash_collector_token, issue_collector_token, is_admin_user
from app.core.time import utc_now
from app.models.github_connections import GitHubConnection
from app.models.tokens import CollectorToken
from app.models.users import User
from app.services.collector_versions import get_latest_collector_version


# This fallback must be a version that is already available from npm. Bump it
# only after the matching collector package has been published successfully.
LATEST_COLLECTOR_VERSION = "0.1.7"


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _active_github_connection(db: Session, user: User) -> GitHubConnection | None:
    return db.scalar(
        select(GitHubConnection).where(
            GitHubConnection.user_id == user.id,
            GitHubConnection.revoked_at.is_(None),
        )
    )


def _github_connection_for_user(db: Session, user: User) -> GitHubConnection | None:
    return db.scalar(
        select(GitHubConnection)
        .where(GitHubConnection.user_id == user.id)
        .order_by(desc(GitHubConnection.updated_at), desc(GitHubConnection.created_at))
    )


def serialize_user(user: User) -> dict[str, Any]:
    return {
        "avatar_url": user.avatar_url,
        "email": user.email,
        "id": str(user.id),
        "is_admin": is_admin_user(user),
        "preferred_locale": normalize_app_locale(user.preferred_locale),
        "username": user.username,
    }


def update_account_preferences_response(
    db: Session,
    *,
    preferred_locale: str,
    user: User,
) -> dict[str, str]:
    user.preferred_locale = preferred_locale
    db.flush()
    return {"preferred_locale": user.preferred_locale}


def serialize_github_connection(
    db: Session,
    *,
    user: User,
) -> dict[str, Any]:
    connection = _github_connection_for_user(db, user)
    active_connection = connection is not None and connection.revoked_at is None
    scopes = (
        [scope.strip() for scope in (connection.scopes or "").split(",") if scope.strip()]
        if connection
        else []
    )
    return {
        "connected": active_connection,
        "created_at": _iso(connection.created_at) if connection else None,
        "revoked_at": _iso(connection.revoked_at) if connection else None,
        "scopes": scopes,
        "status": "connected" if active_connection else "not_connected",
        "token_type": connection.token_type if connection else None,
        "updated_at": _iso(connection.updated_at) if connection else None,
    }


def serialize_collector_token(token: CollectorToken) -> dict[str, Any]:
    return {
        "collector_version": token.collector_version,
        "created_at": _iso(token.created_at),
        "id": str(token.id),
        "last_used_at": _iso(token.last_used_at),
        "name": token.name,
        "revoked_at": _iso(token.revoked_at),
        "status": "revoked" if token.revoked_at else "active",
    }


def list_collector_tokens(db: Session, *, user: User) -> list[dict[str, Any]]:
    tokens = db.scalars(
        select(CollectorToken)
        .where(CollectorToken.user_id == user.id)
        .order_by(
            CollectorToken.revoked_at.is_not(None),
            desc(CollectorToken.last_used_at),
            desc(CollectorToken.created_at),
        )
    ).all()
    return [serialize_collector_token(token) for token in tokens]


def account_overview_response(db: Session, *, user: User) -> dict[str, Any]:
    github_connection = serialize_github_connection(db, user=user)
    serialized_user = serialize_user(user)
    serialized_user["github_repository_access"] = github_connection["connected"]
    return {
        "latest_collector_version": get_latest_collector_version(fallback=LATEST_COLLECTOR_VERSION),
        "collector_tokens": list_collector_tokens(db, user=user),
        "github_connection": github_connection,
        "policy_consents": serialize_policy_consents(user),
        "user": serialized_user,
    }


def accept_current_policies_response(
    db: Session,
    *,
    user: User,
) -> dict[str, Any]:
    accepted_at = utc_now()
    user.policy_version = CURRENT_POLICY_VERSION
    user.policy_accepted_at = accepted_at
    user.eligibility_confirmed_at = accepted_at
    db.flush()
    return serialize_policy_consents(user)


def update_external_ai_consent_response(
    db: Session,
    *,
    allow_external_ai: bool,
    user: User,
) -> dict[str, Any]:
    accepted_at = utc_now()
    if allow_external_ai:
        user.external_ai_consent_version = CURRENT_POLICY_VERSION
        user.external_ai_consented_at = accepted_at
    else:
        user.external_ai_consent_version = None
        user.external_ai_consented_at = None
    db.flush()
    return serialize_policy_consents(user)


def create_collector_token_response(
    db: Session,
    *,
    name: str | None,
    user: User,
) -> dict[str, Any]:
    raw_token = issue_collector_token()
    token = CollectorToken(
        name=name or "Promty CLI",
        token_hash=hash_collector_token(raw_token),
        user_id=user.id,
    )
    db.add(token)
    db.flush()
    return {
        "token": raw_token,
        "collector_token": serialize_collector_token(token),
    }


def collector_token_for_user(
    db: Session,
    *,
    token_id: UUID,
    user: User,
) -> CollectorToken:
    token = db.get(CollectorToken, token_id)
    if token is None or token.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Collector token not found",
        )
    return token


def update_collector_token_response(
    db: Session,
    *,
    name: str,
    token_id: UUID,
    user: User,
) -> dict[str, Any]:
    token = collector_token_for_user(db, token_id=token_id, user=user)
    if token.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Revoked collector tokens cannot be renamed.",
        )
    token.name = name
    db.flush()
    return serialize_collector_token(token)


def revoke_collector_token_response(
    db: Session,
    *,
    token_id: UUID,
    user: User,
) -> dict[str, Any]:
    token = collector_token_for_user(db, token_id=token_id, user=user)
    if token.revoked_at is None:
        token.revoked_at = utc_now()
    db.flush()
    return serialize_collector_token(token)


def disconnect_github_response(db: Session, *, user: User) -> dict[str, Any]:
    connection = _active_github_connection(db, user)
    if connection is not None:
        connection.revoked_at = utc_now()
        db.flush()
    return serialize_github_connection(db, user=user)
