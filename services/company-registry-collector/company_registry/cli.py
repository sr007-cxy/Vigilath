"""Command-line entry point:

    export QICHAMAO_COOKIE='...'          # credentials for the corresponding provider
    python -m company_registry.cli --provider qichamao \
        --word 代理记账 --cities 深圳 北京 --bands all --out out/daili.csv -v   # "bookkeeping agency", Shenzhen, Beijing

--provider selects the provider (default qichamao); --bands all = slice by that provider's capital bands, none = no slicing.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from .collector import Collector
from .config import Settings
from .export import write_csv, write_json
from .registry import available_providers, get_provider


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="company-registry", description="企业工商数据采集(多服务商)")
    ap.add_argument("--provider", default="qichamao",
                    help=f"数据服务商,可用: {', '.join(available_providers())}")
    ap.add_argument("--word", required=True, help="核心词,如 代理记账 / 财税咨询")
    ap.add_argument("--cities", nargs="*", default=[], help="城市切片;留空则不切城市")
    ap.add_argument("--bands", default="none", help="all=按服务商资本档 / none=不切 / 逗号分隔自定义值")
    ap.add_argument("--company-type", default=None, help="企业类型过滤;不传用服务商默认,传空串则不过滤")
    ap.add_argument("--no-detail", action="store_true", help="只抓列表层,不补电话/邮箱")
    ap.add_argument("--out", default="out/company_registry.csv", help="CSV 输出路径")
    ap.add_argument("--json", default="", help="额外 JSON 输出路径(可选)")
    ap.add_argument("--to-db", action="store_true",
                    help="落库到 sales_agent_companies / _company_contacts(需 DATABASE_URL)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")

    try:
        provider = get_provider(args.provider, Settings())
    except KeyError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 2

    if not provider.ready():
        print(f"[配置错误] provider '{args.provider}' 凭证未配置(如 Cookie 环境变量)。", file=sys.stderr)
        return 2

    col = Collector(provider, Settings())
    result = col.run(
        word=args.word,
        cities=args.cities or [""],
        bands=args.bands,
        company_type=args.company_type,
        fetch_detail=not args.no_detail,
        on_progress=lambda m: print(m, flush=True),
    )

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    write_csv(result.companies, args.out)
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)), exist_ok=True)
        write_json(result.companies, args.json)

    print("\n===== 汇总 =====")
    for k, v in result.stats().items():
        print(f"  {k}: {v}")
    print(f"  CSV: {args.out}")
    if result.aborted:
        print(f"  [!] 中止原因: {result.abort_reason}")

    if args.to_db:
        from .sink import DbSink
        try:
            stats = DbSink().upsert(result.companies)
            print("  落库 sales_agent_*: " + str(stats.as_dict()))
        except Exception as e:
            print(f"  [!] 落库失败: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
