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
        platform: Optional[str] = None,
        **kwargs,
    ):
        self._temp_platform_item_id = platform_item_id
        self._temp_title = title
        # 只有"按远端 ID 自动补建 ContentItem"这一条兼容路径需要它，见
        # _resolve_ingestion_content_item_id：没有平台事实时拒绝伪造记录。
        self._temp_platform = platform
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
    """自动补全 content_item_id：先关联已存在的 ContentItem，否则按平台事实创建。

    平台事实必须由调用方通过 ``IngestionItem(platform=...)`` 显式给出。本钩子在
    flush 阶段运行，只能看到 IngestionItem 携带的临时提示，``ingestion_items``
    表没有任何列可以反推平台——所以**没有平台提示时拒绝创建**，而不是像历史实现
    那样伪造 ``platform="douyin"`` + ``https://www.douyin.com/video/{id}``：那会把
    任意平台的条目静默写成抖音，并把用户指向一个不存在的抖音作品。

    懒 import 的理由：``app.models`` 是数据层、``app.services`` 是服务层，依赖
    方向只能是 services -> models；模块级 import 会造成分层倒置。钩子在 flush
    时才运行，此时服务层一定已经可以导入。
    """
    if getattr(target, "content_item_id", None):
        return
    temp_remote_id = getattr(target, "_temp_platform_item_id", None)
    if not temp_remote_id:
        return

    from app.services.content_kind import content_kind_for
    from app.services.platform_registry import build_canonical_url, try_get_platform

    temp_platform = getattr(target, "_temp_platform", None)
    facts = try_get_platform(temp_platform)

    lookup = select(ContentItem.id).where(ContentItem.remote_item_id == temp_remote_id)
    if facts is not None:
        # 同一 remote_item_id 允许跨平台重复（uq_content_platform_remote），
        # 所以有平台事实时必须连平台一起限定，否则可能关联到另一个平台的同号作品。
        lookup = lookup.where(ContentItem.platform == facts.platform)
    rows = connection.execute(lookup).fetchall()
    if rows:
        if facts is None and len(rows) > 1:
            # Legacy callers may omit platform when there is exactly one
            # matching row. Once the same remote id exists on two platforms,
            # choosing the first row would cross-link to the wrong provider.
            raise ValueError(
                f"remote_item_id={temp_remote_id!r} 在多个平台存在，"
                "无法在缺少 platform 时自动关联；请显式传入 platform 或 content_item_id"
            )
        target.content_item_id = rows[0][0]
        return

    if facts is None:
        raise ValueError(
            f"无法为 remote_item_id={temp_remote_id!r} 自动创建 ContentItem："
            f"IngestionItem 未携带已登记的平台（platform={temp_platform!r}），"
            "拒绝伪造一条抖音记录。请显式传入 content_item_id，"
            "或传入受支持的 platform=... 让平台事实注册表决定链接与形态。"
        )

    duration = 0
    res = connection.execute(
        ContentItem.__table__.insert().values(
            platform=facts.platform,
            remote_item_id=temp_remote_id,
            canonical_url=build_canonical_url(facts.platform, temp_remote_id),
            title=getattr(target, "_temp_title", "") or temp_remote_id,
            author="",
            duration=duration,
            cover_url="",
            video_url="",
            # 形态判据只有一处（content_kind）；duration=0 在全局口径下是图文，
            # 历史上这里写死 "video"，是这个判据的第四份定义。
            content_kind=content_kind_for(facts.platform, duration),
            source_fingerprint="",
            part_count=1,
            is_active=True,
        )
    )
    target.content_item_id = res.inserted_primary_key[0]


#: ``content_parts.transcript_source``："计划"与"正文"的区别。
#: 创建 ContentPart 只是登记了分 P 计划，此时还没有任何正文。历史上该列在创建时
#: 就写死 ``"whisper_asr"``，于是 B 站字幕来源的正文被**永久**标记为 ASR
#: （而且 ASR 引擎实际是 DashScope paraformer，"whisper" 本身就是错的）。
#: 现在：正文未产生 = :data:`TRANSCRIPT_SOURCE_UNSET`，绝不在正文存在之前声称来源。
TRANSCRIPT_SOURCE_UNSET = ""
#: 正文已产生，但生产者没有暴露它来自哪条路径。当前 B 站抓取器就是这样：
#: ``fetch_transcript()`` 把"字幕"和"音频 ASR"两条路径的结果都以 ``str`` 返回，
#: 调用方无法区分（见 knowledge_service 中分 P 归因处的说明）。
TRANSCRIPT_SOURCE_UNKNOWN = "unknown"
#: 正文直接来自平台文章/回答 API，不需要音频下载或 ASR。
TRANSCRIPT_SOURCE_ARTICLE_TEXT = "article_text"


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
    # 为什么保留 NOT NULL + 空串哨兵，而不是改成 nullable 的 NULL：
    # 1) ``db/migration.py`` 建表时给了列级 ``DEFAULT 'whisper_asr'``，而这个
    #    server default 已经烙在**存量数据库**里。若 ORM 改成不提供值（NULL），
    #    SQLite 会回落到那个陈旧的 server default，谎言会在真实安装上静默复活
    #    ——而且只在真实安装上，全新的测试库（create_all 不带 server_default）
    #    永远不会暴露它。Python 侧 ``default=TRANSCRIPT_SOURCE_UNSET`` 一定会被
    #    写进 INSERT，因此在所有库上都压得住那个陈旧默认值。
    # 2) NOT NULL -> NULL 需要 SQLite 12 步表重建，而本仓库的版本化迁移引擎
    #    (``db/migration.py``) 是一次性的 legacy->v0.7 重建（已应用的库直接
    #    ``skipped``），为它另开一条迁移路径＝发明第二套机制。
    # 3) 空串与仓库既有"未产生"约定一致（canonical_url / title / source_fingerprint
    #    等 6 列的 default 都是 ""）。
    transcript_source: Mapped[str] = mapped_column(
        String(32), nullable=False, default=TRANSCRIPT_SOURCE_UNSET
    )
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
