from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Table, Text, UniqueConstraint

from .schema import PREFIX, metadata

agent_events = Table(
    PREFIX + "agent_events",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("event_key", String(64), nullable=False, unique=True, index=True),
    Column("event_type", String(48), nullable=False, index=True),
    Column("subject_type", String(32), nullable=False),
    Column("subject_key", String(128), nullable=False, index=True),
    Column("payload_json", Text, nullable=False, default="{}"),
    Column("status", String(20), nullable=False, default="pending", index=True),
    Column("attempts", Integer, nullable=False, default=0),
    Column("available_at", DateTime(timezone=True), nullable=False, index=True),
    Column("locked_at", DateTime(timezone=True), nullable=True),
    Column("locked_by", String(80), nullable=True),
    Column("last_error", Text, nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
Index(PREFIX + "ix_agent_event_claim", agent_events.c.status, agent_events.c.available_at, agent_events.c.id)

agent_runs = Table(
    PREFIX + "agent_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("event_id", Integer, ForeignKey(agent_events.c.id), nullable=False, index=True),
    Column("worker_id", String(80), nullable=False),
    Column("status", String(20), nullable=False, index=True),
    Column("result_json", Text, nullable=False, default="{}"),
    Column("error_text", Text, nullable=False, default=""),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
)

agent_effects = Table(
    PREFIX + "agent_effects",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("event_id", Integer, ForeignKey(agent_events.c.id), nullable=False, index=True),
    Column("effect_key", String(96), nullable=False),
    Column("effect_type", String(48), nullable=False),
    Column("payload_json", Text, nullable=False, default="{}"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("event_id", "effect_key", name=PREFIX + "uq_agent_effect"),
)
