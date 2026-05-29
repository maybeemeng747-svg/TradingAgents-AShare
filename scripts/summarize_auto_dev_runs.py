#!/usr/bin/env python3
# [M-002] auto_dev_report_index
"""
Scan docs/task_runs/ and generate a nightly dev report in docs/auto_dev_reports/YYYY-MM-DD.md.

Usage:
    python scripts/summarize_auto_dev_runs.py [--date YYYY-MM-DD] [--dry-run] [--repo-dir PATH]

Constraints:
    - Read-only: only reads docs/task_runs/, docs/reviews/, git log
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
]


def redact(text: str) -> str:
    for pat, repl in _REDACT_PATTERNS:
        text = pat.sub(repl, text)
    return text


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

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="[M-002] Summarize auto dev runs into a daily report")
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (default: today)")
    parser.add_argument("--dry-run", action="store_true", help="Print report to stdout without writing file")
    parser.add_argument("--repo-dir", default=None, help="Repository root directory")
    args = parser.parse_args()

    repo_dir = Path(args.repo_dir) if args.repo_dir else Path(__file__).resolve().parent.parent
    task_runs_dir = repo_dir / "docs" / "task_runs"
    reviews_dir = repo_dir / "docs" / "reviews"
    reports_dir = repo_dir / "docs" / "auto_dev_reports"

    target_date = args.date or datetime.now().strftime("%Y-%m-%d")

    runs = scan_task_runs(task_runs_dir, target_date=target_date)
    commits = get_git_log_for_date(repo_dir, target_date)
    reviews = collect_reviews(reviews_dir, target_date=target_date)

    report = generate_report(runs, commits, reviews, target_date)

    if args.dry_run:
        print(report)
        return

    reports_dir.mkdir(parents=True, exist_ok=True)
    report_path = reports_dir / f"{target_date}.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"Report written to {report_path}")


if __name__ == "__main__":
    main()
