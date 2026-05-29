#!/usr/bin/env python3
"""TradeFlow CLI — generate pre-market plans and run discovery scans.

Usage:
    python scripts/run_tradeflow_plan.py --date 2026-05-25
    python scripts/run_tradeflow_plan.py --symbols 002353.SZ,603256.SH
    python scripts/run_tradeflow_plan.py --discover --industry-symbols 000001.SZ,600519.SH,601318.SH
    python scripts/run_tradeflow_plan.py --discover --symbols 002353.SZ,603256.SH --top-n 10
    python scripts/run_tradeflow_plan.py --discover --use-event-source --top-n 20
"""

import argparse
import os
import sys

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
    parser.add_argument("--discover", action="store_true", help="Run discovery scan instead of regular plan")  # [T-002]
    parser.add_argument("--industry-symbols", default=None, help="Comma-separated industry pilot symbols for discovery")  # [T-002]
    parser.add_argument("--top-n", type=int, default=20, help="Top N candidates to return in discovery mode")  # [T-002]
    parser.add_argument("--use-event-source", action="store_true", help="Fetch daily events and inject into universe")  # [T-002]
    parser.add_argument("--no-holdings", action="store_true", help="Exclude holdings from universe")  # [T-002]
    parser.add_argument("--no-watchlist", action="store_true", help="Exclude watchlist from universe")  # [T-002]
    args = parser.parse_args()

    trade_date = args.date
    symbols = args.symbols.split(",") if args.symbols else None
    news_texts = [t.strip() for t in args.news.split(",") if t.strip()] if args.news else None

    if args.db:
        tf_db = args.db
    else:
        tf_db = os.path.join(project_root, "tradeflow.db")

    init_db(tf_db)

    prod_db = os.path.join(project_root, "tradingagents.db")

    if args.discover:
        _run_discovery(
            trade_date=trade_date,
            symbols=symbols,
            industry_symbols=args.industry_symbols.split(",") if args.industry_symbols else None,
            tf_db=tf_db,
            prod_db=prod_db,
            top_n=args.top_n,
            include_holdings=not args.no_holdings,
            include_watchlist=not args.no_watchlist,
            news_texts=news_texts,
            use_event_source=args.use_event_source,
            save_candidates=args.save_candidates,
        )
    else:
        _run_plan(
            trade_date=trade_date,
            symbols=symbols,
            tf_db=tf_db,
            prod_db=prod_db,
            news_texts=news_texts,
            save_candidates=args.save_candidates,
            use_event_source=args.use_event_source,
        )


def _run_plan(trade_date, symbols, tf_db, prod_db, news_texts, save_candidates, use_event_source):
    print(f"🚀 TradeFlow 盘前计划生成器")
    print(f"   日期: {trade_date or '今天'}")
    print(f"   标的: {symbols or '全量宇宙'}")
    print()

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

    plan = generate_daily_plan(
        trade_date=trade_date,
        symbols=symbols,
        tf_db_path=tf_db,
        prod_db_path=prod_db,
        candidates=candidates if symbols else None,
        news_texts=news_texts,
        save_candidates=save_candidates or bool(symbols),
        use_event_source=use_event_source,
    )

    save_plan(plan, tf_db)

    print()
    print(plan.render_text())
    print()
    print(f"📋 计划已保存到 {tf_db}")

    return plan


def _run_discovery(trade_date, symbols, industry_symbols, tf_db, prod_db, top_n,
                   include_holdings, include_watchlist, news_texts,
                   use_event_source, save_candidates):
    from tradingagents.tradeflow.discovery import run_discovery, render_discovery_text

    print(f"🔍 TradeFlow Discovery 扫描")
    print(f"   日期: {trade_date or '今天'}")
    print(f"   手动标的: {symbols or '无'}")
    print(f"   行业池标的: {industry_symbols or '无'}")
    print(f"   TopN: {top_n}")
    print(f"   事件源: {'开启' if use_event_source else '关闭'}")
    print()

    result = run_discovery(
        trade_date=trade_date,
        symbols=symbols,
        industry_symbols=industry_symbols,
        prod_db_path=prod_db,
        tf_db_path=tf_db,
        top_n=top_n,
        include_holdings=include_holdings,
        include_watchlist=include_watchlist,
        news_texts=news_texts,
        use_event_source=use_event_source,
        save_candidates=save_candidates,
    )

    print(render_discovery_text(result))
    print()

    if save_candidates:
        print(f"📋 候选已保存到 {tf_db}")

    return result


if __name__ == "__main__":
    main()
