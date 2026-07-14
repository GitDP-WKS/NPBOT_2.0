from __future__ import annotations

import re


def short_executor_name(value: str) -> str:
    """Сокращает только отображение, не изменяя официальные значения в БД."""
    text = " ".join(str(value or "").split())
    text = re.sub(
        r"\s+район\s+электрических\s+сетей$",
        " РЭС",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\s+электрические\s+сети$",
        " ЭС",
        text,
        flags=re.IGNORECASE,
    )
    return text
