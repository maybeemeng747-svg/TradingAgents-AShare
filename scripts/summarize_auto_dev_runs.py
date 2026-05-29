#!/usr/bin/env python3
# [M-002] auto_dev_report_index
# [V-002] nightly_acceptance_report
"""
Scan docs/task_runs/ and generate a nightly dev report in docs/auto_dev_reports/YYYY-MM-DD.md.

V-002 extends M-002 with:
  - Candidate sample replay (VCP, event catalyst, fund flow, liquidity, no-strategy, risk)
  - Ready queue status from TASKS.md
  - Test results summary from test log files
  - "任务池不足" warning when ready queue is empty

Usage:
    python scripts/summarize_auto_dev_runs.py [--date YYYY-MM-DD] [--dry-run] [--repo-dir PATH]
                                              [--with-sample-replay]

Constraints:
    - Read-only: only reads docs/task_runs/, docs/reviews/, git log, TASKS.md
    - No model calls, no stock analysis
    - No API keys or sensitive logs in output
    - Redacts any leaked keys from included content
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


_REDACT_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "[REDACTED_API_KEY]"),
    (re.compile(r"(api[_-]?key[=:]\s*)\S+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(authorization:\s*bearer\s+)\S+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(token[=:]\s*)\S+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(password[=:]\s*)\S+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(secret[=:]\s*)\S+", re.IGNORECASE), r"\1[REDACTED]"),
]


def redact(text: str) -> str:
    for pat, repl in _REDACT_PATTERNS:
        text = pat.sub(repl, text)
    return text


# ── [V-002] nightly_acceptance_report: Ready queue parsing ──

def parse_ready_queue(tasks_md_path: Path) -> list[dict[str, str]]:
    """Parse docs/TASKS.md to extract tasks with status=ready.

    Returns list of dicts with keys: task_id, title, priority.
    """
    if not tasks_md_path.exists():
        return []

    ready_tasks: list[dict[str, str]] = []
    text = tasks_md_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    current_task_id = ""
    current_title = ""
    current_priority = ""
    current_status = ""

    for line in lines:
        header_match = re.match(r"^###\s+([A-Z]+-\d+)\s*:\s*(.+)$", line)
        if header_match:
            if current_task_id and current_status == "ready":
                ready_tasks.append({
                    "task_id": current_task_id,
                    "title": current_title.strip(),
                    "priority": current_priority,
                })
            current_task_id = header_match.group(1)
            current_title = header_match.group(2)
            current_priority = ""
            current_status = ""
            continue

        if current_task_id:
            status_match = re.match(r"^-\s+\*\*状态\*\*\s*[:：]\s*(.+)$", line)
            if status_match:
                current_status = status_match.group(1).strip()
            priority_match = re.match(r"^-\s+\*\*优先级\*\*\s*[:：]\s*(.+)$", line)
            if priority_match:
                current_priority = priority_match.group(1).strip()

    if current_task_id and current_status == "ready":
        ready_tasks.append({
            "task_id": current_task_id,
            "title": current_title.strip(),
            "priority": current_priority,
        })

    return ready_tasks


# ── [V-002] nightly_acceptance_report: Test log parsing ──

def parse_test_summary_from_logs(run: TaskRun) -> dict[str, int]:
    """Parse test result counts from trace files in a task run.

    Looks for pytest-style output lines like 'X passed, Y failed, Z skipped'.
    Returns dict with passed/failed/skipped/error counts.
    """
    summary = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0, "raw_line": ""}

    for trace_name in run.trace_files:
        if not trace_name.startswith("tests-"):
            continue
        trace_path = run.path / trace_name
        if not trace_path.exists():
            continue
        content = trace_path.read_text(encoding="utf-8", errors="replace")
        for line in reversed(content.splitlines()):
            line = line.strip()
            m = re.match(r"^(\d+) passed", line)
            if m:
                summary["passed"] = int(m.group(1))
                fm = re.search(r"(\d+) failed", line)
                if fm:
                    summary["failed"] = int(fm.group(1))
                sm = re.search(r"(\d+) skipped", line)
                if sm:
                    summary["skipped"] = int(sm.group(1))
                em = re.search(r"(\d+) error", line)
                if em:
                    summary["errors"] = int(em.group(1))
                summary["raw_line"] = line
                return summary

    return summary


# ── [V-002] nightly_acceptance_report: Candidate sample replay ──

def run_sample_replay() -> list[dict]:
    """Run TradeFlow candidate sample replay using false_positive_audit fixtures.

    Returns list of dicts with fixture name, description, and audit result.
    """
    try:
        from tradingagents.tradeflow.false_positive_audit import (
            generate_fixture_samples,
            replay_fixtures,
        )
    except ImportError:
        return []

    fixtures = generate_fixture_samples()
    results = replay_fixtures(fixtures)
    return results


def format_sample_replay(replay_results: list[dict]) -> str:
    """Format sample replay results into a markdown section."""
    if not replay_results:
        return ""

    lines = ["## 候选样本回放", ""]
    lines.append("| 样本 | 描述 | 分类 | 正误判 | 匹配 |")
    lines.append("|------|------|------|--------|------|")

    all_match = True
    for r in replay_results:
        name = r.get("fixture_name", "?")
        desc = r.get("description", "")[:40]
        category = r.get("actual_category", r.get("actual_subcategory", ""))
        fp_type = r.get("actual_fp_type", r.get("actual_fn_type", ""))
        cat_match = r.get("category_match", r.get("subcategory_match", None))
        fp_match = r.get("fp_match", r.get("fn_match", None))

        if cat_match is not None and fp_match is not None:
            match_str = "PASS" if (cat_match and fp_match) else "FAIL"
        elif cat_match is not None:
            match_str = "PASS" if cat_match else "FAIL"
        else:
            match_str = "N/A"

        if match_str == "FAIL":
            all_match = False

        lines.append(f"| {name} | {desc} | {category} | {fp_type} | {match_str} |")

    lines.append("")
    if all_match:
        lines.append("> 所有样本回放通过，候选池质量基线稳定。")
    else:
        lines.append("> **注意**: 部分样本回放不匹配，请检查候选策略或审计逻辑。")
    lines.append("")

    return "\n".join(lines)


@dataclass
class TaskRun:
    run_id: str
    path: Path
    task_id: str = ""
    task_title: str = ""
    priority: str = ""
    status: str = ""
    started_at: str = ""
    finished_at: str = ""
    git_head: str = ""
    rounds: int = 0
    test_commands: str = ""
    reason: str = ""
    review_file: str = ""
    has_summary: bool = False
    summary_content: str = ""
    trace_files: list[str] = field(default_factory=list)


def parse_task_md(path: Path) -> dict[str, str]:
    meta: dict[str, str] = {}
    if not path.exists():
        return meta
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- ") and ":" in line:
            key, _, val = line[2:].partition(":")
            meta[key.strip().lower().replace(" ", "_")] = val.strip()
    return meta


def parse_summary_md(path: Path) -> dict[str, str]:
    meta: dict[str, str] = {}
    if not path.exists():
        return meta
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- ") and ":" in line:
            key, _, val = line[2:].partition(":")
            meta[key.strip().lower().replace(" ", "_")] = val.strip()
    return meta


def collect_trace_files(run_dir: Path) -> list[str]:
    traces: list[str] = []
    skip = {"task.md", "summary.md"}
    for f in sorted(run_dir.iterdir()):
        if f.is_file() and f.name not in skip:
            traces.append(f.name)
    return traces


def scan_task_runs(task_runs_dir: Path, target_date: Optional[str] = None) -> list[TaskRun]:
    if not task_runs_dir.exists():
        return []

    runs: list[TaskRun] = []
    for d in sorted(task_runs_dir.iterdir()):
        if not d.is_dir():
            continue
        parts = d.name.split("-")
        date_part = None
        for i, p in enumerate(parts):
            if re.match(r"^\d{8}$", p) and i + 1 < len(parts) and re.match(r"^\d{6}$", parts[i + 1]):
                date_part = f"{p[:4]}-{p[4:6]}-{p[6:8]}"
                break

        if target_date and date_part != target_date:
            continue

        task_md = d / "task.md"
        summary_md = d / "summary.md"

        task_meta = parse_task_md(task_md)
        summary_meta = parse_summary_md(summary_md)

        task_id = task_meta.get("task", "")
        task_title = ""
        if "—" in task_id:
            task_id, task_title = task_id.split("—", 1)
            task_id = task_id.strip()
            task_title = task_title.strip()

        summary_content = ""
        if summary_md.exists():
            summary_content = redact(summary_md.read_text(encoding="utf-8"))

        run = TaskRun(
            run_id=d.name,
            path=d,
            task_id=task_id,
            task_title=task_title,
            priority=task_meta.get("priority", ""),
            status=task_meta.get("status", ""),
            started_at=task_meta.get("started_at", ""),
            git_head=task_meta.get("git_head", ""),
            test_commands=task_meta.get("test_commands", ""),
            has_summary=summary_md.exists(),
            summary_content=summary_content,
            trace_files=collect_trace_files(d),
        )

        if summary_meta:
            run.finished_at = summary_meta.get("finished_at", "")
            run.rounds = int(summary_meta.get("rounds", "0") or "0")
            run.reason = redact(summary_meta.get("reason", ""))
            run.review_file = summary_meta.get("review_file", "")
            final_status = summary_meta.get("final_status", "")
            if final_status:
                run.status = final_status

        runs.append(run)

    return runs


def get_git_log_for_date(repo_dir: Path, target_date: str) -> list[dict[str, str]]:
    try:
        after = f"{target_date}T00:00:00"
        before_suffix = target_date + "T23:59:59"
        result = subprocess.run(
            [
                "git", "log",
                "--after", after,
                "--before", before_suffix,
                "--pretty=format:%h|%s|%an|%ai",
                "--no-merges",
            ],
            capture_output=True,
            text=True,
            cwd=repo_dir,
            timeout=30,
        )
        if result.returncode != 0:
            return []
        commits = []
        for line in result.stdout.strip().splitlines():
            if "|" not in line:
                continue
            parts = line.split("|", 3)
            if len(parts) >= 4:
                commits.append({
                    "hash": parts[0],
                    "message": parts[1],
                    "author": parts[2],
                    "date": parts[3],
                })
        return commits
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []


def collect_reviews(reviews_dir: Path, target_date: Optional[str] = None) -> list[dict[str, str]]:
    reviews: list[dict[str, str]] = []
    if not reviews_dir.exists():
        return reviews
    for f in sorted(reviews_dir.iterdir()):
        if not f.is_file():
            continue
        date_match = re.search(r"(\d{8})", f.name)
        file_date = None
        if date_match:
            raw = date_match.group(1)
            file_date = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
        if target_date and file_date != target_date:
            continue
        content = redact(f.read_text(encoding="utf-8"))
        lines = content.splitlines()
        first_lines = "\n".join(lines[:20])
        reviews.append({
            "file": f.name,
            "date": file_date or "",
            "preview": first_lines,
        })
    return reviews


def generate_report(
    runs: list[TaskRun],
    commits: list[dict[str, str]],
    reviews: list[dict[str, str]],
    target_date: str,
    ready_queue: Optional[list[dict[str, str]]] = None,  # [V-002]
    replay_results: Optional[list[dict]] = None,  # [V-002]
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = []
    lines.append(f"# 自动开发日报 — {target_date}")
    lines.append("")
    lines.append(f"> 生成时间: {now}  ")
    lines.append(f"> 运行档案数: {len(runs)}  ")
    lines.append(f"> 提交数: {len(commits)}  ")
    lines.append(f"> Review 数: {len(reviews)}  ")
    lines.append("")

    if ready_queue is not None:  # [V-002]
        lines.append(f"> Ready 队列: {len(ready_queue)} 个任务  ")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Summary table
    pass_count = sum(1 for r in runs if r.status == "PASS")
    fail_count = sum(1 for r in runs if r.status in ("NEEDS_HUMAN", "FAIL"))
    other_count = len(runs) - pass_count - fail_count

    lines.append("## 总览")
    lines.append("")
    lines.append(f"| 指标 | 值 |")
    lines.append(f"|------|------|")
    lines.append(f"| 任务运行 | {len(runs)} |")
    lines.append(f"| PASS | {pass_count} |")
    lines.append(f"| FAIL/NEEDS_HUMAN | {fail_count} |")
    lines.append(f"| 其他 | {other_count} |")
    lines.append(f"| 提交 | {len(commits)} |")
    lines.append("")

    # Task runs detail
    if runs:
        lines.append("## 任务运行详情")
        lines.append("")
        for run in runs:
            status_icon = "PASS" if run.status == "PASS" else ("NEEDS_HUMAN" if run.status == "NEEDS_HUMAN" else run.status)
            lines.append(f"### {run.run_id}")
            lines.append("")
            lines.append(f"- **任务**: {run.task_id} — {run.task_title}")
            lines.append(f"- **优先级**: {run.priority}")
            lines.append(f"- **最终状态**: {status_icon}")
            lines.append(f"- **Git HEAD**: {run.git_head}")
            lines.append(f"- **轮次**: {run.rounds}")
            if run.started_at:
                lines.append(f"- **开始时间**: {run.started_at}")
            if run.finished_at:
                lines.append(f"- **结束时间**: {run.finished_at}")
            if run.test_commands:
                lines.append(f"- **测试命令**: `{run.test_commands}`")
            if run.reason:
                lines.append(f"- **失败原因**: {redact(run.reason)}")
            if run.review_file:
                lines.append(f"- **Review**: {run.review_file}")
            if run.trace_files:
                lines.append(f"- **Trace 文件**: {', '.join(run.trace_files)}")
            lines.append("")

            if run.has_summary and run.summary_content:
                safe_summary = redact(run.summary_content)
                lines.append("<details>")
                lines.append(f"<summary>summary.md</summary>")
                lines.append("")
                lines.append("```")
                lines.append(safe_summary.strip())
                lines.append("```")
                lines.append("")
                lines.append("</details>")
                lines.append("")

    # Commits
    if commits:
        lines.append("## 提交记录")
        lines.append("")
        lines.append("| Hash | Message | Author | Date |")
        lines.append("|------|---------|--------|------|")
        for c in commits:
            msg = c["message"].replace("|", "\\|")
            lines.append(f"| {c['hash']} | {msg} | {c['author']} | {c['date'][:16]} |")
        lines.append("")

    # Reviews
    if reviews:
        lines.append("## Codex Reviews")
        lines.append("")
        for rv in reviews:
            lines.append(f"### {rv['file']}")
            lines.append("")
            lines.append("<details>")
            lines.append("<summary>预览</summary>")
            lines.append("")
            lines.append("```")
            lines.append(rv["preview"])
            lines.append("```")
            lines.append("")
            lines.append("</details>")
            lines.append("")

    # Risks
    lines.append("## 风险与注意事项")
    lines.append("")
    risk_items: list[str] = []
    for run in runs:
        if run.status in ("NEEDS_HUMAN", "FAIL"):
            risk_items.append(f"- **{run.task_id}** 需要人工介入: {redact(run.reason)[:200]}")
    if not risk_items:
        risk_items.append("- 无特殊风险")
    lines.extend(risk_items)
    lines.append("")

    # Next steps
    lines.append("## 下一步")
    lines.append("")
    if fail_count > 0:
        lines.append("- 优先处理 NEEDS_HUMAN 任务")
    lines.append("- 检查 `docs/TASKS.md` 中下一个 `ready` 任务")
    lines.append("- 确认测试基线未被破坏")
    lines.append("")

    # [V-002] nightly_acceptance_report: Test results summary
    if runs:
        lines.append("## 测试结果汇总")
        lines.append("")
        for run in runs:
            test_summary = parse_test_summary_from_logs(run)
            if test_summary["raw_line"]:
                lines.append(f"- **{run.task_id}**: {test_summary['passed']} passed, "
                             f"{test_summary['failed']} failed, {test_summary['skipped']} skipped")
            else:
                lines.append(f"- **{run.task_id}**: 无测试日志")
        lines.append("")

    # [V-002] nightly_acceptance_report: Ready queue
    if ready_queue is not None:
        lines.append("## Ready 队列")
        lines.append("")
        if not ready_queue:
            lines.append("> **任务池不足**: 当前无 `ready` 状态任务，夜间 cron 不应空转。")
            lines.append("> 请人工添加新任务到 `docs/TASKS.md`，或将 `proposed` 任务转为 `ready`。")
        else:
            lines.append("| 任务ID | 标题 | 优先级 |")
            lines.append("|--------|------|--------|")
            for t in ready_queue:
                lines.append(f"| {t['task_id']} | {t['title']} | {t['priority']} |")
        lines.append("")

    # [V-002] nightly_acceptance_report: Candidate sample replay
    if replay_results:
        replay_section = format_sample_replay(replay_results)
        if replay_section:
            lines.append(replay_section)

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="[M-002/V-002] Summarize auto dev runs into a daily report")
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (default: today)")
    parser.add_argument("--dry-run", action="store_true", help="Print report to stdout without writing file")
    parser.add_argument("--repo-dir", default=None, help="Repository root directory")
    parser.add_argument("--with-sample-replay", action="store_true",  # [V-002]
                        help="Include TradeFlow candidate sample replay in report")
    args = parser.parse_args()

    repo_dir = Path(args.repo_dir) if args.repo_dir else Path(__file__).resolve().parent.parent
    task_runs_dir = repo_dir / "docs" / "task_runs"
    reviews_dir = repo_dir / "docs" / "reviews"
    reports_dir = repo_dir / "docs" / "auto_dev_reports"
    tasks_md_path = repo_dir / "docs" / "TASKS.md"  # [V-002]

    target_date = args.date or datetime.now().strftime("%Y-%m-%d")

    runs = scan_task_runs(task_runs_dir, target_date=target_date)
    commits = get_git_log_for_date(repo_dir, target_date)
    reviews = collect_reviews(reviews_dir, target_date=target_date)

    ready_queue = parse_ready_queue(tasks_md_path)  # [V-002]

    replay_results = None  # [V-002]
    if args.with_sample_replay:
        replay_results = run_sample_replay()

    report = generate_report(runs, commits, reviews, target_date,
                             ready_queue=ready_queue, replay_results=replay_results)  # [V-002]

    report = redact(report)  # [V-002] final redaction pass

    if args.dry_run:
        print(report)
        return

    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"{target_date}.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"Report written to {report_path}")


if __name__ == "__main__":
    main()
