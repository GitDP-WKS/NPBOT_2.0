from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.load_test_pipeline import run_pipeline  # noqa: E402


def main() -> int:
    database = Path("/tmp/res-ai-load-50000.db")
    os.environ["DATABASE_URL"] = f"sqlite:///{database}"
    os.environ.setdefault("ADMIN_PASSWORD", "load-admin")
    os.environ.setdefault("RES_AI_ALLOW_SQLITE", "1")
    os.environ.setdefault("RES_AI_DISABLE_BACKGROUND_WORKER", "1")
    if database.exists():
        database.unlink()
    output = Path("artifacts/ci/load-50000.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    result = run_pipeline(50_000)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
