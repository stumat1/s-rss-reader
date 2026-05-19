from datetime import datetime, timezone
from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)


class Category(Base):
    __tablename__ = "categories"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    color: Mapped[str | None] = mapped_column(String, nullable=True)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Feed(Base):
    __tablename__ = "feeds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String)
    site_url: Mapped[str | None] = mapped_column(String)
    favicon_url: Mapped[str | None] = mapped_column(String)
    last_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetch_interval_min: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    etag: Mapped[str | None] = mapped_column(String, nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    articles: Mapped[list["Article"]] = relationship(
        "Article", back_populates="feed", cascade="all, delete-orphan"
    )


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("feed_id", "guid"),
        Index("ix_articles_feed_deleted_published", "feed_id", "is_deleted", "published_at"),
        Index("ix_articles_deleted_read", "is_deleted", "is_read"),
        Index("ix_articles_deleted_favourite", "is_deleted", "is_favourite"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feed_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("feeds.id", ondelete="CASCADE"), nullable=False
    )
    guid: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str | None] = mapped_column(String)
    link: Mapped[str | None] = mapped_column(String)
    summary: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    is_favourite: Mapped[bool] = mapped_column(Boolean, default=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

    feed: Mapped["Feed"] = relationship("Feed", back_populates="articles")
