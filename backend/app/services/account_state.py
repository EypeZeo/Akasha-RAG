"""Small, durable projection of local provider authentication state.

The database stores only a reference to local credential material, never a
cookie or token.  This keeps the settings UI and the runtime provider state in
agreement without widening the credential attack surface.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.external_urls import safe_platform_image_url
from app.models.entities import SourceAccount


def record_account_state(
    db: Session,
    platform: str,
    *,
    active: bool,
    auth_state_ref: str,
    nickname: str = "",
    avatar_url: str = "",
) -> SourceAccount | None:
    platform = platform.strip().lower()
    status = "active" if active else "revoked"
    safe_avatar_url = safe_platform_image_url(platform, avatar_url) if active else ""
    row = db.scalar(
        select(SourceAccount).where(
            SourceAccount.platform == platform,
            SourceAccount.local_profile_id == "default",
        )
    )
    if row is None:
        if not active:
            return None
        row = SourceAccount(
            platform=platform,
            local_profile_id="default",
            auth_state_ref=auth_state_ref if active else "",
            status=status,
            nickname=nickname[:128] if active else "",
            avatar_url=safe_avatar_url,
        )
        db.add(row)
        return row
    if row.status != status or row.auth_state_ref != (auth_state_ref if active else ""):
        row.status = status
        row.auth_state_ref = auth_state_ref if active else ""
    # Guard each assignment against the value already stored, same as the
    # status/auth_state_ref fields above -- otherwise this fires on every
    # poll (e.g. list_platforms_status every 30s) and issues a redundant
    # UPDATE each time even when nothing actually changed.
    if not active:
        if row.nickname:
            row.nickname = ""
        if row.avatar_url:
            row.avatar_url = ""
    else:
        if nickname and row.nickname != nickname[:128]:
            row.nickname = nickname[:128]
        if safe_avatar_url and row.avatar_url != safe_avatar_url:
            row.avatar_url = safe_avatar_url
    return row


def ensure_source_account_profile_columns(engine: Engine) -> None:
    """Idempotently extend existing SQLite installations with display-only fields."""
    if engine.dialect.name != "sqlite":
        return
    with engine.begin() as connection:
        columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(source_accounts)")
        }
        for name, definition in (
            ("nickname", "VARCHAR(128) NOT NULL DEFAULT ''"),
            ("avatar_url", "VARCHAR(1024) NOT NULL DEFAULT ''"),
        ):
            if name not in columns:
                connection.exec_driver_sql(
                    f"ALTER TABLE source_accounts ADD COLUMN {name} {definition}"
                )
