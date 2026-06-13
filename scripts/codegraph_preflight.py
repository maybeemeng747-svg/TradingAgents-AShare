#!/usr/bin/env python3
"""[M-013] CodeGraph impact preflight for auto dev loop.

Generates codegraph-context.txt, codegraph-impact.txt, and codegraph-status.json
in the task run archive directory before and after each task.

Usage:
    # Pre-task: generate context from task description
    python scripts/codegraph_preflight.py pre \\
        --run-dir docs/task_runs/TASK-XXX \\
        --task-id TASK-XXX \\
        --task-title "Task title text"

    # Post-task: generate impact from changed files
    python scripts/codegraph_preflight.py post \\
        --run-dir docs/task_runs/TASK-XXX \\
        --changed-files "file1.py,file2.py"

    # Full run: pre + post in one call
    python scripts/codegraph_preflight.py full \\
        --run-dir docs/task_runs/TASK-XXX \\
        --task-id TASK-XXX \\
        --task-title "Task title text" \\
        --changed-files "file1.py,file2.py"

CodeGraph CLI must be available in PATH. If not found, writes SKIPPED status
and does not block task execution.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class CodeGraphStatus:
    available: bool = False
    indexed: bool = False
    version: Optional[str] = None
    error: Optional[str] = None
    files_indexed: int = 0
    nodes_count: int = 0
    command: Optional[str] = None

    def to_dict(self):
        return {
            "available": self.available,
            "indexed": self.indexed,
            "version": self.version,
            "error": self.error,
            "files_indexed": self.files_indexed,
            "nodes_count": self.nodes_count,
            "command": self.command,
        }


@dataclass
class PreflightResult:
    status: str = "UNKNOWN"
    context_generated: bool = False
    impact_generated: bool = False
    context_file: Optional[str] = None
    impact_file: Optional[str] = None
    status_file: Optional[str] = None
    symbols_found: int = 0
    impact_files_count: int = 0
    affected_tests_count: int = 0
    warnings: list = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self):
        return {
            "status": self.status,
            "context_generated": self.context_generated,
            "impact_generated": self.impact_generated,
            "context_file": self.context_file,
            "impact_file": self.impact_file,
            "status_file": self.status_file,
            "symbols_found": self.symbols_found,
            "impact_files_count": self.impact_files_count,
            "affected_tests_count": self.affected_tests_count,
            "warnings": self.warnings,
            "error": self.error,
        }


def _run_cmd(cmd, timeout=60, cwd=None):
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return 127, "", "command not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except OSError as e:
        return 1, "", str(e)


def check_codegraph_available(repo_dir=None):
    # [CODEGRAPH-002] codegraph_preflight
    # codegraph status takes [path] as positional argument, not -p.
    # Use -j for JSON output for robust parsing.
    rc, stdout, stderr = _run_cmd(["codegraph", "--version"])
    if rc != 0:
        return CodeGraphStatus(available=False, error="codegraph not found in PATH")

    version = stdout.strip()
    status = CodeGraphStatus(available=True, version=version)

    status_cmd = ["codegraph", "status", "-j", repo_dir or "."]
    status.command = " ".join(status_cmd)
    rc, stdout, stderr = _run_cmd(status_cmd, timeout=30)
    if rc != 0:
        status.error = f"status check failed: {stderr.strip()[:200]}"
        return status

    # [CODEGRAPH-002] Parse JSON output from codegraph status -j
    parsed = False
    if stdout.strip():
        try:
            data = json.loads(stdout)
            status.files_indexed = data.get("fileCount", 0)
            status.nodes_count = data.get("nodeCount", 0)
            parsed = True
        except (json.JSONDecodeError, TypeError):
            pass

    # Fallback: parse text output (handles comma-formatted numbers)
    if not parsed:
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("Files:"):
                try:
                    val = line.split(":", 1)[1].strip().replace(",", "")
                    status.files_indexed = int(val)
                except (ValueError, IndexError):
                    pass
            elif line.startswith("Nodes:"):
                try:
                    val = line.split(":", 1)[1].strip().replace(",", "")
                    status.nodes_count = int(val)
                except (ValueError, IndexError):
                    pass

    status.indexed = status.files_indexed > 0
    return status


def _extract_keywords_from_task(task_id, task_title):
    words = []
    for part in task_id.split("-"):
        if part and not part.isdigit() and len(part) > 1:
            words.append(part.lower())
    title_words = task_title.split()
    skip = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "in",
        "to",
        "for",
        "with",
        "on",
        "at",
        "by",
        "from",
        "is",
        "it",
        "as",
        "no",
        "not",
        "do",
        "be",
    }
    for w in title_words:
        clean = w.strip(".,;:!?()[]{}").lower()
        if len(clean) > 2 and clean not in skip:
            words.append(clean)
    return list(dict.fromkeys(words))[:20]


def generate_context(run_dir, task_id, task_title, repo_dir=None):
    os.makedirs(run_dir, exist_ok=True)
    context_file = os.path.join(run_dir, "codegraph-context.txt")
    status_file = os.path.join(run_dir, "codegraph-status.json")

    cg_status = check_codegraph_available(repo_dir)

    status_data = {
        "task_id": task_id,
        "generated_at": datetime.now().isoformat(),
        "phase": "pre",
        "codegraph": cg_status.to_dict(),
    }

    if not cg_status.available or not cg_status.indexed:
        status_data["result"] = "SKIPPED"
        status_data["reason"] = (
            "CodeGraph not available"
            if not cg_status.available
            else "CodeGraph not indexed"
        )
        with open(status_file, "w", encoding="utf-8") as f:
            json.dump(status_data, f, indent=2, ensure_ascii=False)
        with open(context_file, "w", encoding="utf-8") as f:
            f.write(f"# CodeGraph Context: SKIPPED\n")
            f.write(f"# Task: {task_id}\n")
            f.write(f"# Reason: {status_data['reason']}\n")
        return PreflightResult(
            status="SKIPPED",
            context_file=context_file,
            status_file=status_file,
            warnings=[status_data["reason"]],
        )

    keywords = _extract_keywords_from_task(task_id, task_title)
    query = " ".join(keywords)

    rc, stdout, stderr = _run_cmd(
        ["codegraph", "context", query, "-p", repo_dir or ".", "--no-code", "-n", "50"],
        timeout=60,
    )

    context_lines = []
    context_lines.append(f"# CodeGraph Context for {task_id}")
    context_lines.append(f"# Task: {task_title}")
    context_lines.append(f"# Generated: {datetime.now().isoformat()}")
    context_lines.append(f"# Keywords: {', '.join(keywords)}")
    context_lines.append("")

    symbols_found = 0
    if rc == 0 and stdout.strip():
        context_lines.append(stdout)
        sym_count = stdout.count("## ") + stdout.count("### ")
        symbols_found = max(sym_count, stdout.count("`"))
    else:
        context_lines.append(f"# Context generation returned no results")
        if stderr.strip():
            context_lines.append(f"# Error: {stderr.strip()[:200]}")

    with open(context_file, "w", encoding="utf-8") as f:
        f.write("\n".join(context_lines))

    status_data["result"] = "OK" if rc == 0 else "PARTIAL"
    status_data["symbols_found"] = symbols_found
    with open(status_file, "w", encoding="utf-8") as f:
        json.dump(status_data, f, indent=2, ensure_ascii=False)

    warnings = []
    if rc != 0:
        warnings.append(f"codegraph context exited with code {rc}")
    if symbols_found == 0:
        warnings.append("no symbols found in context")

    return PreflightResult(
        status="OK" if rc == 0 else "PARTIAL",
        context_generated=True,
        context_file=context_file,
        status_file=status_file,
        symbols_found=symbols_found,
        warnings=warnings,
    )


def generate_impact(run_dir, changed_files_str, repo_dir=None):
    impact_file = os.path.join(run_dir, "codegraph-impact.txt")
    status_file = os.path.join(run_dir, "codegraph-status.json")

    cg_status = check_codegraph_available(repo_dir)

    existing_status = {}
    if os.path.exists(status_file):
        try:
            with open(status_file, "r", encoding="utf-8") as f:
                existing_status = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    status_data = existing_status.copy() if existing_status else {}
    status_data["post_generated_at"] = datetime.now().isoformat()
    status_data["phase"] = "post"
    status_data["codegraph_post"] = cg_status.to_dict()

    changed_files = [f.strip() for f in changed_files_str.split(",") if f.strip()]
    if not changed_files:
        status_data["post_result"] = "SKIPPED"
        status_data["reason"] = "no changed files provided"
        with open(status_file, "w", encoding="utf-8") as f:
            json.dump(status_data, f, indent=2, ensure_ascii=False)
        with open(impact_file, "w", encoding="utf-8") as f:
            f.write("# CodeGraph Impact: SKIPPED\n")
            f.write("# Reason: no changed files\n")
        return PreflightResult(
            status="SKIPPED",
            impact_file=impact_file,
            status_file=status_file,
            warnings=["no changed files"],
        )

    if not cg_status.available or not cg_status.indexed:
        status_data["post_result"] = "SKIPPED"
        status_data["reason"] = (
            "CodeGraph not available"
            if not cg_status.available
            else "CodeGraph not indexed"
        )
        with open(status_file, "w", encoding="utf-8") as f:
            json.dump(status_data, f, indent=2, ensure_ascii=False)
        with open(impact_file, "w", encoding="utf-8") as f:
            f.write(f"# CodeGraph Impact: SKIPPED\n")
            f.write(f"# Reason: {status_data['reason']}\n")
        return PreflightResult(
            status="SKIPPED",
            impact_file=impact_file,
            status_file=status_file,
            warnings=[status_data["reason"]],
        )

    impact_lines = []
    impact_lines.append("# CodeGraph Impact Analysis")
    impact_lines.append(f"# Generated: {datetime.now().isoformat()}")
    impact_lines.append(f"# Changed files: {len(changed_files)}")
    impact_lines.append("")

    for cf in changed_files:
        impact_lines.append(f"## Source: {cf}")
        rc, stdout, stderr = _run_cmd(
            ["codegraph", "impact", cf, "-p", repo_dir or ".", "-d", "2"],
            timeout=30,
        )
        if rc == 0 and stdout.strip():
            impact_lines.append(stdout)
        else:
            impact_lines.append(f"(no impact data: rc={rc})")
            if stderr.strip():
                impact_lines.append(f"  error: {stderr.strip()[:100]}")
        impact_lines.append("")

    flat_lines = "\n".join(impact_lines).splitlines()
    total_impact_files = sum(
        1 for line in flat_lines if line.startswith("  →") or line.startswith("  -")
    )

    impact_lines.append("## Affected Tests")
    affected_cmd = ["codegraph", "affected", "-p", repo_dir or ".", "-q"]
    affected_cmd.extend(changed_files)
    rc, stdout, stderr = _run_cmd(
        affected_cmd,
        timeout=30,
    )
    affected_tests_count = 0
    if rc == 0 and stdout.strip():
        test_lines = [line.strip() for line in stdout.strip().splitlines() if line.strip()]
        affected_tests_count = len(test_lines)
        for tl in test_lines:
            impact_lines.append(f"  - {tl}")
    else:
        impact_lines.append("  (could not determine affected tests)")

    with open(impact_file, "w", encoding="utf-8") as f:
        f.write("\n".join(impact_lines))

    status_data["post_result"] = "OK"
    status_data["impact_files_count"] = total_impact_files
    status_data["affected_tests_count"] = affected_tests_count
    status_data["changed_files"] = changed_files
    with open(status_file, "w", encoding="utf-8") as f:
        json.dump(status_data, f, indent=2, ensure_ascii=False)

    warnings = []
    if total_impact_files > 30:
        warnings.append(
            f"large blast radius: {total_impact_files} files impacted"
        )
    if affected_tests_count == 0:
        warnings.append("no affected tests found — coverage gap possible")

    return PreflightResult(
        status="OK",
        impact_generated=True,
        impact_file=impact_file,
        status_file=status_file,
        impact_files_count=total_impact_files,
        affected_tests_count=affected_tests_count,
        warnings=warnings,
    )


def run_preflight(args):
    repo_dir = args.repo_dir or os.getcwd()
    run_dir = args.run_dir

    os.makedirs(run_dir, exist_ok=True)

    result = PreflightResult()

    if args.phase in ("pre", "full"):
        pre = generate_context(
            run_dir, args.task_id or "UNKNOWN", args.task_title or "", repo_dir
        )
        result.context_generated = pre.context_generated
        result.context_file = pre.context_file
        result.status_file = pre.status_file
        result.symbols_found = pre.symbols_found
        result.warnings.extend(pre.warnings)
        if pre.status == "SKIPPED":
            result.status = "SKIPPED"
        else:
            result.status = pre.status

    if args.phase in ("post", "full") and args.changed_files:
        post = generate_impact(run_dir, args.changed_files, repo_dir)
        result.impact_generated = post.impact_generated
        result.impact_file = post.impact_file
        result.impact_files_count = post.impact_files_count
        result.affected_tests_count = post.affected_tests_count
        result.warnings.extend(post.warnings)
        if not result.status_file:
            result.status_file = post.status_file
        if result.status != "SKIPPED":
            result.status = post.status

    if not args.changed_files and args.phase in ("post", "full"):
        result.warnings.append("no changed files — skipping impact")

    if args.dry_run:
        print(f"[DRY-RUN] Phase: {args.phase}")
        print(f"[DRY-RUN] Run dir: {run_dir}")
        print(f"[DRY-RUN] Status: {result.status}")
        if result.warnings:
            print(f"[DRY-RUN] Warnings: {len(result.warnings)}")
            for w in result.warnings:
                print(f"  - {w}")
        return result

    print(f"[M-013] CodeGraph preflight: {result.status}")
    if result.context_file:
        print(f"  Context: {result.context_file}")
    if result.impact_file:
        print(f"  Impact:  {result.impact_file}")
    if result.status_file:
        print(f"  Status:  {result.status_file}")
    if result.warnings:
        print(f"  Warnings ({len(result.warnings)}):")
        for w in result.warnings:
            print(f"    - {w}")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="[M-013] CodeGraph impact preflight for auto dev loop"
    )
    parser.add_argument(
        "phase",
        choices=["pre", "post", "full"],
        help="Pre-task context, post-task impact, or both",
    )
    parser.add_argument(
        "--run-dir", required=True, help="Task run archive directory"
    )
    parser.add_argument("--task-id", default="", help="Task ID (e.g. M-013)")
    parser.add_argument("--task-title", default="", help="Task title text")
    parser.add_argument(
        "--changed-files", default="", help="Comma-separated list of changed files"
    )
    parser.add_argument(
        "--repo-dir", default=None, help="Repository root directory"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print results without writing files"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output result as JSON"
    )

    args = parser.parse_args()
    result = run_preflight(args)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
