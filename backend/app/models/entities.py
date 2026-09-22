"""
数据表实体定义模块 (v0.7.0 Multi-Platform Architecture)

定义项目规范化数据表的 ORM 模型：
- SourceAccount：多平台认证账户表
- FavoriteCollection：多平台收藏夹表（以 platform + remote_collection_id 联合唯一）
- ContentItem：规范化内容实体表（视频/图文笔记，以 platform + remote_item_id 联合唯一）
- CollectionItemRelation：收藏夹与内容多对多关联表
- IngestionItem：内容转写与持久入库任务表（替代旧版纯内存易失性状态）
- ContentPart：多 P 分集内容表（每 P 独立记录转写与时间范围）
- ChatSession：对话会话
- ChatMessage：对话消息
"""
import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, event, func, select, text
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column, relationship, synonym

from app.db.base import Base


class SourceAccount(Base):
    """
    多平台认证账户表

    记录用户在各平台的登录凭证引用、到期时间与状态。
    """
    __tablename__ = "source_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="douyin")
    local_profile_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    auth_state_ref: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    # Display-only profile data. Credentials remain exclusively in provider state files.
    nickname: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    avatar_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    auth_expiry: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("platform", "local_profile_id", name="uq_source_account"),
    )

    def __repr__(self) -> str:
        return f"<SourceAccount platform='{self.platform}' profile='{self.local_profile_id}' status='{self.status}'>"


class FavoriteCollection(Base):
    """
    多平台收藏夹表

    存储各平台同步的收藏夹元信息，以 (platform, remote_collection_id) 联合唯一。
    """
    __tablename__ = "favorite_collections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="douyin", index=True)
    remote_collection_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    cover_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    video_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    snapshot_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # 兼容别名：platform_collection_id 映射到 remote_collection_id
    platform_collection_id = synonym("remote_collection_id")

    # 关联关系
    collection_items: Mapped[list["CollectionItemRelation"]] = relationship(
        "CollectionItemRelation",
        back_populates="collection",
        cascade="all, delete-orphan",
    )
    items: Mapped[list["ContentItem"]] = relationship(
        "ContentItem",
        secondary="collection_items",
        back_populates="collections",
        viewonly=True,
    )

    __table_args__ = (
        UniqueConstraint("platform", "remote_collection_id", name="uq_collection_platform_remote"),
    )

    def __repr__(self) -> str:
        return f"<FavoriteCollection id={self.id} platform='{self.platform}' title='{self.title}'>"


class ContentItem(Base):
    """
    规范化内容实体表（视频/图文笔记）

    代表平台发布的作品实体，与单一收藏夹彻底解耦。
    以 (platform, remote_item_id) 联合唯一。
    """
    __tablename__ = "content_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="douyin", index=True)
    remote_item_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    canonical_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    author: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    duration: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cover_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    video_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    content_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="video")
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    part_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    # When this item's provider-side part/page data was last confirmed fresh
    # (BUG-10/NET-04). NULL means "never enriched" -- added post-hoc via
    # ensure_content_item_enrichment_column(), same pattern as
    # ensure_source_account_profile_columns().
    last_enriched_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # 兼容别名：platform_item_id 映射到 remote_item_id
    platform_item_id = synonym("remote_item_id")

    def __init__(self, *args, collection_id: Optional[int] = None, **kwargs):
        super().__init__(*args, **kwargs)
        if collection_id is not None:
            self.collection_items.append(
                CollectionItemRelation(
                    collection_id=collection_id,
                    is_active=getattr(self, "is_active", True),
                )
            )

    @hybrid_property
    def collection_id(self) -> Optional[int]:
        """兼容属性：返回首个活跃关联的收藏夹 ID"""
        for rel in self.collection_items:
            if rel.is_active:
                return rel.collection_id
        return None

    @collection_id.expression
    def collection_id(cls):
        return (
            select(CollectionItemRelation.collection_id)
            .where(
                CollectionItemRelation.content_item_id == cls.id,
                CollectionItemRelation.is_active.is_(True),
            )
            .scalar_subquery()
        )

    # 关联关系
    collection_items: Mapped[list["CollectionItemRelation"]] = relationship(
        "CollectionItemRelation",
        back_populates="content_item",
        cascade="all, delete-orphan",
    )
    collections: Mapped[list["FavoriteCollection"]] = relationship(
        "FavoriteCollection",
        secondary="collection_items",
        back_populates="items",
        viewonly=True,
    )
    ingestion_items: Mapped[list["IngestionItem"]] = relationship(
        "IngestionItem",
        back_populates="content_item",
        cascade="all, delete-orphan",
    )
    content_parts: Mapped[list["ContentPart"]] = relationship(
        "ContentPart",
        back_populates="content_item",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("platform", "remote_item_id", name="uq_content_platform_remote"),
        # Ingestion updates updated_at, so listing must use immutable columns.
        Index("ix_content_items_active_created_id", "is_active", "created_at", "id"),
        Index("ix_content_items_platform_active_created_id", "platform", "is_active", "created_at", "id"),
        Index("ix_content_items_active_id", "is_active", "id"),
        Index("ix_content_items_platform_active_id", "platform", "is_active", "id"),
    )

    def __repr__(self) -> str:
        return f"<ContentItem id={self.id} platform='{self.platform}' remote_id='{self.remote_item_id}' title='{self.title}'>"


