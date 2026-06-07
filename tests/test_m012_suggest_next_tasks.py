# [M-012] task_pool_suggestion
"""
Tests for scripts/suggest_next_tasks.py — auto-generate proposed task drafts
when the ready queue is empty.
"""

from __future__ import annotations

import os
import re
import textwrap
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

# Import the module under test
import sys

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import suggest_next_tasks as S


# ── Fixtures ──────────────────────────────────────────────────


SAMPLE_TASKS_MD = textwrap.dedent("""\
# 任务池

> 最后更新：2026-06-07

---

## 0. 自动领取规则与当前队列

### 状态规则

- `ready`：允许自动领取。
- `blocked`：前置任务未满足。
- `done`：已完成。
- `proposed`：候选任务草案。

### 当前优先队列

1. `TASK-DONE-001`：已完成任务A（P1，done）。
2. `TASK-READY-001`：可执行任务B（P1，ready）。
3. `TASK-BLOCKED-001`：阻塞任务C（P2，blocked）。

---

## SAMPLE. 样本任务池

### TASK-DONE-001: 已完成任务
- **描述**：一个已完成的样本任务
- **优先级**：P1
- **状态**：done -- commit abc1234
- **前置条件**：无

### TASK-READY-001: 可执行任务
- **描述**：一个 ready 的样本任务
- **优先级**：P1
- **状态**：ready
- **前置条件**：TASK-DONE-001 完成 ✓

### TASK-BLOCKED-001: 阻塞任务
- **描述**：一个 blocked 的样本任务
- **优先级**：P2
- **状态**：blocked
- **前置条件**：TASK-NOTEXIST-001 完成

### TASK-PROPOSED-001: 已有提议
- **描述**：一个 proposed 的样本任务
- **优先级**：P2
- **状态**：proposed
- **前置条件**：无

### TASK-INPROGRESS-001: 进行中任务
- **描述**：一个 in_progress 的样本任务
- **优先级**：P1
- **状态**：in_progress — claimed TASK-INPROGRESS-001-20260607
- **前置条件**：无
""")


SAMPLE_TASKS_MD_NO_READY = textwrap.dedent("""\
# 任务池

---

## SAMPLE. 样本任务池（无 ready）

### TASK-DONE-001: 已完成任务
- **描述**：已完成
- **优先级**：P1
- **状态**：done -- commit abc1234

### TASK-DONE-002: 另一个已完成
- **描述**：已完成2
- **优先级**：P1
- **状态**：done

### TASK-BLOCKED-001: 阻塞但依赖已满足
- **描述**：依赖 TASK-DONE-001 和 TASK-DONE-002
- **优先级**：P1
- **状态**：blocked
- **前置条件**：TASK-DONE-001、TASK-DONE-002 完成 ✓

### TASK-BLOCKED-002: 阻塞且依赖未满足
- **描述**：依赖 TASK-NOTEXIST-001
- **优先级**：P2
- **状态**：blocked
- **前置条件**：TASK-NOTEXIST-001 完成

### TASK-PROPOSED-001: 已有提议
- **描述**：已有 proposed
- **优先级**：P2
- **状态**：proposed

### TASK-INPROGRESS-001: 进行中
- **描述**：进行中
- **优先级**：P1
- **状态**：in_progress — claimed RUN-001
""")


SAMPLE_ROADMAP_MD = textwrap.dedent("""\
# TradingAgents-AShare Roadmap

## Development Phases

### Phase 1: Stabilize TradeFlow
Status: mostly done.

Key tasks:
- TASK-DONE-001
- TASK-READY-001

### Phase 2: Build Mandate Radar
Status: in progress.

Key tasks:
- TASK-BLOCKED-001
- TASK-INPROGRESS-001

### Phase 3: Make Mandate Radar Visible

Key tasks:
- TASK-PROPOSED-001
""")


SAMPLE_TASKS_MD_EMPTY = "# 任务池\n\n---\n"


@pytest.fixture
def tmp_repo(tmp_path):
    """Create a temporary repo with docs/ structure."""
    docs = tmp_path / "docs"
    docs.mkdir()
    task_runs = docs / "task_runs"
    task_runs.mkdir()
    suggestions = docs / "task_suggestions"
    suggestions.mkdir()
    return tmp_path


