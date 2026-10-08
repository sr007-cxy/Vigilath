"""Export: CSV / JSON."""
from __future__ import annotations

import csv
import json
from typing import Iterable

from .models import CSV_COLUMNS, Company


def write_csv(companies: Iterable[Company], path: str) -> str:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for c in companies:
            row = c.to_row()
            w.writerow({k: row.get(k, "") for k in CSV_COLUMNS})
    return path


def write_json(companies: Iterable[Company], path: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([c.to_row() for c in companies], f, ensure_ascii=False, indent=2)
    return path
