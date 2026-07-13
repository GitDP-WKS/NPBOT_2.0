from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion, Pipeline


@dataclass
class TrainingRow:
    text: str
    label: str
    group: str
    weight: float


def pipeline() -> Pipeline:
    features = FeatureUnion([("word", TfidfVectorizer(ngram_range=(1, 2), min_df=1, max_features=120_000, sublinear_tf=True)), ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 6), min_df=1, max_features=180_000, sublinear_tf=True))])
    return Pipeline([("features", features), ("classifier", LogisticRegression(max_iter=2500, class_weight="balanced", C=3.0))])


def fit(rows: list[TrainingRow]) -> Pipeline:
    model = pipeline(); model.fit([row.text for row in rows], [row.label for row in rows], classifier__sample_weight=np.array([row.weight for row in rows])); return model


def dump_model(model: Pipeline) -> bytes:
    stream = io.BytesIO(); joblib.dump(model, stream, compress=3); return stream.getvalue()


def load_model(value: bytes) -> Pipeline:
    return joblib.load(io.BytesIO(value))


def predict_options(model: Pipeline, text: str, top_n: int = 3) -> list[dict[str, Any]]:
    probabilities = model.predict_proba([text])[0]; classes = model.classes_; order = np.argsort(probabilities)[::-1][:top_n]
    return [{"res": str(classes[index]), "probability": float(probabilities[index])} for index in order]