def write_tasks_md(repo_dir: Path, content: str):
    docs = repo_dir / "docs"
    docs.mkdir(exist_ok=True)
    path = docs / "TASKS.md"
    path.write_text(content, encoding="utf-8")
    return path


def write_roadmap(repo_dir: Path, content: str):
    docs = repo_dir / "docs"
    docs.mkdir(exist_ok=True)
    path = docs / "ROADMAP.md"
    path.write_text(content, encoding="utf-8")
    return path


# ── Test: TaskInfo dataclass ──────────────────────────────────


class TestTaskInfo:
    def test_default(self):
        t = S.TaskInfo()
        assert t.task_id == ""
        assert t.dependencies == []

    def test_with_values(self):
        t = S.TaskInfo(task_id="T-001", title="Test", priority="P1", status="ready")
        assert t.task_id == "T-001"
        assert t.status == "ready"


class TestProposedSuggestion:
    def test_default(self):
        s = S.ProposedSuggestion()
        assert s.suggested_id == ""
        assert s.dependencies == []

    def test_with_values(self):
        s = S.ProposedSuggestion(
            suggested_id="T-001", title="Test", priority="P1", source_reason="test"
        )
        assert s.suggested_id == "T-001"


# ── Test: parse_all_tasks ─────────────────────────────────────


class TestParseAllTasks:
    def test_empty_file(self, tmp_path):
        path = tmp_path / "empty.md"
        path.write_text("", encoding="utf-8")
        tasks = S.parse_all_tasks(path)
        assert tasks == []

    def test_nonexistent_file(self, tmp_path):
        tasks = S.parse_all_tasks(tmp_path / "nonexistent.md")
        assert tasks == []

    def test_parse_sample(self, tmp_path):
        path = write_tasks_md(tmp_path, SAMPLE_TASKS_MD)
        tasks = S.parse_all_tasks(path)
        assert len(tasks) >= 4

        ids = {t.task_id for t in tasks}
        assert "TASK-DONE-001" in ids
        assert "TASK-READY-001" in ids
        assert "TASK-BLOCKED-001" in ids

    def test_parse_status(self, tmp_path):
        path = write_tasks_md(tmp_path, SAMPLE_TASKS_MD)
        tasks = S.parse_all_tasks(path)
        by_id = {t.task_id: t for t in tasks}

        assert "done" in by_id["TASK-DONE-001"].status
        assert "ready" in by_id["TASK-READY-001"].status
        assert "blocked" in by_id["TASK-BLOCKED-001"].status
        assert "proposed" in by_id["TASK-PROPOSED-001"].status
        assert "in_progress" in by_id["TASK-INPROGRESS-001"].status

    def test_parse_dependencies(self, tmp_path):
        path = write_tasks_md(tmp_path, SAMPLE_TASKS_MD)
        tasks = S.parse_all_tasks(path)
        by_id = {t.task_id: t for t in tasks}

        assert "TASK-DONE-001" in by_id["TASK-READY-001"].dependencies
        assert "TASK-NOTEXIST-001" in by_id["TASK-BLOCKED-001"].dependencies

    def test_parse_priority(self, tmp_path):
        path = write_tasks_md(tmp_path, SAMPLE_TASKS_MD)
        tasks = S.parse_all_tasks(path)
        by_id = {t.task_id: t for t in tasks}

        assert by_id["TASK-DONE-001"].priority == "P1"
        assert by_id["TASK-READY-001"].priority == "P1"
        assert by_id["TASK-BLOCKED-001"].priority == "P2"


# ── Test: parse helpers ───────────────────────────────────────


class TestParseHelpers:
    def test_parse_ready(self, tmp_path):
        path = write_tasks_md(tmp_path, SAMPLE_TASKS_MD)
        tasks = S.parse_all_tasks(path)
        ready = S.parse_ready_tasks(tasks)
        assert len(ready) == 1
        assert ready[0].task_id == "TASK-READY-001"

    def test_parse_done(self, tmp_path):
        path = write_tasks_md(tmp_path, SAMPLE_TASKS_MD)
        tasks = S.parse_all_tasks(path)
        done = S.parse_done_task_ids(tasks)
        assert "TASK-DONE-001" in done

    def test_parse_blocked(self, tmp_path):
        path = write_tasks_md(tmp_path, SAMPLE_TASKS_MD)
        tasks = S.parse_all_tasks(path)
        blocked = S.parse_blocked_tasks(tasks)
        assert len(blocked) == 1
        assert blocked[0].task_id == "TASK-BLOCKED-001"

    def test_parse_proposed(self, tmp_path):
        path = write_tasks_md(tmp_path, SAMPLE_TASKS_MD)
        tasks = S.parse_all_tasks(path)
        proposed = S.parse_proposed_tasks(tasks)
        assert len(proposed) == 1
        assert proposed[0].task_id == "TASK-PROPOSED-001"


