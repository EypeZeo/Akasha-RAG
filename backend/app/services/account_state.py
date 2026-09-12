"""Small, durable projection of local provider authentication state.

The database stores only a reference to local credential material, never a
cookie or token.  This keeps the settings UI and the runtime provider state in
agreement without widening the credential attack surface.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

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
            avatar_url=avatar_url[:1024] if active else "",
        )
        db.add(row)
        return row
    if row.status != status or row.auth_state_ref != (auth_state_ref if active else ""):
        row.status = status
        row.auth_state_ref = auth_state_ref if active else ""
    if not active:
        row.nickname = ""
        row.avatar_url = ""
    else:
        if nickname:
            row.nickname = nickname[:128]
        if avatar_url:
            row.avatar_url = avatar_url[:1024]
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
