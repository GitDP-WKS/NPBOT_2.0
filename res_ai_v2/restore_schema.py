from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, String, Table, Text

from .schema import PREFIX, metadata

restore_requests = Table(
    PREFIX + "restore_requests",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("actor", Text, nullable=False),
    Column("snapshot_json", Text, nullable=False),
    Column("status", String(24), nullable=False, default="pending", index=True),
    Column("result_json", Text, nullable=False, default="{}"),
    Column("error_text", Text, nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=True),
)