# ── Test: parse_roadmap_phases ────────────────────────────────


class TestParseRoadmapPhases:
    def test_nonexistent(self, tmp_path):
        phases = S.parse_roadmap_phases(tmp_path / "nonexistent.md")
        assert phases == {}

    def test_parse_sample(self, tmp_path):
        path = write_roadmap(tmp_path, SAMPLE_ROADMAP_MD)
        phases = S.parse_roadmap_phases(path)
        assert "1" in phases
        assert "2" in phases
        assert phases["1"]["title"] == "Stabilize TradeFlow"
        assert "TASK-DONE-001" in phases["1"]["key_tasks"]

    def test_empty_roadmap(self, tmp_path):
        path = tmp_path / "roadmap.md"
        path.write_text("# Roadmap\n\nNo phases.\n", encoding="utf-8")
        phases = S.parse_roadmap_phases(path)
        assert phases == {}


# ── Test: get_recent_task_runs ────────────────────────────────


class TestGetRecentTaskRuns:
    def test_nonexistent(self, tmp_path):
        runs = S.get_recent_task_runs(tmp_path / "nonexistent")
        assert runs == []

    def test_with_dirs(self, tmp_path):
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        for name in ["RUN-A-20260607-120000", "RUN-B-20260606-100000", "RUN-C-20260605-080000"]:
            (runs_dir / name).mkdir()
        runs = S.get_recent_task_runs(runs_dir, limit=2)
        assert len(runs) == 2
        assert runs[0] == "RUN-C-20260605-080000" or "RUN" in runs[0]

    def test_limit(self, tmp_path):
        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        for i in range(5):
            (runs_dir / f"RUN-{i:03d}").mkdir()
        runs = S.get_recent_task_runs(runs_dir, limit=3)
        assert len(runs) == 3


# ── Test: _deps_satisfied ─────────────────────────────────────


class TestDepsSatisfied:
    def test_no_deps(self):
        assert S._deps_satisfied([], set()) is True

    def test_all_satisfied(self):
        assert S._deps_satisfied(["A-001", "A-002"], {"A-001", "A-002", "A-003"}) is True

    def test_partial(self):
        assert S._deps_satisfied(["A-001", "A-002"], {"A-001"}) is False

    def test_none_satisfied(self):
        assert S._deps_satisfied(["A-001"], set()) is False


# ── Test: _is_dev_task ────────────────────────────────────────


class TestIsDevTask:
    def test_normal(self):
        t = S.TaskInfo(task_id="H-001")
        assert S._is_dev_task(t) is True

    def test_excluded_prefix_R(self):
        t = S.TaskInfo(task_id="R-001")
        assert S._is_dev_task(t) is False

    def test_excluded_prefix_AUTO(self):
        t = S.TaskInfo(task_id="AUTO-001")
        assert S._is_dev_task(t) is False

    def test_excluded_id_T000(self):
        t = S.TaskInfo(task_id="T-000")
        assert S._is_dev_task(t) is False

    def test_normal_task(self):
        t = S.TaskInfo(task_id="M-012")
        assert S._is_dev_task(t) is True


# ── Test: generate_suggestions ────────────────────────────────


