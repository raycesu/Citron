from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    normalized_title = Column(String(500), index=True)
    description = Column(Text, default="")
    url = Column(String(1000), unique=True, index=True, nullable=False)
    summary = Column(Text, default="")
    source = Column(String(100), index=True)
    location = Column(String(300), default="")
    city = Column(String(200), default="")
    country = Column(String(100), index=True, default="")
    province_state = Column(String(100), index=True, default="")
    start_date = Column(DateTime, index=True, nullable=True)
    end_date = Column(DateTime, nullable=True)
    deadline = Column(DateTime, nullable=True)
    prize_pool = Column(String(200), nullable=True)
    is_online = Column(Boolean, default=False, index=True)
    is_inperson = Column(Boolean, default=True, index=True)
    has_travel_grant = Column(Boolean, default=False, index=True)
    travel_grant_details = Column(Text, nullable=True)
    relevance_score = Column(Float, default=0.0, index=True)
    priority_score = Column(Float, default=0.0, index=True)
    scraped_at = Column(DateTime, default=datetime.utcnow, index=True)
    ai_classified = Column(Boolean, default=False)
    last_seen_at = Column(DateTime, nullable=True, index=True)
    consecutive_misses = Column(Integer, default=0, nullable=False, server_default="0")

    tags = relationship("Tag", secondary="event_tags", back_populates="events", lazy="joined")

    __table_args__ = (
        Index("ix_events_priority_date", "priority_score", "start_date"),
        Index("ix_events_country_inperson", "country", "is_inperson"),
    )


class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False, index=True)

    events = relationship("Event", secondary="event_tags", back_populates="tags")


class EventTag(Base):
    __tablename__ = "event_tags"

    event_id = Column(
        Integer, ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id = Column(
        Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


class AICache(Base):
    __tablename__ = "ai_cache"

    id = Column(Integer, primary_key=True, index=True)
    event_url = Column(String(1000), unique=True, index=True, nullable=False)
    classification_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
