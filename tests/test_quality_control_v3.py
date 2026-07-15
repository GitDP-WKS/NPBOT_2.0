from __future__ import annotations

import pytest
from sqlalchemy import insert

from res_ai_v2.db import get_engine, utcnow
from res_ai_v2.modeling import compare_model_versions, publish_candidate, rollback_model
from res_ai_v2.normalize import stable_json
from res_ai_v2.quality_control import build_quality_metrics, quality_gate_extended
from res_ai_v2.schema import model_versions


def test_quality_metrics_include_all_required_segments() -> None:
    records = [
        {
            "expected": "РЭС 1",
            "predicted": "РЭС 1",
            "branch": "Филиал 1",
            "address_type": "СНТ",
            "source_system": "112",
        },
        {
            "expected": "РЭС 2",
            "predicted": "РЭС 1",
            "branch": "Филиал 2",
            "address_type": "дом",
            "source_system": "реестр",
        },
    ]
    metrics = build_quality_metrics(records)
    assert metrics["accuracy"] == 0.5
    assert set(metrics["per_res"]) == {"РЭС 1", "РЭС 2"}
    assert set(metrics["per_branch"]) == {"Филиал 1", "Филиал 2"}
    assert set(metrics["per_address_type"]) == {"СНТ", "дом"}
    assert set(metrics["per_source"]) == {"112", "реестр"}
    assert metrics["confusion"] == [
        {"expected": "РЭС 2", "predicted": "РЭС 1", "count": 1}
    ]


def test_segment_regression_blocks_version_even_when_total_is_high() -> None:
    previous = {
        "accuracy": 0.95,
        "per_res": {"РЭС 1": {"accuracy": 0.95, "support": 20}},
        "per_branch": {"Филиал 1": {"accuracy": 0.95, "support": 20}},
        "per_address_type": {"СНТ": {"accuracy": 0.95, "support": 20}},
        "per_source": {"112": {"accuracy": 0.95, "support": 20}},
    }
    current = {
        "accuracy": 0.95,
        "per_res": {"РЭС 1": {"accuracy": 0.95, "support": 20}},
        "per_branch": {"Филиал 1": {"accuracy": 0.70, "support": 20}},
        "per_address_type": {"СНТ": {"accuracy": 0.95, "support": 20}},
        "per_source": {"112": {"accuracy": 0.95, "support": 20}},
    }
    passed, reasons = quality_gate_extended(current, previous)
    assert passed is False
    assert any("филиалу" in reason for reason in reasons)


def _model(version: str, *, gate_passed: bool, status: str, accuracy: float) -> dict:
    metrics = {
        "accuracy": accuracy,
        "macro_f1": accuracy,
        "per_res": {},
        "per_branch": {},
        "per_address_type": {},
        "per_source": {},
        "gate_reasons": [] if gate_passed else ["Ухудшилось качество"],
    }
    return {
        "version": version,
        "status": status,
        "algorithm": "test",
        "training_signature": version,
        "metrics_json": stable_json(metrics),
        "confusion_json": "[]",
        "model_blob": b"test",
        "gate_passed": gate_passed,
        "created_at": utcnow(),
        "published_at": utcnow() if status == "published" else None,
    }


def test_failed_candidate_cannot_be_forced_to_publish(temp_db) -> None:
    with get_engine().begin() as conn:
        conn.execute(insert(model_versions).values(**_model("bad", gate_passed=False, status="candidate", accuracy=0.7)))
    with pytest.raises(ValueError, match="Публикация запрещена"):
        publish_candidate("bad", force=True)


def test_rollback_is_allowed_only_to_confirmed_version(temp_db) -> None:
    with get_engine().begin() as conn:
        conn.execute(insert(model_versions).values(**_model("good", gate_passed=True, status="published", accuracy=0.9)))
        conn.execute(insert(model_versions).values(**_model("bad", gate_passed=False, status="archived", accuracy=0.7)))
    with pytest.raises(ValueError, match="подтвержденной"):
        rollback_model("bad")


def test_versions_can_be_compared(temp_db) -> None:
    with get_engine().begin() as conn:
        conn.execute(insert(model_versions).values(**_model("v1", gate_passed=True, status="archived", accuracy=0.8)))
        conn.execute(insert(model_versions).values(**_model("v2", gate_passed=True, status="published", accuracy=0.9)))
    comparison = compare_model_versions("v1", "v2")
    assert comparison["accuracy_delta"] == pytest.approx(0.1)
    assert comparison["macro_f1_delta"] == pytest.approx(0.1)
