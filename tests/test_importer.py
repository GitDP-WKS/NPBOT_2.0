from __future__ import annotations

import io

import pandas as pd

from res_ai_v2.importer import inspect_excel


def workbook_bytes() -> bytes:
    buffer = io.BytesIO()
    df = pd.DataFrame(
        [
            {
                "Произвольный исполнитель": "Лаишевский РЭС",
                "Населенный пункт / город": "Усады",
                "Муниципальный район": "Лаишевский",
                "Не тот филиал": "Казанские электрические сети",
                "Описание сообщения": "В селе Усады нет электричества",
            }
        ]
    )
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Данные", index=False)
    return buffer.getvalue()


def test_intelligent_column_detection_and_executor_canonicalization() -> None:
    plan = inspect_excel(workbook_bytes(), "test.xlsx")
    assert plan.detected_rows == 1
    row = plan.sheets[0].rows[0]
    assert row["locality"] == "Усады"
    assert row["district"] == "Лаишевский"
    assert row["res"] == "Лаишевский район электрических сетей"
    assert row["branch"] == "Приволжские сети"
    assert row["text"] == "В селе Усады нет электричества"
