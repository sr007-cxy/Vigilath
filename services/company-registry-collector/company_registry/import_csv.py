"""Persist to DB from an already exported CSV, avoiding a re-crawl.

    export DATABASE_URL='postgresql+psycopg://user:pw@localhost:5432/vigilath'
    python -m company_registry.import_csv out/daili_sz.csv [--all]

Reads back the CSV produced by export.write_csv, rebuilds Company, and calls DbSink to write into
sales_agent_companies / sales_agent_company_contacts. By default only "target companies" are persisted
(government/school types excluded); add --all to disable filtering.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from typing import List

from .models import Company


def load_csv(path: str) -> List[Company]:
    companies: List[Company] = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            emails = [e for e in (row.get("emails") or "").split(";") if e]
            companies.append(Company(
                id=row.get("id", ""),
                source=row.get("source", ""),
                name=row.get("name", ""),
                city=row.get("city", ""),
                band=row.get("band", ""),
                legal_rep=row.get("legal_rep", ""),
                capital=row.get("capital", ""),
                establish=row.get("establish", ""),
                status=row.get("status", ""),
                industry=row.get("industry", ""),
                phone=row.get("phone", ""),
                emails=emails,
                address=row.get("address", ""),
                credit_code=row.get("credit_code", ""),
                detail_url=row.get("detail_url", ""),
            ))
    return companies


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="import_csv", description="从 CSV 落库到 sales_agent_* 表")
    ap.add_argument("csv_path", help="export 产出的 CSV 路径")
    ap.add_argument("--all", action="store_true", help="连非目标企业(政府/学校类)也落库")
    ap.add_argument("--database-url", default="", help="覆盖 DATABASE_URL 环境变量")
    args = ap.parse_args(argv)

    if not os.path.exists(args.csv_path):
        print(f"[错误] 文件不存在: {args.csv_path}", file=sys.stderr)
        return 2

    companies = load_csv(args.csv_path)
    print(f"读入 {len(companies)} 家(来自 {args.csv_path})")

    try:
        from .sink import DbSink
    except ModuleNotFoundError as e:
        print(f"[错误] 落库需要 SQLAlchemy: {e}(pip install -r requirements.txt)", file=sys.stderr)
        return 1

    try:
        sink = DbSink(args.database_url or None)
        stats = sink.upsert(companies, only_targets=not args.all)
    except Exception as e:
        print(f"[落库失败] {e}", file=sys.stderr)
        return 1

    print("落库完成:", stats.as_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
