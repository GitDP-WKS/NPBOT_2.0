from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .db import initialize_database
from .search import load_search_index, predict


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="РЭС AI 2.0 API", version="2.0.0", lifespan=lifespan)


class PredictRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/predict")
def predict_endpoint(request: PredictRequest) -> dict:
    result = predict(request.text, index=load_search_index(), enqueue=True)
    return {
        "status": result.status,
        "confidence": result.confidence,
        "method": result.method,
        "reason": result.reason,
        "needs_review": result.needs_review,
        "model_version": result.model_version,
        "candidates": result.candidates,
    }
