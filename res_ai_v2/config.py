from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    admin_password: str
    model_auto_threshold: float = 0.985
    model_margin_threshold: float = 0.25
    review_votes_required: int = 3
    retrain_after_human_decisions: int = 50


def load_settings() -> Settings:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url.startswith("postgresql://"):
        database_url = "postgresql+psycopg://" + database_url.removeprefix("postgresql://")
    if not database_url:
        database_url = "sqlite:///data/res_ai_v2.db"
    return Settings(
        database_url=database_url,
        admin_password=os.getenv("ADMIN_PASSWORD", "admin"),
        model_auto_threshold=float(os.getenv("MODEL_AUTO_THRESHOLD", "0.985")),
        model_margin_threshold=float(os.getenv("MODEL_MARGIN_THRESHOLD", "0.25")),
        review_votes_required=int(os.getenv("REVIEW_VOTES_REQUIRED", "3")),
        retrain_after_human_decisions=int(os.getenv("RETRAIN_AFTER_DECISIONS", "50")),
    )
