#!/usr/bin/env python3
"""批量运行 TA 分析，逐只发送到飞书。"""
import os, sys, json, time, asyncio, sqlite3
from datetime import date, datetime
from pathlib import Path

# Load env
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

import requests

# ── Config ──────────────────────────────────────────────────────────
TODAY = date.today().strftime("%Y-%m-%d")
RESULTS_DIR = Path(__file__).resolve().parent.parent / "analysis_results" / TODAY
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

TA_BASE = "http://localhost:8000"
FEISHU_WEBHOOK = os.getenv("FEISHU_WEBHOOK", "")  # optional

# ZhiPu Coding Plan config
ZHIPU_CONFIG = {
    "llm_provider": "openai",
    "backend_url": "https://open.bigmodel.cn/api/coding/paas/v4",
    "quick_think_llm": "glm-4.5-air",
    "deep_think_llm": "glm-5-turbo",
}

# Get watchlist
def get_watchlist():
    db_path = Path(__file__).resolve().parent.parent / "tradingagents.db"
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM watchlist_items ORDER BY symbol"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


# Create API token
def create_api_token():
    from api.services.token_service import create_token
    from api.database import get_db_ctx
    with get_db_ctx() as db:
        result = create_token(db, "78b96789-9f22-47f2-b744-fb0f26a19060", "batch-run")
        return result["token"]


# Submit analysis via API
def submit_analysis(symbol, token):
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "symbol": symbol,
        "trade_date": TODAY,
        "config_overrides": ZHIPU_CONFIG,
    }
    r = requests.post(f"{TA_BASE}/v1/analyze", json=payload, headers=headers, timeout=30)
    if r.status_code == 200:
        data = r.json()
        return data.get("job_id") or data.get("id")
    else:
        print(f"  ❌ Submit failed ({r.status_code}): {r.text[:200]}")
        return None


# Poll job status
def poll_job(job_id, token, max_wait=1200):
    headers = {"Authorization": f"Bearer {token}"}
    start = time.time()
    while time.time() - start < max_wait:
        r = requests.get(f"{TA_BASE}/v1/jobs/{job_id}", headers=headers, timeout=10)
        if r.status_code == 200:
            status = r.json()
            if status.get("status") in ("completed", "failed", "error"):
                return status
        time.sleep(30)
    return {"status": "timeout"}


# Get job result
def get_result(job_id, token):
    headers = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{TA_BASE}/v1/jobs/{job_id}/result", headers=headers, timeout=30)
    if r.status_code == 200:
        return r.json()
    return None


# Save result to file
def save_result(symbol, result):
    filename = symbol.replace(".", "_") + ".json"
    filepath = RESULTS_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return filepath


# Format report for Feishu
def format_feishu_report(symbol, result):
    verdict = result.get("final_verdict", result.get("verdict", "N/A"))
    direction = verdict.get("direction", "N/A") if isinstance(verdict, dict) else str(verdict)
    reason = verdict.get("reason", "") if isinstance(verdict, dict) else ""

    lines = [
        f"📊 **{symbol}** 分析报告 ({TODAY})",
        f"**方向**: {direction}",
        f"**理由**: {reason}",
        "",
    ]

    # Add key sections
    for key in ["risk_judge", "trader_decision", "research_summary"]:
        if key in result and result[key]:
            section = result[key]
            if isinstance(section, str):
                lines.append(f"**{key}**: {section[:300]}")
                lines.append("")

    return "\n".join(lines)


# Send to Feishu via message tool (called from OpenClaw)
def send_to_feishu(symbol, report_text):
    """Save report for OpenClaw to send."""
    filename = symbol.replace(".", "_") + ".md"
    filepath = RESULTS_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(report_text)
    return filepath


# ── Main ────────────────────────────────────────────────────────────
def main():
    symbols = get_watchlist()
    print(f"📋 自选股: {len(symbols)} 只")
    print(f"📅 日期: {TODAY}")
    print(f"📁 结果目录: {RESULTS_DIR}")
    print()

    # Create token
    try:
        token = create_api_token()
        print(f"🔑 API Token 已创建")
    except Exception as e:
        print(f"❌ Token 创建失败: {e}")
        return

    results_summary = []

    for i, symbol in enumerate(symbols, 1):
        print(f"[{i}/{len(symbols)}] 🔄 {symbol} ...")

        try:
            job_id = submit_analysis(symbol, token)
            if not job_id:
                results_summary.append({"symbol": symbol, "status": "submit_failed"})
                continue

            print(f"  📤 Job: {job_id}")
            status = poll_job(job_id, token, max_wait=1200)

            if status.get("status") == "completed":
                result = get_result(job_id, token)
                if result:
                    save_result(symbol, result)
                    report = format_feishu_report(symbol, result)
                    send_to_feishu(symbol, report)
                    results_summary.append({"symbol": symbol, "status": "ok", "job_id": job_id})
                    print(f"  ✅ 完成")
                else:
                    results_summary.append({"symbol": symbol, "status": "no_result"})
                    print(f"  ⚠️ 无结果")
            else:
                results_summary.append({"symbol": symbol, "status": status.get("status", "unknown")})
                print(f"  ❌ 状态: {status.get('status')}")

        except Exception as e:
            results_summary.append({"symbol": symbol, "status": "error", "error": str(e)})
            print(f"  ❌ 错误: {e}")

        # Brief pause between submissions
        time.sleep(5)

    # Save summary
    summary_path = RESULTS_DIR / "_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(results_summary, f, ensure_ascii=False, indent=2)

    ok_count = sum(1 for r in results_summary if r["status"] == "ok")
    print(f"\n{'='*50}")
    print(f"✅ 完成: {ok_count}/{len(symbols)}")
    print(f"📁 结果保存在: {RESULTS_DIR}")

    return results_summary


if __name__ == "__main__":
    main()
