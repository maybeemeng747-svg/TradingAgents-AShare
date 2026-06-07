#!/usr/bin/env python3
# [M-012] task_pool_suggestion
"""
When docs/TASKS.md has no ready tasks, generate proposed task drafts based on
ROADMAP, TASKS.md, DEVLOG.md, and recent task_runs.

Usage:
    python scripts/suggest_next_tasks.py [--dry-run] [--repo-dir PATH]

Constraints:
    - No model calls; static analysis only.
    - Never auto-promotes proposed -> ready.
    - Does not modify TASKS.md status.
    - No changes to tradingagents/prompts/ or tradingagents.db.
    - Output goes to docs/task_suggestions/YYYY-MM-DD.md.
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class TaskInfo:
    task_id: str = ""
    title: str = ""
    priority: str = ""
    status: str = ""
    body: str = ""
    dependencies: list[str] = field(default_factory=list)
    section_header: str = ""


@dataclass
class ProposedSuggestion:
    suggested_id: str = ""
    title: str = ""
    priority: str = ""
    source_reason: str = ""
    description: str = ""
    dependencies: list[str] = field(default_factory=list)
    acceptance: str = ""
    risks: str = ""
    section: str = ""


def parse_all_tasks(tasks_md_path: Path) -> list[TaskInfo]:
    if not tasks_md_path.exists():
        return []

    text = tasks_md_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"###\s+([\w-]+)\s*:\s*(.+?)\n(.*?)(?=\n###|\n---|\Z)",
        re.DOTALL,
    )

    tasks: list[TaskInfo] = []
    for m in pattern.finditer(text):
        task_id = m.group(1)
        title = m.group(2).strip()
        body = m.group(3)

        status = ""
        status_match = re.search(
            r"\*\*(status|状态|状态)\*\*\s*[：:,]+\s*(.+?)(?:\n|$)", body, re.IGNORECASE
        )
        if status_match:
            status = status_match.group(2).strip()

        priority = ""
        prio_match = re.search(r"\*\*优先级\*\*\s*[：:,]+\s*(.+?)(?:\n|$)", body)
        if prio_match:
            priority = prio_match.group(1).strip()
        elif "P0" in body[:200]:
            priority = "P0"
        elif "P1" in body[:200]:
            priority = "P1"
        elif "P2" in body[:200]:
            priority = "P2"

        deps: list[str] = []
        dep_match = re.search(
            r"\*\*前置条件\*\*\s*[：:,]+\s*(.+?)(?:\n|$)", body
        )
        if dep_match:
            dep_text = dep_match.group(1)
            deps = re.findall(r"[A-Z]+-[\w-]+", dep_text)

        section_match = re.search(r"^##\s+(.+?)$", text[: m.start()], re.MULTILINE)
        section = section_match.group(1).strip() if section_match else ""

        tasks.append(
            TaskInfo(
                task_id=task_id,
                title=title,
                priority=priority,
                status=status.lower() if status else "",
                body=body,
                dependencies=deps,
                section_header=section,
            )
        )

    return tasks


def parse_ready_tasks(tasks: list[TaskInfo]) -> list[TaskInfo]:
    return [t for t in tasks if "ready" in t.status]


def parse_done_task_ids(tasks: list[TaskInfo]) -> set[str]:
    done_ids: set[str] = set()
    for t in tasks:
        if "done" in t.status:
            done_ids.add(t.task_id)
    return done_ids


def parse_blocked_tasks(tasks: list[TaskInfo]) -> list[TaskInfo]:
    return [t for t in tasks if "blocked" in t.status]


def parse_proposed_tasks(tasks: list[TaskInfo]) -> list[TaskInfo]:
    return [t for t in tasks if "proposed" in t.status]


def get_recent_task_runs(task_runs_dir: Path, limit: int = 10) -> list[str]:
    if not task_runs_dir.exists():
        return []

    dirs = sorted(
        [d for d in task_runs_dir.iterdir() if d.is_dir()],
        key=lambda d: d.name,
        reverse=True,
    )
    return [d.name for d in dirs[:limit]]


def parse_roadmap_phases(roadmap_path: Path) -> dict[str, dict]:
    if not roadmap_path.exists():
        return {}

    text = roadmap_path.read_text(encoding="utf-8")
    phases: dict[str, dict] = {}

    phase_pattern = re.compile(
        r"###\s+Phase\s+(\d+(?:\.\d+)?)[：:]\s*(.+?)(?:\n|$)(.*?)(?=###\s+Phase|\Z)",
        re.DOTALL,
    )

    for m in phase_pattern.finditer(text):
        phase_num = m.group(1)
        phase_title = m.group(2).strip()
        phase_body = m.group(3)

        status = ""
        if "status:" in phase_body.lower() or "status：" in phase_body.lower():
            status_match = re.search(
                r"[Ss]tatus[：:]\s*(.+?)(?:\n|$)", phase_body
            )
            if status_match:
                status = status_match.group(1).strip()

        key_tasks: list[str] = []
        for task_match in re.finditer(r"-\s+([A-Z][\w-]+-[\d]+)", phase_body):
            key_tasks.append(task_match.group(1))

        phases[phase_num] = {
            "title": phase_title,
            "status": status,
            "key_tasks": key_tasks,
            "body": phase_body,
        }

    return phases


def _deps_satisfied(deps: list[str], done_ids: set[str]) -> bool:
    if not deps:
        return True
    return all(d in done_ids for d in deps)


def _is_dev_task(task: TaskInfo) -> bool:
    excluded_prefixes = ("R-", "AUTO-", "T-000")
    excluded_ids = {"T-000"}
    if task.task_id in excluded_ids:
        return False
    if any(task.task_id.startswith(p) for p in excluded_prefixes):
        return False
    return True


def generate_suggestions(
    tasks: list[TaskInfo],
    roadmap_phases: dict[str, dict],
    done_ids: set[str],
    recent_runs: list[str],
) -> list[ProposedSuggestion]:
    suggestions: list[ProposedSuggestion] = []

    ready_tasks = parse_ready_tasks(tasks)
    if ready_tasks:
        return suggestions

    blocked_tasks = parse_blocked_tasks(tasks)
    existing_proposed = parse_proposed_tasks(tasks)
    proposed_ids = {t.task_id for t in existing_proposed}

    for task in tasks:
        if not _is_dev_task(task):
            continue

        if "done" in task.status:
            continue
        if "ready" in task.status:
            continue
        if "in_progress" in task.status:
            continue
        if task.task_id in proposed_ids:
            continue
        if "blocked" in task.status and _deps_satisfied(task.dependencies, done_ids):
            pass
        elif "blocked" in task.status:
            continue

        if _deps_satisfied(task.dependencies, done_ids):
            reasons = []

            if "blocked" in task.status and _deps_satisfied(task.dependencies, done_ids):
                reasons.append(
                    f"原本 blocked，但依赖 {', '.join(task.dependencies)} 已全部完成"
                )

            for phase_num, phase_info in roadmap_phases.items():
                if task.task_id in phase_info.get("key_tasks", []):
                    reasons.append(
                        f"属于 Roadmap Phase {phase_num}（{phase_info['title']}）的关键任务"
                    )

            if task.priority in ("P1", "P0"):
                reasons.append(f"高优先级（{task.priority}）")

            desc_match = re.search(
                r"\*\*描述\*\*\s*[：:,]+\s*(.+?)(?:\n|$)", task.body
            )
            desc = desc_match.group(1).strip() if desc_match else task.title

            acc_match = re.search(
                r"\*\*验收(?:方式)?\*\*\s*[：:,]+\s*(.+?)(?:\n##|\n###|\n---|\Z)",
                task.body,
                re.DOTALL,
            )
            acceptance = acc_match.group(1).strip()[:300] if acc_match else ""

            risks = []
            if not task.dependencies:
                pass
            else:
                unsatisfied = [d for d in task.dependencies if d not in done_ids]
                if unsatisfied:
                    risks.append(f"依赖未满足: {', '.join(unsatisfied)}")

            suggestions.append(
                ProposedSuggestion(
                    suggested_id=task.task_id,
                    title=task.title,
                    priority=task.priority or "P2",
                    source_reason="; ".join(reasons) if reasons else "依赖已满足，可转为 ready",
                    description=desc,
                    dependencies=task.dependencies,
                    acceptance=acceptance,
                    risks="; ".join(risks) if risks else "无明显风险",
                    section=task.section_header,
                )
            )

    if not suggestions:
        for task in blocked_tasks:
            if task.task_id in proposed_ids:
                continue
            unsatisfied = [d for d in task.dependencies if d not in done_ids]
            suggestions.append(
                ProposedSuggestion(
                    suggested_id=task.task_id,
                    title=task.title,
                    priority=task.priority or "P2",
                    source_reason=f"blocked，等待依赖完成: {', '.join(unsatisfied)}",
                    description=task.title,
                    dependencies=task.dependencies,
                    acceptance="",
                    risks=f"阻塞依赖: {', '.join(unsatisfied)}",
                    section=task.section_header,
                )
            )

    suggestions.sort(key=lambda s: (0 if "P0" in s.priority else 1 if "P1" in s.priority else 2, s.suggested_id))

    return suggestions


def render_suggestions_report(
    suggestions: list[ProposedSuggestion],
    ready_count: int,
    target_date: str,
    recent_runs: list[str],
    tasks_md_path: Path,
) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = []
    lines.append(f"# 任务池建议 — {target_date}")
    lines.append("")
    lines.append(f"> 生成时间: {now}  ")
    lines.append(f"> Ready 任务数: {ready_count}  ")
    lines.append(f"> 建议任务数: {len(suggestions)}  ")
    lines.append(f"> 最近运行: {len(recent_runs)} 个  ")
    lines.append("")
    lines.append("---")
    lines.append("")

    if ready_count > 0:
        lines.append("## 当前 Ready 队列非空")
        lines.append("")
        lines.append(
            "当前仍有 `ready` 状态任务，无需生成建议。请先完成现有任务。"
        )
        lines.append("")
        return "\n".join(lines)

    if not suggestions:
        lines.append("## 无建议生成")
        lines.append("")
        lines.append("当前无 `ready` 任务，且无法从 ROADMAP/TASKS 中推导出新建议。")
        lines.append("可能原因：")
        lines.append("- 所有任务已完成或正在进行")
        lines.append("- 剩余 blocked 任务依赖未满足")
        lines.append("- 需要人工从 Roadmap 中提取新任务")
        lines.append("")
        lines.append(
        "> 请人工在 `docs/TASKS.md` 中添加新任务，或将 `proposed` 任务转为 `ready`。"
        )
        lines.append("")
        return "\n".join(lines)

    lines.append("## 建议任务列表")
    lines.append("")
    lines.append("| 任务ID | 标题 | 优先级 | 原因 | 风险 |")
    lines.append("|--------|------|--------|------|------|")
    for s in suggestions:
        reason_short = s.source_reason[:60]
        risk_short = s.risks[:40]
        lines.append(
            f"| {s.suggested_id} | {s.title[:40]} | {s.priority} | {reason_short} | {risk_short} |"
        )
    lines.append("")

    for i, s in enumerate(suggestions, 1):
        lines.append(f"### 建议 {i}: {s.suggested_id}")
        lines.append("")
        lines.append(f"- **标题**: {s.title}")
        lines.append(f"- **优先级**: {s.priority}")
        lines.append(f"- **原因**: {s.source_reason}")
        lines.append(f"- **描述**: {s.description[:200]}")
        if s.dependencies:
            lines.append(f"- **依赖**: {', '.join(s.dependencies)}")
        else:
            lines.append("- **依赖**: 无")
        if s.acceptance:
            lines.append(f"- **验收方式**: {s.acceptance[:200]}")
        lines.append(f"- **风险**: {s.risks}")
        lines.append(f"- **所属**: {s.section or '未分类'}")
        lines.append("")
        lines.append("> 此建议不直接修改 `docs/TASKS.md` 状态。需人工或 Codex 确认后转为 `ready`。")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("## 下一步")
    lines.append("")
    lines.append("1. 审核上述建议任务。")
    lines.append("2. 对认可的建议，在 `docs/TASKS.md` 中将对应任务状态从 `blocked`/`proposed` 改为 `ready`。")
    lines.append("3. 或人工添加新任务到 `docs/TASKS.md`。")
    lines.append("4. 重新运行 `scripts/auto_dev_loop.sh` 领取 ready 任务。")
    lines.append("")

    if recent_runs:
        lines.append("## 最近任务运行")
        lines.append("")
        for run_name in recent_runs[:5]:
            lines.append(f"- `{run_name}`")
        lines.append("")

    return "\n".join(lines)


def run_suggest(
    repo_dir: Path,
    target_date: Optional[str] = None,
    dry_run: bool = False,
) -> str:
    tasks_md_path = repo_dir / "docs" / "TASKS.md"
    roadmap_path = repo_dir / "docs" / "ROADMAP.md"
    task_runs_dir = repo_dir / "docs" / "task_runs"
    suggestions_dir = repo_dir / "docs" / "task_suggestions"

    if not target_date:
        target_date = datetime.now().strftime("%Y-%m-%d")

    tasks = parse_all_tasks(tasks_md_path)
    done_ids = parse_done_task_ids(tasks)
    ready_tasks = parse_ready_tasks(tasks)
    roadmap_phases = parse_roadmap_phases(roadmap_path)
    recent_runs = get_recent_task_runs(task_runs_dir)

    suggestions = generate_suggestions(tasks, roadmap_phases, done_ids, recent_runs)

    report = render_suggestions_report(
        suggestions=suggestions,
        ready_count=len(ready_tasks),
        target_date=target_date,
        recent_runs=recent_runs,
        tasks_md_path=tasks_md_path,
    )

    if not dry_run:
        suggestions_dir.mkdir(parents=True, exist_ok=True)
        report_path = suggestions_dir / f"{target_date}.md"
        report_path.write_text(report, encoding="utf-8")
        print(f"Suggestions written to {report_path}")
    else:
        print(report)

    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="[M-012] Suggest proposed tasks when ready queue is empty"
    )
    parser.add_argument("--dry-run", action="store_true", help="Print to stdout")
    parser.add_argument("--repo-dir", default=None, help="Repository root")
    parser.add_argument(
        "--date", default=None, help="Target date YYYY-MM-DD (default: today)"
    )
    args = parser.parse_args()

    repo_dir = Path(args.repo_dir) if args.repo_dir else Path(__file__).resolve().parent.parent
    target_date = args.date

    run_suggest(repo_dir, target_date=target_date, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
