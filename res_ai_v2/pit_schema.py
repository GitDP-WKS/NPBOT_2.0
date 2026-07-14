from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from .schema import PREFIX, metadata, review_tasks, source_files, source_rows


pit_observations = Table(
    PREFIX + "pit_observations",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("observation_key", String(64), nullable=False, unique=True, index=True),
    Column("canonical_hash", String(64), nullable=False, index=True),
    Column("branch_name", Text, nullable=False, default=""),
    Column("res_name", Text, nullable=False, default="", index=True),
    Column("locality", Text, nullable=False, default=""),
    Column("district", Text, nullable=False, default=""),
    Column("settlement", Text, nullable=False, default=""),
    Column("street", Text, nullable=False, default=""),
    Column("raw_text", Text, nullable=False, default=""),
    Column("locality_key", Text, nullable=False, default="", index=True),
    Column("district_key", Text, nullable=False, default="", index=True),
    Column("settlement_key", Text, nullable=False, default="", index=True),
    Column("street_key", Text, nullable=False, default="", index=True),
    Column("text_key", Text, nullable=False, default=""),
    Column("occurrence_count", Integer, nullable=False, default=1),
    Column("source_count", Integer, nullable=False, default=1),
    Column("state", String(24), nullable=False, default="new", index=True),
    Column("first_seen_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
Index(
    PREFIX + "ix_pit_anchor",
    pit_observations.c.locality_key,
    pit_observations.c.settlement_key,
    pit_observations.c.district_key,
)

pit_occurrences = Table(
    PREFIX + "pit_occurrences",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("observation_id", Integer, ForeignKey(pit_observations.c.id), nullable=False, index=True),
    Column("source_file_id", Integer, ForeignKey(source_files.c.id), nullable=False, index=True),
    Column("source_row_id", Integer, ForeignKey(source_rows.c.id), nullable=False, unique=True, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("observation_id", "source_row_id", name=PREFIX + "uq_pit_occurrence"),
)

knowledge_directives = Table(
    PREFIX + "knowledge_directives",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("directive_key", String(64), nullable=False, unique=True, index=True),
    Column("task_id", Integer, ForeignKey(review_tasks.c.id), nullable=True, index=True),
    Column("subject_type", String(32), nullable=False),
    Column("subject_key", String(128), nullable=False, index=True),
    Column("selection_json", Text, nullable=False),
    Column("actor", Text, nullable=False),
    Column("active", Boolean, nullable=False, default=True, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
)

knowledge_generations = Table(
    PREFIX + "knowledge_generations",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("generation_key", String(64), nullable=False, unique=True, index=True),
    Column("status", String(24), nullable=False, index=True),
    Column("trigger_type", String(40), nullable=False),
    Column("trigger_key", Text, nullable=False),
    Column("source_version", Integer, nullable=False, default=0),
    Column("full_rebuild", Boolean, nullable=False, default=False),
    Column("rows_scanned", Integer, nullable=False, default=0),
    Column("rows_changed", Integer, nullable=False, default=0),
    Column("tasks_created", Integer, nullable=False, default=0),
    Column("stats_json", Text, nullable=False, default="{}"),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
)

review_task_leases = Table(
    PREFIX + "review_task_leases",
    metadata,
    Column("task_id", Integer, ForeignKey(review_tasks.c.id), primary_key=True),
    Column("reviewer", Text, nullable=False, index=True),
    Column("lease_token", String(64), nullable=False, unique=True),
    Column("claimed_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False, index=True),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

agent_daily_runs = Table(
    PREFIX + "agent_daily_runs",
    metadata,
    Column("run_date", String(10), primary_key=True),
    Column("event_id", Integer, nullable=True),
    Column("status", String(24), nullable=False, default="scheduled"),
    Column("started_at", DateTime(timezone=True), nullable=True),
    Column("finished_at", DateTime(timezone=True), nullable=True),
    Column("result_json", Text, nullable=False, default="{}"),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
