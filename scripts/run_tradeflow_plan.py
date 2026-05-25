#!/usr/bin/env python3
"""TradeFlow CLI — generate pre-market plans.

Usage:
    python scripts/run_tradeflow_plan.py --date 2026-05-25
    python scripts/run_tradeflow_plan.py --symbols 002353.SZ,603256.SH
"""

import argparse
import os
import sys

# Ensure project root is on path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from tradingagents.tradeflow.candidate_engine import init_db, evaluate_symbol
from tradingagents.tradeflow.plan_runner import generate_daily_plan, save_plan


def main():
    parser = argparse.ArgumentParser(description="TradeFlow Pre-Market Plan Generator")
    parser.add_argument("--date", default=None, help="Trade date (YYYY-MM-DD), default: today")
    parser.add_argument("--symbols", default=None, help="Comma-separated symbols to evaluate")
    parser.add_argument("--db", default=None, help="Tradeflow DB path (default: tradeflow.db in project root)")
    parser.add_argument("--save-candidates", action="store_true", help="Save evaluated candidates to DB")
    parser.add_argument("--news", default=None, help="Comma-separated news texts for event catalyst detection")
    args = parser.parse_args()

    trade_date = args.date
    symbols = args.symbols.split(",") if args.symbols else None
    news_texts = [t.strip() for t in args.news.split(",") if t.strip()] if args.news else None

    # DB path
    if args.db:
        tf_db = args.db
    else:
        tf_db = os.path.join(project_root, "tradeflow.db")

    # Init tables
    init_db(tf_db)

    prod_db = os.path.join(project_root, "tradingagents.db")

    print(f"🚀 TradeFlow 盘前计划生成器")
    print(f"   日期: {trade_date or '今天'}")
    print(f"   标的: {symbols or '全量宇宙'}")
    print()

    # Evaluate symbols if specified
    candidates = []
    if symbols:
        for sym in symbols:
            print(f"  📊 评估 {sym} ...")
            c, reason = evaluate_symbol(
                symbol=sym,
                trade_date=trade_date,
                news_texts=news_texts,
            )
            if c is not None:
                candidates.append(c)
                print(f"     ✅ 命中策略: {c.strategy_tags} (score={c.score})")
                if c.need_deep_ta:
                    print(f"     ⚠️ 需要深度 TA 分析")
            else:
                print(f"     ⏭️ 被过滤：{reason}")

    # Generate plan
    plan = generate_daily_plan(
        trade_date=trade_date,
        symbols=symbols,
        tf_db_path=tf_db,
        prod_db_path=prod_db,
        candidates=candidates if symbols else None,
        news_texts=news_texts,
        save_candidates=args.save_candidates or bool(symbols),
    )

    # Save plan
    save_plan(plan, tf_db)

    # Render
    print()
    print(plan.render_text())
    print()
    print(f"📋 计划已保存到 {tf_db}")

    return plan


if __name__ == "__main__":
    main()
