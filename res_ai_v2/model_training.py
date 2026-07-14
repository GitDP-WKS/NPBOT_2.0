from __future__ import annotations

import hashlib
from collections import Counter
from typing import Any

from sklearn.metrics import classification_report, f1_score
from sklearn.model_selection import GroupKFold, cross_val_predict
from sqlalchemy import func, select

from .db import get_engine, initialize_database
from .model_data import TrainingRow, fit
from .normalize import normalize_text
from .quality_control import build_quality_metrics, quality_gate_extended
from .schema import addresses, address_mappings, text_examples
from .structure import CURRENT_STRUCTURE


def _address_text(row: Any) -> str:
    return " ".join(
        str(getattr(row, key, "") or "").strip()
        for key in ("district", "locality", "settlement", "street")
        if str(getattr(row, key, "") or "").strip()
    )


def _address_type(row: Any) -> str:
    if str(getattr(row, "settlement", "") or "").strip():
        return "территория или СНТ"
    if str(getattr(row, "street", "") or "").strip():
        return "улица"
    return "населенный пункт"


def load_training_rows() -> list[TrainingRow]:
    initialize_database()
    result: dict[tuple[str, str], TrainingRow] = {}
    with get_engine().connect() as conn:
        mapping_rows = conn.execute(
            select(
                addresses.c.address_key,
                addresses.c.locality,
                addresses.c.district,
                addresses.c.settlement,
                addresses.c.street,
                address_mappings.c.res_name,
                address_mappings.c.branch_name,
                address_mappings.c.status,
                address_mappings.c.human_confirmations,
            )
            .select_from(
                addresses.join(
                    address_mappings,
                    addresses.c.id == address_mappings.c.address_id,
                )
            )
            .where(
                address_mappings.c.active.is_(True),
                address_mappings.c.status.in_(["consistent", "human_verified", "geo_verified"]),
            )
        ).all()
        label_count = {
            str(row.address_key): int(row.count)
            for row in conn.execute(
                select(
                    addresses.c.address_key,
                    func.count(func.distinct(address_mappings.c.res_name)).label("count"),
                )
                .select_from(
                    addresses.join(
                        address_mappings,
                        addresses.c.id == address_mappings.c.address_id,
                    )
                )
                .where(address_mappings.c.active.is_(True))
                .group_by(addresses.c.address_key)
            )
        }
        for row in mapping_rows:
            if label_count.get(str(row.address_key), 0) != 1:
                continue
            text = normalize_text(_address_text(row))
            if text:
                weight = (
                    3.0
                    if str(row.status) in {"human_verified", "geo_verified"}
                    or int(row.human_confirmations or 0) > 0
                    else 1.0
                )
                result[(text, str(row.res_name))] = TrainingRow(
                    text,
                    str(row.res_name),
                    str(row.address_key),
                    weight,
                    address_type=_address_type(row),
                    source_system="working_knowledge",
                    branch=str(row.branch_name),
                )
        for row in conn.execute(
            select(text_examples).where(
                text_examples.c.status.in_(["source_only", "human_verified"])
            )
        ):
            text, label = normalize_text(str(row.raw_text)), str(row.res_name)
            if text and label in CURRENT_STRUCTURE:
                result[(text, label)] = TrainingRow(
                    text,
                    label,
                    str(row.address_id or row.example_hash),
                    max(
                        float(row.weight or 1.0),
                        3.0 if str(row.status) == "human_verified" else 1.0,
                    ),
                    address_type="текст карточки",
                    source_system="text_examples",
                    branch=str(row.branch_name),
                )
    return list(result.values())


def compute_candidate() -> dict[str, Any]:
    rows = load_training_rows()
    labels = [row.label for row in rows]
    if len(rows) < 50 or len(set(labels)) < 2:
        raise ValueError("Для обучения нужно минимум 50 примеров и два РЭС.")
    groups = [row.group for row in rows]
    splits = min(5, len(set(groups)))
    if splits < 2:
        raise ValueError("Недостаточно независимых адресных групп.")
    sample_weights = [row.weight for row in rows]
    predicted = cross_val_predict(
        fit(rows),
        [row.text for row in rows],
        labels,
        groups=groups,
        cv=GroupKFold(splits),
        method="predict",
        params={"classifier__sample_weight": sample_weights},
    )
    accuracy = sum(a == p for a, p in zip(labels, predicted)) / len(labels)
    macro = float(f1_score(labels, predicted, average="macro", zero_division=0))
    report = classification_report(labels, predicted, output_dict=True, zero_division=0)
    records = [
        {
            "expected": row.label,
            "predicted": str(value),
            "branch": row.branch or CURRENT_STRUCTURE.get(row.label, ""),
            "address_type": row.address_type,
            "source_system": row.source_system,
        }
        for row, value in zip(rows, predicted)
    ]
    segmented = build_quality_metrics(records)
    per_res = {
        label: {
            "precision": float(report[label]["precision"]),
            "recall": float(report[label]["recall"]),
            "f1": float(report[label]["f1-score"]),
            "accuracy": float(segmented["per_res"][label]["accuracy"]),
            "support": int(report[label]["support"]),
        }
        for label in set(labels)
    }
    confusion_counts = Counter(
        (a, str(p)) for a, p in zip(labels, predicted) if a != p
    )
    confusion = [
        {"expected": a, "predicted": p, "count": count}
        for (a, p), count in confusion_counts.most_common(100)
    ]
    metrics = {
        "accuracy": accuracy,
        "macro_f1": macro,
        "rows": len(rows),
        "test_rows": len(labels),
        "classes": len(set(labels)),
        "per_res": per_res,
        "per_branch": segmented["per_branch"],
        "per_address_type": segmented["per_address_type"],
        "per_source": segmented["per_source"],
        "class_support": dict(Counter(labels)),
    }
    signature = hashlib.sha256(
        "|".join(
            sorted(
                f"{row.text}|{row.label}|{row.weight}|{row.address_type}|{row.source_system}"
                for row in rows
            )
        ).encode()
    ).hexdigest()
    errors = [
        {"row": rows[index], "predicted": str(value)}
        for index, value in enumerate(predicted)
        if rows[index].label != str(value)
    ]
    return {
        "version": signature[:16],
        "signature": signature,
        "model": fit(rows),
        "metrics": metrics,
        "confusion": confusion,
        "errors": errors,
    }


def quality_gate(
    metrics: dict[str, Any],
    previous: dict[str, Any] | None,
) -> tuple[bool, list[str]]:
    passed, reasons = quality_gate_extended(metrics, previous)
    if metrics["macro_f1"] < 0.70:
        reasons.append("Macro-F1 ниже 70%")
    if previous and metrics["macro_f1"] + 0.005 < float(previous.get("macro_f1", 0)):
        reasons.append("Macro-F1 ухудшился")
    return passed and not reasons, list(dict.fromkeys(reasons))