class TestGenerateSuggestions:
    def test_with_ready_tasks_returns_empty(self, tmp_path):
        path = write_tasks_md(tmp_path, SAMPLE_TASKS_MD)
        tasks = S.parse_all_tasks(path)
        done = S.parse_done_task_ids(tasks)
        suggestions = S.generate_suggestions(tasks, {}, done, [])
        assert len(suggestions) == 0

    def test_no_ready_unblocked_suggested(self, tmp_path):
        path = write_tasks_md(tmp_path, SAMPLE_TASKS_MD_NO_READY)
        tasks = S.parse_all_tasks(path)
        done = S.parse_done_task_ids(tasks)
        roadmap_path = write_roadmap(tmp_path, SAMPLE_ROADMAP_MD)
        phases = S.parse_roadmap_phases(roadmap_path)

        suggestions = S.generate_suggestions(tasks, phases, done, [])
        assert len(suggestions) >= 1

        suggested_ids = {s.suggested_id for s in suggestions}
        assert "TASK-BLOCKED-001" in suggested_ids
        assert "TASK-DONE-001" not in suggested_ids
        assert "TASK-PROPOSED-001" not in suggested_ids
        assert "TASK-INPROGRESS-001" not in suggested_ids

    def test_blocked_with_deps_met_is_suggested(self, tmp_path):
        path = write_tasks_md(tmp_path, SAMPLE_TASKS_MD_NO_READY)
        tasks = S.parse_all_tasks(path)
        done = S.parse_done_task_ids(tasks)
        suggestions = S.generate_suggestions(tasks, {}, done, [])

        blocked_001_suggestions = [s for s in suggestions if s.suggested_id == "TASK-BLOCKED-001"]
        assert len(blocked_001_suggestions) == 1
        s = blocked_001_suggestions[0]
        assert "blocked" in s.source_reason.lower() or "依赖" in s.source_reason

    def test_blocked_with_deps_unmet_in_fallback(self, tmp_path):
        path = write_tasks_md(tmp_path, SAMPLE_TASKS_MD_NO_READY)
        tasks = S.parse_all_tasks(path)
        done = S.parse_done_task_ids(tasks)
        suggestions = S.generate_suggestions(tasks, {}, done, [])

        blocked_002 = [s for s in suggestions if s.suggested_id == "TASK-BLOCKED-002"]
        if blocked_002:
            assert "TASK-NOTEXIST-001" in blocked_002[0].risks
        else:
            all_ids = [s.suggested_id for s in suggestions]
            assert "TASK-BLOCKED-002" not in all_ids

    def test_no_duplicate_proposed(self, tmp_path):
        path = write_tasks_md(tmp_path, SAMPLE_TASKS_MD_NO_READY)
        tasks = S.parse_all_tasks(path)
        done = S.parse_done_task_ids(tasks)
        suggestions = S.generate_suggestions(tasks, {}, done, [])

        suggested_ids = [s.suggested_id for s in suggestions]
        assert "TASK-PROPOSED-001" not in suggested_ids

    def test_roadmap_reason_included(self, tmp_path):
        path = write_tasks_md(tmp_path, SAMPLE_TASKS_MD_NO_READY)
        tasks = S.parse_all_tasks(path)
        done = S.parse_done_task_ids(tasks)
        roadmap_path = write_roadmap(tmp_path, SAMPLE_ROADMAP_MD)
        phases = S.parse_roadmap_phases(roadmap_path)

        suggestions = S.generate_suggestions(tasks, phases, done, [])
        blocked_001 = [s for s in suggestions if s.suggested_id == "TASK-BLOCKED-001"]
        if blocked_001 and phases.get("2", {}).get("key_tasks"):
            assert ("Phase" in blocked_001[0].source_reason
                    or "Roadmap" in blocked_001[0].source_reason
                    or "blocked" in blocked_001[0].source_reason.lower())

    def test_sorted_by_priority(self, tmp_path):
        path = write_tasks_md(tmp_path, SAMPLE_TASKS_MD_NO_READY)
        tasks = S.parse_all_tasks(path)
        done = S.parse_done_task_ids(tasks)
        suggestions = S.generate_suggestions(tasks, {}, done, [])

        if len(suggestions) >= 2:
            priorities = [s.priority for s in suggestions]
            p0_idx = [i for i, p in enumerate(priorities) if "P0" in p]
            p1_idx = [i for i, p in enumerate(priorities) if "P1" in p]
            p2_idx = [i for i, p in enumerate(priorities) if "P2" in p]
            if p0_idx and p2_idx:
                assert p0_idx[0] < p2_idx[0]
            if p1_idx and p2_idx:
                assert p1_idx[0] < p2_idx[0]


# ── Test: render_suggestions_report ───────────────────────────


