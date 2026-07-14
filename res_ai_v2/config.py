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


def _read_secret(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        import streamlit as st

        secret = st.secrets.get(name, "")
        return str(secret).strip() if secret else ""
    except Exception:
        return ""


def load_settings() -> Settings:
    database_url = _read_secret("DATABASE_URL")
    if database_url.startswith("postgresql://"):
        database_url = "postgresql+psycopg://" + database_url.removeprefix("postgresql://")

    allow_local = os.getenv("RES_AI_ALLOW_SQLITE", "").strip() == "1" or bool(os.getenv("PYTEST_CURRENT_TEST"))
    if not database_url:
        if allow_local:
            database_url = "sqlite:///data/res_ai_v2.db"
        else:
            raise RuntimeError(
                "Общая база Neon не подключена. Добавьте DATABASE_URL в Streamlit Secrets. "
                "Работа с локальной базой отключена, чтобы данные не расходились между компьютерами."
            )

    return Settings(
        database_url=database_url,
        admin_password=_read_secret("ADMIN_PASSWORD") or "admin",
        model_auto_threshold=float(os.getenv("MODEL_AUTO_THRESHOLD", "0.985")),
        model_margin_threshold=float(os.getenv("MODEL_MARGIN_THRESHOLD", "0.25")),
        review_votes_required=int(os.getenv("REVIEW_VOTES_REQUIRED", "3")),
        retrain_after_human_decisions=int(os.getenv("RETRAIN_AFTER_DECISIONS", "50")),
    )
