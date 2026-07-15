from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from .pit_schema import knowledge_generations, pit_observations
from .schema import PREFIX, address_mappings, metadata, source_files, source_rows

canonical_observations = Table(
    PREFIX + "canonical_observations",
    metadata,
    Column("observation_id", Integer, ForeignKey(pit_observations.c.id), primary_key=True),
    Column("canonical_address_key", String(64), nullable=False, index=True),
    Column("ambiguity_key", String(64), nullable=False, index=True),
    Column("context_key", String(64), nullable=False, index=True),
    Column("address_type", String(40), nullable=False, index=True),
    Column("completeness", Float, nullable=False, default=0.0),
    Column("components_json", Text, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)
Index(
    PREFIX + "ix_canonical_ambiguity_context",
    canonical_observations.c.ambiguity_key,
    canonical_observations.c.context_key,
)

source_registries = Table(
    PREFIX + "source_registries",
    metadata,
    Column("source_file_id", Integer, ForeignKey(source_files.c.id), primary_key=True),
    Column("file_hash", String(64), nullable=False, index=True),
    Column("registry_fingerprint", String(64), nullable=False, index=True),
    Column("source_system", String(120), nullable=False, index=True),
    Column("source_quality", Float, nullable=False, default=0.5),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

evidence_claims = Table(
    PREFIX + "evidence_claims",
    metadata,
    Column("evidence_key", String(64), primary_key=True),
    Column("observation_key", String(64), nullable=False, index=True),
    Column("independence_key", String(64), nullable=False, index=True),
    Column("source_system", String(120), nullable=False, index=True),
    Column("source_event_key", String(128), nullable=False, index=True),
    Column("source_quality", Float, nullable=False, default=0.5),
    Column("source_accuracy", Float, nullable=False, default=0.5),
    Column("observed_at", DateTime(timezone=True), nullable=True),
    Column("first_source_row_id", Integer, ForeignKey(source_rows.c.id), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "observation_key",
        "independence_key",
        name=PREFIX + "uq_observation_independence",
    ),
)

source_evidence = Table(
    PREFIX + "source_evidence",
    metadata,
    Column("source_row_id", Integer, ForeignKey(source_rows.c.id), primary_key=True),
    Column("source_file_id", Integer, ForeignKey(source_files.c.id), nullable=False, index=True),
    Column("observation_key", String(64), nullable=False, index=True),
    Column("evidence_key", String(64), nullable=False, index=True),
    Column("independence_key", String(64), nullable=False, index=True),
    Column("technical_duplicate", Boolean, nullable=False, default=False, index=True),
    Column("duplicate_of_source_row_id", Integer, ForeignKey(source_rows.c.id), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

conditional_rules = Table(
    PREFIX + "conditional_rules",
    metadata,
    Column("rule_key", String(64), primary_key=True),
    Column("ambiguity_key", String(64), nullable=False, index=True),
    Column("condition_json", Text, nullable=False),
    Column("result_json", Text, nullable=False),
    Column("status", String(24), nullable=False, default="active", index=True),
    Column(
        "generation_id",
        Integer,
        ForeignKey(knowledge_generations.c.id),
        nullable=True,
        index=True,
    ),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

mapping_explanations = Table(
    PREFIX + "mapping_explanations",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("mapping_id", Integer, ForeignKey(address_mappings.c.id), nullable=False, index=True),
    Column(
        "generation_id",
        Integer,
        ForeignKey(knowledge_generations.c.id),
        nullable=True,
        index=True,
    ),
    Column("confidence", Float, nullable=False),
    Column("explanation_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "mapping_id",
        "generation_id",
        name=PREFIX + "uq_mapping_generation_explanation",
    ),
)

source_quality_history = Table(
    PREFIX + "source_quality_history",
    metadata,
    Column("source_system", String(120), primary_key=True),
    Column("correct_count", Integer, nullable=False, default=0),
    Column("incorrect_count", Integer, nullable=False, default=0),
    Column("accuracy", Float, nullable=False, default=0.5),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

recalculation_log = Table(
    PREFIX + "recalculation_log",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("trigger_type", String(64), nullable=False, index=True),
    Column("trigger_key", Text, nullable=False),
    Column("scope", String(24), nullable=False, index=True),
    Column("reason", Text, nullable=False),
    Column("observation_count", Integer, nullable=False, default=0),
    Column("generation_id", Integer, nullable=True, index=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

quality_snapshots = Table(
    PREFIX + "quality_snapshots",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("version", String(64), nullable=False, unique=True, index=True),
    Column("status", String(24), nullable=False, index=True),
    Column("metrics_json", Text, nullable=False),
    Column("confusion_json", Text, nullable=False),
    Column("reason", Text, nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("published_at", DateTime(timezone=True), nullable=True),
)