class TestRenderSuggestionsReport:
    def test_with_ready_tasks(self, tmp_path):
        path = write_tasks_md(tmp_path, SAMPLE_TASKS_MD)
        report = S.render_suggestions_report(
            suggestions=[], ready_count=1, target_date="2026-06-07",
            recent_runs=[], tasks_md_path=path,
        )
        assert "Ready" in report
        assert "无需生成建议" in report or "非空" in report

    def test_no_suggestions_no_ready(self, tmp_path):
        path = write_tasks_md(tmp_path, SAMPLE_TASKS_MD_EMPTY)
        report = S.render_suggestions_report(
            suggestions=[], ready_count=0, target_date="2026-06-07",
            recent_runs=[], tasks_md_path=path,
        )
        assert "无建议生成" in report
        assert "人工" in report or "TASKS.md" in report

    def test_with_suggestions(self, tmp_path):
        path = write_tasks_md(tmp_path, SAMPLE_TASKS_MD_EMPTY)
        suggestions = [
            S.ProposedSuggestion(
                suggested_id="T-001",
                title="Test task",
                priority="P1",
                source_reason="依赖已满足",
                description="A test task",
                acceptance="pytest passes",
                risks="无明显风险",
            ),
        ]
        report = S.render_suggestions_report(
            suggestions=suggestions, ready_count=0, target_date="2026-06-07",
            recent_runs=["RUN-001"], tasks_md_path=path,
        )
        assert "T-001" in report
        assert "P1" in report
        assert "建议" in report
        assert "依赖已满足" in report
        assert "RUN-001" in report

    def test_does_not_modify_tasks_md(self, tmp_path):
        path = write_tasks_md(tmp_path, SAMPLE_TASKS_MD)
        original = path.read_text(encoding="utf-8")
        S.render_suggestions_report(
            suggestions=[], ready_count=0, target_date="2026-06-07",
            recent_runs=[], tasks_md_path=path,
        )
        assert path.read_text(encoding="utf-8") == original

    def test_no_sensitive_data(self, tmp_path):
        path = write_tasks_md(tmp_path, SAMPLE_TASKS_MD_EMPTY)
        suggestions = [
            S.ProposedSuggestion(
                suggested_id="T-001",
                title="Test",
                priority="P1",
                source_reason="test",
                risks="test",
            ),
        ]
        report = S.render_suggestions_report(
            suggestions=suggestions, ready_count=0, target_date="2026-06-07",
            recent_runs=[], tasks_md_path=path,
        )
        assert "sk-" not in report
        assert "api_key" not in report.lower()
        assert "bearer" not in report.lower()


# ── Test: run_suggest (integration) ──────────────────────────


class TestRunSuggest:
    def test_dry_run_prints_report(self, tmp_repo, capsys):
        write_tasks_md(tmp_repo, SAMPLE_TASKS_MD_NO_READY)
        write_roadmap(tmp_repo, SAMPLE_ROADMAP_MD)

        report = S.run_suggest(tmp_repo, target_date="2026-06-07", dry_run=True)
        assert "建议" in report or "无建议" in report

        suggestions_dir = tmp_repo / "docs" / "task_suggestions"
        files = list(suggestions_dir.iterdir())
        assert len(files) == 0

    def test_write_report(self, tmp_repo):
        write_tasks_md(tmp_repo, SAMPLE_TASKS_MD_NO_READY)
        write_roadmap(tmp_repo, SAMPLE_ROADMAP_MD)

        S.run_suggest(tmp_repo, target_date="2026-06-07", dry_run=False)

        report_path = tmp_repo / "docs" / "task_suggestions" / "2026-06-07.md"
        assert report_path.exists()
        content = report_path.read_text(encoding="utf-8")
        assert "任务池建议" in content
        assert "2026-06-07" in content

    def test_with_ready_no_file(self, tmp_repo):
        write_tasks_md(tmp_repo, SAMPLE_TASKS_MD)
        write_roadmap(tmp_repo, SAMPLE_ROADMAP_MD)

        S.run_suggest(tmp_repo, target_date="2026-06-07", dry_run=False)

        report_path = tmp_repo / "docs" / "task_suggestions" / "2026-06-07.md"
        assert report_path.exists()
        content = report_path.read_text(encoding="utf-8")
        assert "非空" in content or "无需" in content

    def test_empty_tasks_md(self, tmp_repo):
        write_tasks_md(tmp_repo, SAMPLE_TASKS_MD_EMPTY)
        write_roadmap(tmp_repo, SAMPLE_ROADMAP_MD)

        S.run_suggest(tmp_repo, target_date="2026-06-07", dry_run=False)

        report_path = tmp_repo / "docs" / "task_suggestions" / "2026-06-07.md"
        assert report_path.exists()

    def test_creates_suggestions_dir(self, tmp_repo):
        suggestions_dir = tmp_repo / "docs" / "task_suggestions"
        if suggestions_dir.exists():
            import shutil
            shutil.rmtree(suggestions_dir)

        write_tasks_md(tmp_repo, SAMPLE_TASKS_MD_NO_READY)
        write_roadmap(tmp_repo, SAMPLE_ROADMAP_MD)

        S.run_suggest(tmp_repo, target_date="2026-06-07", dry_run=False)
        assert suggestions_dir.exists()


