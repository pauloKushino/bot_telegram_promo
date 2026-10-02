import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Store(enum.StrEnum):
    SHOPEE = "shopee"
    MERCADOLIVRE = "mercadolivre"
    AMAZON = "amazon"


class ProductType(enum.StrEnum):
    MANGA = "manga"
    FIGURE = "figure"
    BLURAY = "bluray"
    BOX = "box"
    OUTRO = "outro"


class PostStatus(enum.StrEnum):
    PENDING = "pending"
    POSTED = "posted"
    FAILED = "failed"
    SKIPPED = "skipped"


TzDateTime = DateTime(timezone=True)
# Enums como VARCHAR com CHECK (native_enum=False) para simplificar migrações.
StoreColumn = Enum(Store, native_enum=False, length=20, validate_strings=True)
ProductTypeColumn = Enum(ProductType, native_enum=False, length=20, validate_strings=True)
PostStatusColumn = Enum(PostStatus, native_enum=False, length=20, validate_strings=True)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    store: Mapped[Store] = mapped_column(StoreColumn, nullable=False)
    external_id: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str | None] = mapped_column(Text)
    seller_name: Mapped[str | None] = mapped_column(String(200))
    seller_rating: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    sales_count: Mapped[int | None] = mapped_column(Integer)
    commission_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))

    # Classificação da IA (None = ainda não classificado)
    is_anime_merch: Mapped[bool | None] = mapped_column(Boolean)
    franchise: Mapped[str | None] = mapped_column(String(200))
    product_type: Mapped[ProductType | None] = mapped_column(ProductTypeColumn)
    volume: Mapped[str | None] = mapped_column(String(50))
    publisher: Mapped[str | None] = mapped_column(String(200))
    official_confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    classified_at: Mapped[datetime | None] = mapped_column(TzDateTime)

    first_seen_at: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now(), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")

    __table_args__ = (
        Index("ix_products_store_external", "store", "external_id", unique=True),
        Index("ix_products_active_unclassified", "is_anime_merch", postgresql_where=(active == True)),  # noqa: E712
    )


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    price_min: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    price_max: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    collected_at: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_price_history_product_collected", "product_id", "collected_at"),)


class Keyword(Base):
    __tablename__ = "keywords"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    term: Mapped[str] = mapped_column(String(200), nullable=False)
    store: Mapped[Store | None] = mapped_column(StoreColumn)  # NULL = todas as lojas
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class BlacklistTerm(Base):
    __tablename__ = "blacklist_terms"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    term: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")


class PostQueue(Base):
    __tablename__ = "post_queue"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    price_at_post: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    reference_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    affiliate_link: Mapped[str] = mapped_column(Text, nullable=False)
    sub_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[PostStatus] = mapped_column(PostStatusColumn, nullable=False, default=PostStatus.PENDING)
    scheduled_for: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now(), nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(TzDateTime)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_post_queue_status_scheduled", "status", "scheduled_for"),)


class Conversion(Base):
    """Conversão (venda/comissão) importada da API de afiliados (fase 2, spec 7.7)."""

    __tablename__ = "conversions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    store: Mapped[Store] = mapped_column(StoreColumn, nullable=False)
    sub_id: Mapped[str | None] = mapped_column(String(64))  # amarra a conversão ao post
    order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    commission: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    status: Mapped[str] = mapped_column(String(30), nullable=False)  # ex.: approved, pending, rejected
    occurred_at: Mapped[datetime] = mapped_column(TzDateTime, nullable=False)

    __table_args__ = (
        Index("ix_conversions_store_order", "store", "order_id", unique=True),
        Index("ix_conversions_sub_id", "sub_id"),
    )


class User(Base):
    """Usuário do bot (fase 3a). Mínimo necessário (privacidade — spec doc fase 3)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    username: Mapped[str | None] = mapped_column(String(50))
    full_name: Mapped[str | None] = mapped_column(String(100))
    source: Mapped[str | None] = mapped_column(String(30))  # origem do cadastro: "canal", None etc.
    is_active: Mapped[bool] = mapped_column(  # False = /parar
        Boolean, nullable=False, default=True, server_default="true"
    )
    dm_blocked: Mapped[bool] = mapped_column(  # True = usuário bloqueou o bot
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now(), nullable=False)


class Follow(Base):
    """Usuário segue uma franquia (ex.: "one piece"). Limite free: FREE_MAX_FOLLOWS."""

    __tablename__ = "follows"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    franchise: Mapped[str] = mapped_column(String(200), nullable=False)  # normalizada (lower/strip)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_follows_user_franchise", "user_id", "franchise", unique=True),)


class PriceAlert(Base):
    """Alerta de preço-alvo num produto (limite free: FREE_MAX_ALERTS). Dispara uma vez."""

    __tablename__ = "price_alerts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), nullable=False)
    target_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    triggered_at: Mapped[datetime | None] = mapped_column(TzDateTime)
    created_at: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now(), nullable=False)

    __table_args__ = (
        Index(
            "ix_price_alerts_active_product",
            "product_id",
            postgresql_where=(triggered_at == None),  # noqa: E711
        ),
    )


class DmLog(Base):
    """Cada DM enviada pelo bot — usado p/ limite diário por usuário e auditoria."""

    __tablename__ = "dm_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)  # follow_push | price_alert | aviso
    post_id: Mapped[int | None] = mapped_column(ForeignKey("post_queue.id", ondelete="SET NULL"))
    sent_at: Mapped[datetime] = mapped_column(TzDateTime, server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_dm_log_user_sent", "user_id", "sent_at"),)


class BotSetting(Base):
    """Chave-valor simples p/ estado compartilhado entre os processos bot e worker (ex.: pausa)."""

    __tablename__ = "bot_settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


# Chave usada pelo /pause e /resume
SETTING_PAUSED = "publishing_paused"