class CollectionItemRelation(Base):
    """
    收藏夹与内容多对多关联表

    跟踪内容在具体收藏夹中的有效性与初次/末次同步时间。
    """
    __tablename__ = "collection_items"

    collection_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("favorite_collections.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    content_item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("content_items.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    first_seen_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    collection: Mapped["FavoriteCollection"] = relationship("FavoriteCollection", back_populates="collection_items")
    content_item: Mapped["ContentItem"] = relationship("ContentItem", back_populates="collection_items")


class IngestionItem(Base):
    """
    内容入库任务与转写状态持久表

    由 SQLite 持久化租约机制调度，记录每个内容的 ASR 转写与索引元数据。
    """
    __tablename__ = "ingestion_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    pipeline_version: Mapped[str] = mapped_column(String(32), nullable=False, default="v0.7.0")
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_retry_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    lease_owner: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    transcript_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    transcript_checkpoint: Mapped[str] = mapped_column(Text, nullable=False, default="")
    index_manifest: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    has_substantive_content: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    processed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    content_item: Mapped["ContentItem"] = relationship("ContentItem", back_populates="ingestion_items")

    __table_args__ = (
        UniqueConstraint("content_item_id", "pipeline_version", "source_fingerprint", name="uq_ingestion_content_pipe_fingerprint"),
    )

    def __init__(
        self,
        *args,
        content_item_id: Optional[int] = None,
        platform_item_id: Optional[str] = None,
        title: Optional[str] = None,
        **kwargs,
    ):
        self._temp_platform_item_id = platform_item_id
        self._temp_title = title
        if content_item_id is not None:
            kwargs["content_item_id"] = content_item_id
        super().__init__(*args, **kwargs)

    @hybrid_property
    def platform_item_id(self) -> str:
        if self.content_item:
            return self.content_item.remote_item_id
        return getattr(self, "_temp_platform_item_id", "") or ""

    @platform_item_id.setter
    def platform_item_id(self, val: str) -> None:
        self._temp_platform_item_id = val

    @platform_item_id.expression
    def platform_item_id(cls):
        return (
            select(ContentItem.remote_item_id)
            .where(ContentItem.id == cls.content_item_id)
            .scalar_subquery()
        )

    @hybrid_property
    def title(self) -> str:
        if self.content_item:
            return self.content_item.title
        return getattr(self, "_temp_title", "") or ""

    @title.setter
    def title(self, val: str) -> None:
        self._temp_title = val

    @title.expression
    def title(cls):
        return (
            select(ContentItem.title)
            .where(ContentItem.id == cls.content_item_id)
            .scalar_subquery()
        )

    def __repr__(self) -> str:
        return f"<IngestionItem id={self.id} item_id={self.content_item_id} status='{self.status}'>"


@event.listens_for(IngestionItem, "before_insert")
def _resolve_ingestion_content_item_id(mapper, connection, target):
    """自动补全 content_item_id，若不存在则原子关联或创建存量 ContentItem"""
    if not getattr(target, "content_item_id", None):
        temp_remote_id = getattr(target, "_temp_platform_item_id", None)
        if temp_remote_id:
            row = connection.execute(
                select(ContentItem.id).where(ContentItem.remote_item_id == temp_remote_id)
            ).fetchone()
            if row:
                target.content_item_id = row[0]
            else:
                res = connection.execute(
                    ContentItem.__table__.insert().values(
                        platform="douyin",
                        remote_item_id=temp_remote_id,
                        canonical_url=f"https://www.douyin.com/video/{temp_remote_id}",
                        title=getattr(target, "_temp_title", "") or temp_remote_id,
                        author="",
                        duration=0,
                        cover_url="",
                        video_url="",
                        content_kind="video",
                        source_fingerprint="",
                        part_count=1,
                        is_active=True,
                    )
                )
                target.content_item_id = res.inserted_primary_key[0]


class ContentPart(Base):
    """
    内容多 P 分集表

    首类公民级多分集模型，支持 B 站多 P 视频按集独立拉取字幕/ASR 与精准跳转。
    """
    __tablename__ = "content_parts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("content_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    remote_part_id: Mapped[str] = mapped_column(String(64), nullable=False)
    part_index: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    part_title: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    duration: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    transcript_source: Mapped[str] = mapped_column(String(32), nullable=False, default="whisper_asr")
    transcript_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1")
    time_range: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    content_item: Mapped["ContentItem"] = relationship("ContentItem", back_populates="content_parts")

    __table_args__ = (
        UniqueConstraint("content_item_id", "remote_part_id", name="uq_content_parts_remote"),
    )

    def __repr__(self) -> str:
        return f"<ContentPart id={self.id} item_id={self.content_item_id} P{self.part_index} title='{self.part_title}'>"


# 兼容层别名定义
FavoriteVideo = ContentItem
VideoCache = IngestionItem


class ChatSession(Base):
    """
    对话会话

    每次问答归属一个会话，支持多轮对话。
    """

    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    """主键 ID"""

    title: Mapped[str] = mapped_column(String(128), nullable=False, default="New Chat")
    """会话标题（默认取首条用户消息前 40 字）"""

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    """创建时间"""

    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    """更新时间"""

    # 关联
    messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage", back_populates="session", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<ChatSession id={self.id} title='{self.title}'>"


class ChatMessage(Base):
    """
    对话消息

    记录每次问答的用户消息和助手回复。
    """

    __tablename__ = "chat_messages"
    __table_args__ = (
        # One client key names one message within a session. Rows without a key (older rows, or
        # messages sent without one) are not constrained.
        Index("uq_chat_messages_session_client_key", "session_id", "client_key", unique=True,
              sqlite_where=text("client_key IS NOT NULL")),
    )

    client_key: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    """主键 ID"""

    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("chat_sessions.id"), nullable=False, index=True
    )
    """所属会话 ID"""

    role: Mapped[str] = mapped_column(String(16), nullable=False)
    """
    消息角色：
    - user: 用户提问
    - assistant: 助手回复
    """

    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    """消息内容"""

    route_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="direct"
    )
    """
    路由类型：
    - direct: 直接回复（问候等不需要检索的场景）
    - vector: 向量检索 + RAG
    - db_list: 数据库列表查询
    - db_content: 数据库内容概览
    """

    retrieved_video_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    """召回的视频 ID 列表（JSON 数组字符串）"""

    retrieved_chunk_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    """召回的 chunk ID 列表（JSON 数组字符串）"""

    model: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    """使用的 LLM 模型名称"""

    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """请求延迟（毫秒），仅 assistant 角色记录"""

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    """创建时间"""

    # 关联
    session: Mapped["ChatSession"] = relationship(
        "ChatSession", back_populates="messages"
    )

    def __repr__(self) -> str:
        return f"<ChatMessage id={self.id} role='{self.role}' session_id={self.session_id}>"
