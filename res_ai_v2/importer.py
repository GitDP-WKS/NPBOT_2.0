from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

import pandas as pd

from .normalize import clean, normalize_text, sha256_parts
from .structure import BRANCH_ALIASES, CURRENT_STRUCTURE, canonical_executor, normalize_res

FIELD_LABELS = {
    "branch": "Филиал",
    "res": "РЭС",
    "locality": "Населенный пункт",
    "district": "Район",
    "settlement": "СНТ / поселок",
    "street": "Улица",
    "text": "Исходный текст",
    "record_number": "Номер записи",
}

ALIASES: dict[str, set[str]] = {
    "branch": {"филиал", "branch", "электрические сети", "эс", "наименование филиала"},
    "res": {"рэс", "рес", "исполнитель", "подразделение", "район электрических сетей", "res"},
    "locality": {
        "населенный пункт", "населённый пункт", "н п", "нп", "город пгт", "город", "locality",
        "населенный пункт город", "город село деревня",
    },
    "district": {"район", "муниципальный район", "административный район", "district"},
    "settlement": {
        "снт поселок", "снт", "поселок", "посёлок", "мкр поселки", "мкр", "территория",
        "territory", "садоводческое товарищество",
    },
    "street": {"улица", "street", "ул", "наименование улицы"},
    "text": {
        "текст", "текст обращения", "адрес или текст", "адрес", "суть", "сообщение", "описание",
        "текст обращения адрес", "исходный текст", "appeal text",
    },
    "record_number": {"номер", "номер обращения", "№ обращения", "id", "record number"},
}


