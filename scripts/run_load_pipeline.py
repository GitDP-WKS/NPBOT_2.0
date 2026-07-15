from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import load_test_pipeline as pipeline


_original_row = pipeline._row


def _row_with_unique_conflict_position(index: int, *, prefix: str, res_name: str):
    row = _original_row(index, prefix=prefix, res_name=res_name)
    if prefix == "operator-conflict":
        offset = list(pipeline.CURRENT_STRUCTURE).index(res_name)
        row["row_number"] = int(row["row_number"]) + offset
        row["record_number"] = f"{row['record_number']}-{offset}"
    return row


def run_pipeline(rows: int):
    pipeline._row = _row_with_unique_conflict_position
    return pipeline.run_pipeline(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = run_pipeline(args.rows)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
