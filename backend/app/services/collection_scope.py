"""Collection references at API boundaries are remote IDs, qualified by platform."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import FavoriteCollection

ContentScope = set[tuple[str, str]]


class AmbiguousCollectionError(ValueError):
    """A request named an id that exists on more than one platform without saying which.

    Routes answer this with HTTP 400 so every endpoint treats the same input the same way.
    """


class CollectionNotFoundError(ValueError):
    """The requested collection does not exist for the requested platform."""


def resolve_collection(db: Session, remote_id: str, platform: str | None = None) -> FavoriteCollection | None:
    query = select(FavoriteCollection).where(
        FavoriteCollection.remote_collection_id == remote_id,
        FavoriteCollection.is_active.is_(True),
    )
    if platform and platform != "all":
        query = query.where(FavoriteCollection.platform == platform)
    rows = db.scalars(query.limit(2)).all()
    if len(rows) > 1:
        # Legacy clients may omit the platform only when the identity is unambiguous.
        raise AmbiguousCollectionError("Ambiguous collection_id; specify platform")
    return rows[0] if rows else None