@dataclass
class SheetPlan:
    sheet_name: str
    header_row: int
    columns: dict[str, str]
    confidence: dict[str, float]
    all_columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ImportPlan:
    file_hash: str
    file_name: str
    source_kind: str
    sheets: list[SheetPlan]
    detected_rows: int
    warnings: list[str]

    @property
    def detected_columns(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for sheet in self.sheets:
            for source, target in sheet.columns.items():
                result.setdefault(source, target)
        return result


def file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _header_score(value: str, target: str) -> float:
    value_key = normalize_text(value)
    if not value_key:
        return 0.0
    aliases = ALIASES[target]
    exact = 1.0 if value_key in aliases else 0.0
    fuzzy = max(SequenceMatcher(None, value_key, alias).ratio() for alias in aliases)
    contains = 0.96 if any(alias in value_key or value_key in alias for alias in aliases if len(alias) >= 3) else 0.0
    return max(exact, fuzzy, contains)


def _value_profile(series: pd.Series) -> dict[str, float]:
    values = [clean(value) for value in series.head(80).tolist() if clean(value)]
    if not values:
        return {"res": 0.0, "branch": 0.0, "text": 0.0, "district": 0.0, "settlement": 0.0, "street": 0.0}
    res_hits = sum(1 for value in values if normalize_res(value) in CURRENT_STRUCTURE)
    branch_keys = {normalize_text(key) for key in BRANCH_ALIASES} | {normalize_text(value) for value in CURRENT_STRUCTURE.values()}
    branch_hits = sum(1 for value in values if normalize_text(value) in branch_keys)
    avg_len = sum(len(value) for value in values) / len(values)
    sentence_hits = sum(1 for value in values if len(value) >= 45 or len(value.split()) >= 8)
    district_hits = sum(1 for value in values if "район" in normalize_text(value) or normalize_text(value).endswith("ский"))
    settlement_hits = sum(1 for value in values if any(token in normalize_text(value) for token in ("снт", "посел", "мкр", "территор")))
    street_hits = sum(1 for value in values if any(token in normalize_text(value) for token in ("улиц", "ул ", "переул", "просп")))
    count = len(values)
    return {
        "res": res_hits / count,
        "branch": branch_hits / count,
        "text": max(sentence_hits / count, min(1.0, avg_len / 120.0)),
        "district": district_hits / count,
        "settlement": settlement_hits / count,
        "street": street_hits / count,
    }


def _detect_columns(df: pd.DataFrame) -> tuple[dict[str, str], dict[str, float]]:
    candidates: list[tuple[float, str, str]] = []
    for column in df.columns:
        source = str(column)
        profile = _value_profile(df[column])
        for target in FIELD_LABELS:
            score = _header_score(source, target)
            if target in profile:
                score = max(score, profile[target] * 0.92)
            if target == "record_number":
                numeric_ratio = pd.to_numeric(df[column].head(80), errors="coerce").notna().mean()
                score = max(score, float(numeric_ratio) * 0.6)
            candidates.append((score, source, target))

    candidates.sort(reverse=True)
    used_sources: set[str] = set()
    used_targets: set[str] = set()
    mapping: dict[str, str] = {}
    confidence: dict[str, float] = {}
    for score, source, target in candidates:
        threshold = 0.72 if target in {"branch", "res", "locality", "district", "settlement", "street"} else 0.66
        if score < threshold or source in used_sources or target in used_targets:
            continue
        mapping[source] = target
        confidence[target] = round(score, 3)
        used_sources.add(source)
        used_targets.add(target)
    return mapping, confidence


def _find_header(book: pd.ExcelFile, sheet_name: str) -> int:
    preview = pd.read_excel(book, sheet_name=sheet_name, header=None, nrows=25, dtype=object).fillna("")
    best_row = 0
    best_score = -1.0
    for row_number, row in preview.iterrows():
        values = [clean(value) for value in row.tolist()]
        fake = pd.DataFrame(columns=values)
        score = 0.0
        for column in fake.columns:
            score += max(_header_score(str(column), target) for target in FIELD_LABELS)
        if score > best_score:
            best_score = score
            best_row = int(row_number)
    return best_row


def _extract_rows(df: pd.DataFrame, mapping: dict[str, str], sheet_name: str, header_row: int) -> list[dict[str, Any]]:
    renamed = df.rename(columns=mapping)
    rows: list[dict[str, Any]] = []
    for offset, raw in enumerate(renamed.to_dict("records"), start=header_row + 2):
        original = {str(column): clean(value) for column, value in raw.items()}
        canonical = {field: clean(raw.get(field, "")) for field in FIELD_LABELS}
        branch, res, known_res = canonical_executor(canonical.get("branch"), canonical.get("res"))
        canonical["branch"] = branch
        canonical["res"] = res
        canonical["known_res"] = known_res
        canonical["sheet_name"] = sheet_name
        canonical["row_number"] = offset
        canonical["raw"] = original

        has_address = bool(canonical["locality"] or canonical["settlement"])
        has_text = bool(canonical["text"])
        if not (has_address or has_text or canonical["res"] or canonical["branch"]):
            continue
        rows.append(canonical)
    return rows


def _source_kind(sheets: list[SheetPlan]) -> str:
    address_rows = 0
    text_rows = 0
    for sheet in sheets:
        for row in sheet.rows:
            if row.get("locality") or row.get("settlement"):
                address_rows += 1
            if row.get("text"):
                text_rows += 1
    if address_rows and text_rows:
        return "mixed"
    if text_rows:
        return "labeled_texts"
    return "address_registry"


def inspect_excel(content: bytes, file_name: str, overrides: dict[str, dict[str, str]] | None = None) -> ImportPlan:
    book = pd.ExcelFile(io.BytesIO(content))
    sheets: list[SheetPlan] = []
    warnings: list[str] = []
    total = 0
    for sheet_name in book.sheet_names:
        header_row = _find_header(book, sheet_name)
        df = pd.read_excel(book, sheet_name=sheet_name, header=header_row, dtype=object).fillna("")
        mapping, confidence = _detect_columns(df)
        if overrides and sheet_name in overrides:
            mapping = {source: target for source, target in overrides[sheet_name].items() if target}
            confidence = {target: 1.0 for target in mapping.values()}
        sheet_warnings: list[str] = []
        if "res" not in mapping.values():
            sheet_warnings.append("Не найден столбец РЭС.")
        if not ({"locality", "settlement", "text"} & set(mapping.values())):
            sheet_warnings.append("Не найден адресный или текстовый столбец.")
        rows = _extract_rows(df, mapping, sheet_name, header_row)
        if not rows:
            continue
        total += len(rows)
        sheets.append(
            SheetPlan(
                sheet_name=sheet_name,
                header_row=header_row,
                columns=mapping,
                confidence=confidence,
                all_columns=[str(column) for column in df.columns],
                rows=rows,
                warnings=sheet_warnings,
            )
        )
        warnings.extend(f"{sheet_name}: {warning}" for warning in sheet_warnings)

    if not sheets or total == 0:
        raise ValueError("Не удалось распознать строки ни на одном листе Excel.")
    return ImportPlan(
        file_hash=file_hash(content),
        file_name=file_name,
        source_kind=_source_kind(sheets),
        sheets=sheets,
        detected_rows=total,
        warnings=warnings,
    )


def override_sheet_mapping(plan: ImportPlan, sheet_name: str, mapping: dict[str, str]) -> ImportPlan:
    book_rows = next((sheet for sheet in plan.sheets if sheet.sheet_name == sheet_name), None)
    if book_rows is None:
        return plan
    book_rows.columns = {source: target for source, target in mapping.items() if target}
    return plan


def canonical_row_key(row: dict[str, Any]) -> str:
    return sha256_parts(
        [
            row.get("branch", ""),
            row.get("res", ""),
            row.get("locality", ""),
            row.get("district", ""),
            row.get("settlement", ""),
            row.get("street", ""),
            row.get("text", ""),
        ]
    )
