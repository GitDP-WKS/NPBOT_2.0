from __future__ import annotations

from res_ai_v2.import_service import import_plan
from res_ai_v2.modeling import list_model_versions, publish_candidate, train_candidate
from res_ai_v2.search import predict
from tests.test_import_and_analysis import make_plan


def test_train_publish_and_predict_model(temp_db) -> None:
    rows = []
    for index in range(60):
        rows.append(
            {
                "res": "Лаишевский район электрических сетей",
                "locality": f"Лаишево Тест {index}",
                "district": "Лаишевский",
                "text": f"Лаишевский район поселок Лаишево Тест {index} отсутствует свет",
            }
        )
        rows.append(
            {
                "res": "Елабужский район электрических сетей",
                "locality": f"Елабуга Тест {index}",
                "district": "Елабужский",
                "text": f"Елабужский район поселок Елабуга Тест {index} отключение электричества",
            }
        )
    import_plan(make_plan("2" * 64, rows))
    result = train_candidate()
    assert result["metrics"]["test_rows"] >= 50
    assert result["metrics"]["accuracy"] > 0.8
    publish_candidate(result["version"], force=True)
    versions = list_model_versions()
    assert any(item["status"] == "published" for item in versions)
    prediction = predict("Елабужский район неизвестная деревня отключение электричества", index=[], enqueue=False)
    assert prediction.method == "model"
