from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from typing import Any

_SPACE = re.compile(r"\s+")
_WORD = re.compile(r"[а-яa-z0-9]+", re.IGNORECASE)

GENERIC_WORDS = {
    "республика", "татарстан", "район", "муниципальный", "город", "село",
    "деревня", "поселок", "посёлок", "пгт", "снт", "улица", "ул", "дом",
    "корпус", "квартира", "обращение", "адрес",
}


def clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ").replace("\u200b", " ")
    return _SPACE.sub(" ", text).strip()


def normalize_text(value: Any) -> str:
    text = clean(value).lower().replace("ё", "е")
    return " ".join(_WORD.findall(text))


def normalize_entity(value: Any) -> str:
    words = [word for word in normalize_text(value).split() if word not in GENERIC_WORDS]
    return " ".join(words)


def sha256_parts(parts: Iterable[Any]) -> str:
    normalized = "|".join(normalize_text(part) for part in parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def row_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def whole_phrase_in(phrase: str, text: str) -> bool:
    phrase = normalize_text(phrase)
    text = normalize_text(text)
    if not phrase or not text:
        return False
    return re.search(rf"(^|\s){re.escape(phrase)}($|\s)", text) is not None