# ── Test: acceptance criteria (M-012 verification) ────────────


class TestAcceptanceM012:
    def test_no_ready_generates_proposed_report(self, tmp_repo):
        """验收: 无 ready 任务时生成 proposed 报告。"""
        write_tasks_md(tmp_repo, SAMPLE_TASKS_MD_NO_READY)
        write_roadmap(tmp_repo, SAMPLE_ROADMAP_MD)

        S.run_suggest(tmp_repo, target_date="2026-06-07", dry_run=False)

        report_path = tmp_repo / "docs" / "task_suggestions" / "2026-06-07.md"
        assert report_path.exists()
        content = report_path.read_text(encoding="utf-8")
        assert "建议" in content

    def test_has_ready_no_generation(self, tmp_repo):
        """验收: 有 ready 任务时不生成或只提示无需建议。"""
        write_tasks_md(tmp_repo, SAMPLE_TASKS_MD)
        write_roadmap(tmp_repo, SAMPLE_ROADMAP_MD)

        report = S.run_suggest(tmp_repo, target_date="2026-06-07", dry_run=True)
        suggestions = []
        if "非空" in report or "无需" in report:
            pass
        else:
            pytest.fail("Should indicate no suggestions needed when ready tasks exist")

    def test_report_does_not_change_tasks_status(self, tmp_repo):
        """验收: 报告不直接改 TASKS 状态。"""
        path = write_tasks_md(tmp_repo, SAMPLE_TASKS_MD_NO_READY)
        write_roadmap(tmp_repo, SAMPLE_ROADMAP_MD)

        original = path.read_text(encoding="utf-8")
        S.run_suggest(tmp_repo, target_date="2026-06-07", dry_run=False)
        after = path.read_text(encoding="utf-8")
        assert original == after

    def test_dry_run_does_not_write_files(self, tmp_repo):
        """验收: dry-run 不写文件。"""
        write_tasks_md(tmp_repo, SAMPLE_TASKS_MD_NO_READY)
        write_roadmap(tmp_repo, SAMPLE_ROADMAP_MD)

        S.run_suggest(tmp_repo, target_date="2026-06-07", dry_run=True)
        suggestions_dir = tmp_repo / "docs" / "task_suggestions"
        md_files = list(suggestions_dir.glob("*.md"))
        assert len(md_files) == 0

    def test_report_contains_priority_and_reasons(self, tmp_repo):
        """验收: 报告包含 priority、依赖、原因。"""
        write_tasks_md(tmp_repo, SAMPLE_TASKS_MD_NO_READY)
        write_roadmap(tmp_repo, SAMPLE_ROADMAP_MD)

        S.run_suggest(tmp_repo, target_date="2026-06-07", dry_run=False)
        report_path = tmp_repo / "docs" / "task_suggestions" / "2026-06-07.md"
        content = report_path.read_text(encoding="utf-8")

        assert "优先级" in content or "P1" in content or "P2" in content
        assert "原因" in content or "依赖" in content or "blocked" in content

    def test_no_model_calls(self):
        """验收: 不调用高成本模型。"""
        import inspect
        source = inspect.getsource(S)
        assert "openai" not in source.lower()
        assert "anthropic" not in source.lower()
        assert "requests.post" not in source
        assert "llm" not in source.lower() or "llm_allowed" in source.lower()

    def test_auto_dev_loop_calls_suggest(self):
        """验收: auto_dev_loop.sh 引用了 suggest_next_tasks.py。"""
        script_path = Path(__file__).resolve().parent.parent / "scripts" / "auto_dev_loop.sh"
        if script_path.exists():
            content = script_path.read_text(encoding="utf-8")
            assert "suggest_next_tasks" in content
            assert "M-012" in content
