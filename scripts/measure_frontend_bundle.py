#!/usr/bin/env python3
# [PERF-006] frontend_bundle_trend
"""Frontend bundle size parser and trend report generator.

This script implements PERF-006 acceptance:

1. Parse `npm run build` (vite) output and capture js/css gzip sizes.
2. Append a record to `docs/perf/frontend_bundle_trend.jsonl` (one JSON per line)
   so we can track bundle size over time without re-running builds.
3. Generate `docs/perf/frontend_bundle_report.md` summarizing the latest
   measurement, the historical trend, and a list of lazy-load candidates
   (Reports / TradeFlow / TrackingBoard / Charts).

Design notes
------------
- **No live build dependency**: the parser accepts raw vite stdout text. This
  lets tests feed in a fixture string (PERF-006 constraint: "测试只验证解析
  逻辑和报告格式，不让性能测试依赖绝对耗时").
- **No routing changes, no code split**: this script only *suggests*
  lazy-load candidates; it never edits source files.
- **Deterministic output**: report generation is a pure function of
  (measurement, history, candidates) so tests can pin it down.

Usage
-----
    # Parse a captured build log
    python scripts/measure_frontend_bundle.py --from-log build.txt --write

    # Run npm run build inline and record (requires node/npm on PATH)
    python scripts/measure_frontend_bundle.py --run-build --write

    # Just regenerate the report from existing trend history
    python scripts/measure_frontend_bundle.py --report-only
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TREND_PATH = REPO_ROOT / "docs" / "perf" / "frontend_bundle_trend.jsonl"
DEFAULT_REPORT_PATH = REPO_ROOT / "docs" / "perf" / "frontend_bundle_report.md"
DEFAULT_BUILD_CMD = ["npm", "run", "build"]
DEFAULT_FRONTEND_DIR = REPO_ROOT / "frontend"

# Vite prints each emitted asset on its own line in this shape
# (note the │ separator and gzip token):
#   dist/assets/index-CgqzJoWi.js   1,212.22 kB │ gzip: 348.06 kB
# Some lines omit gzip (e.g. svg/jpg) — we only track js/css here.
_VITE_LINE_RE = re.compile(
    r"""
    ^\s*
    (?P<path>\S+\.(?P<ext>js|css))       # emitted asset path + extension
    \s+
    (?P<raw>[\d,]+(?:\.\d+)?)\s*k?B      # raw minified size
    \s*\│\s*
    gzip:\s*
    (?P<gzip>[\d,]+(?:\.\d+)?)\s*k?B     # gzip size
    """,
    re.VERBOSE,
)

# Vite >=5 emits a chunk-size warning when any chunk crosses this threshold.
VITE_DEFAULT_WARN_KB = 500


# ═══════════════════════════════════════════════════════════════════
# [PERF-006] Data model
# ═══════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class AssetMeasurement:
    """One emitted asset (js or css) from a single build."""

    path: str
    kind: str  # "js" | "css"
    raw_kb: float
    gzip_kb: float

    @property
    def raw_bytes(self) -> int:
        return int(round(self.raw_kb * 1024))

    @property
    def gzip_bytes(self) -> int:
        return int(round(self.gzip_kb * 1024))


@dataclass(frozen=True)
class BundleMeasurement:
    """All assets emitted by one build run + metadata."""

    recorded_at: str  # ISO-8601 local
    git_commit: str
    assets: Sequence[AssetMeasurement] = field(default_factory=tuple)
    build_log: str = ""  # full stdout (optional, not persisted to trend)

    @property
    def js_assets(self) -> List[AssetMeasurement]:
        return [a for a in self.assets if a.kind == "js"]

    @property
    def css_assets(self) -> List[AssetMeasurement]:
        return [a for a in self.assets if a.kind == "css"]

    @property
    def total_raw_kb(self) -> float:
        return round(sum(a.raw_kb for a in self.assets), 2)

    @property
    def total_gzip_kb(self) -> float:
        return round(sum(a.gzip_kb for a in self.assets), 2)

    @property
    def largest_js(self) -> Optional[AssetMeasurement]:
        js = self.js_assets
        return max(js, key=lambda a: a.raw_kb) if js else None

    def to_trend_dict(self) -> dict:
        """Compact dict for appending to the jsonl trend log."""
        return {
            "recorded_at": self.recorded_at,
            "git_commit": self.git_commit,
            "total_raw_kb": self.total_raw_kb,
            "total_gzip_kb": self.total_gzip_kb,
            "js_raw_kb": round(sum(a.raw_kb for a in self.js_assets), 2),
            "js_gzip_kb": round(sum(a.gzip_kb for a in self.js_assets), 2),
            "css_raw_kb": round(sum(a.raw_kb for a in self.css_assets), 2),
            "css_gzip_kb": round(sum(a.gzip_kb for a in self.css_assets), 2),
            "largest_js_path": self.largest_js.path if self.largest_js else None,
            "largest_js_raw_kb": (
                round(self.largest_js.raw_kb, 2) if self.largest_js else 0.0
            ),
            "largest_js_gzip_kb": (
                round(self.largest_js.gzip_kb, 2) if self.largest_js else 0.0
            ),
        }


# ═══════════════════════════════════════════════════════════════════
# [PERF-006] Parsing
# ═══════════════════════════════════════════════════════════════════
def parse_vite_output(log: str) -> List[AssetMeasurement]:
    """Parse vite build stdout and return all js/css asset measurements.

    The parser is intentionally tolerant: any line that matches the
    `path  raw kB │ gzip: gzip kB` shape is captured, regardless of the
    surrounding context. Unknown lines (deprecation warnings, transform
    counts, the chunk-size advisory, etc.) are silently skipped.

    Raises ValueError only when the log clearly never reached the asset
    summary (no matches AND looks like a failed build) — this protects
    tests from accidentally asserting on an empty parse.
    """
    if not isinstance(log, str):
        raise TypeError("vite log must be a string")

    measurements: List[AssetMeasurement] = []
    seen_paths: set[str] = set()

    for raw_line in log.splitlines():
        line = raw_line.strip()
        if not line or "gzip:" not in line:
            continue
        match = _VITE_LINE_RE.search(line)
        if not match:
            continue
        path = match.group("path")
        if path in seen_paths:
            # Vite never emits the same asset path twice in one build;
            # dedupe defensively in case of stray log noise.
            continue
        seen_paths.add(path)
        try:
            raw_kb = float(match.group("raw").replace(",", ""))
            gzip_kb = float(match.group("gzip").replace(",", ""))
        except ValueError:
            continue
        measurements.append(
            AssetMeasurement(
                path=path,
                kind=match.group("ext").lower(),
                raw_kb=raw_kb,
                gzip_kb=gzip_kb,
            )
        )

    return measurements


def detect_chunk_size_warning(log: str) -> bool:
    """Return True if vite emitted the >500kB chunk-size warning."""
    if not log:
        return False
    # Match both the old ("larger than 500 kB") and new ("larger than 500 kB")
    # phrasings; the trigger keyword is "chunks are larger than".
    return (
        "chunks are larger than" in log
        or "chunks are larger than 500 kB" in log
        or "Some chunks are larger than" in log
    )


def _run_git_commit() -> str:
    for cmd in (
        ["git", "rev-parse", "--short", "HEAD"],
        ["git", "rev-parse", "HEAD"],
    ):
        try:
            out = subprocess.check_output(
                cmd, cwd=str(REPO_ROOT), stderr=subprocess.DEVNULL
            ).decode("utf-8", errors="replace").strip()
            if out:
                return out[:12]
        except Exception:
            continue
    return "unknown"


def _run_build(frontend_dir: Path) -> str:
    """Run `npm run build` and return combined stdout+stderr."""
    if not frontend_dir.exists():
        raise FileNotFoundError(f"frontend dir not found: {frontend_dir}")
    proc = subprocess.run(
        DEFAULT_BUILD_CMD,
        cwd=str(frontend_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout + "\n" + proc.stderr


# ═══════════════════════════════════════════════════════════════════
# [PERF-006] Lazy-load candidates
# ═══════════════════════════════════════════════════════════════════
# Static candidate list. These are *suggestions* derived from source-tree
# analysis (see report.md §3 for the rationale). The script never edits
# source code — humans/agents decide when to actually wire React.lazy().
@dataclass(frozen=True)
class LazyLoadCandidate:
    name: str
    kind: str  # "page" | "chart_component" | "vendor_lib"
    source: str  # file path under frontend/src
    rationale: str
    estimated_impact: str  # "high" | "medium" | "low"


LAZY_LOAD_CANDIDATES: List[LazyLoadCandidate] = [
    LazyLoadCandidate(
        name="TradeFlow",
        kind="page",
        source="frontend/src/pages/TradeFlow.tsx",
        rationale=(
            "最大的页面模块（~3800 行）。仅在 /tradeflow 路由下使用，"
            "适合 React.lazy + Suspense 路由级拆分。"
        ),
        estimated_impact="high",
    ),
    LazyLoadCandidate(
        name="Reports",
        kind="page",
        source="frontend/src/pages/Reports.tsx",
        rationale=(
            "报告列表/详情页（~785 行），含 ReportViewer 子组件。"
            "非首屏路由，可安全懒加载。"
        ),
        estimated_impact="medium",
    ),
    LazyLoadCandidate(
        name="TrackingBoard",
        kind="page",
        source="frontend/src/pages/TrackingBoard.tsx",
        rationale=(
            "跟踪看板路由（依赖 TrackingBoardV2Panel 等较重组件）。"
            "用户大多从 Dashboard 进入，可懒加载。"
        ),
        estimated_impact="medium",
    ),
    LazyLoadCandidate(
        name="Portfolio",
        kind="page",
        source="frontend/src/pages/Portfolio.tsx",
        rationale=(
            "持仓页（~1343 行），含表格与图表组件。"
            "非首屏，可懒加载。"
        ),
        estimated_impact="medium",
    ),
    LazyLoadCandidate(
        name="Settings",
        kind="page",
        source="frontend/src/pages/Settings.tsx",
        rationale=(
            "设置页（~947 行），与主链路解耦，访问频率低，适合懒加载。"
        ),
        estimated_impact="low",
    ),
    LazyLoadCandidate(
        name="Analysis",
        kind="page",
        source="frontend/src/pages/Analysis.tsx",
        rationale=(
            "智能分析控制台（~430 行 + 多个子组件）。"
            "TradeFlow 跳转入口，可懒加载。"
        ),
        estimated_impact="medium",
    ),
    LazyLoadCandidate(
        name="AgentCollaboration",
        kind="chart_component",
        source="frontend/src/components/AgentCollaboration.tsx",
        rationale=(
            "依赖 @xyflow/react（Flow 图视图），只在报告详情页打开。"
            "可用 React.lazy 在 ReportViewer 内部按需加载。"
        ),
        estimated_impact="medium",
    ),
    LazyLoadCandidate(
        name="KlinePanel",
        kind="chart_component",
        source="frontend/src/components/KlinePanel.tsx",
        rationale=(
            "依赖 lightweight-charts（K 线渲染），仅在 TradeFlow/Reports 中展开。"
            "可在父组件中 React.lazy 引入。"
        ),
        estimated_impact="medium",
    ),
    LazyLoadCandidate(
        name="MiniKline",
        kind="chart_component",
        source="frontend/src/components/MiniKline.tsx",
        rationale=(
            "依赖 lightweight-charts，在候选行/卡片中按需展开。"
            "可懒加载以避免首屏引入 chart 引擎。"
        ),
        estimated_impact="medium",
    ),
]


# ═══════════════════════════════════════════════════════════════════
# [PERF-006] Trend log I/O
# ═══════════════════════════════════════════════════════════════════
def append_trend(measurement: BundleMeasurement, trend_path: Path) -> None:
    """Append one measurement (one JSON object per line) to the trend log.

    Creates parent dirs if needed. Never rewrites history — PERF-006 only
    *records* the trend, it does not edit past entries.
    """
    trend_path.parent.mkdir(parents=True, exist_ok=True)
    record = measurement.to_trend_dict()
    with trend_path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_trend(trend_path: Path) -> List[dict]:
    """Read the full trend history (one dict per line)."""
    if not trend_path.exists():
        return []
    out: List[dict] = []
    with trend_path.open("r", encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                # Skip corrupted lines rather than crashing — the trend
                # log is append-only and may have been partially written.
                continue
    return out


# ═══════════════════════════════════════════════════════════════════
# [PERF-006] Report generation
# ═══════════════════════════════════════════════════════════════════
def _fmt_delta(current: float, previous: Optional[float]) -> str:
    if previous is None or previous == 0:
        return "—"
    delta = current - previous
    pct = (delta / previous) * 100.0
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta:.2f} kB ({sign}{pct:.1f}%)"


def _format_trend_table(history: Sequence[dict]) -> str:
    if not history:
        return "_（暂无历史趋势）_\n"
    header = (
        "| 记录时间 | commit | JS raw | JS gzip | CSS raw | CSS gzip | "
        "Largest JS raw | Largest JS gzip |\n"
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |\n"
    )
    rows = []
    for rec in history:
        rows.append(
            "| {when} | {commit} | {js_raw:.2f} kB | {js_gzip:.2f} kB | "
            "{css_raw:.2f} kB | {css_gzip:.2f} kB | {lraw:.2f} kB | "
            "{lgzip:.2f} kB |\n".format(
                when=rec.get("recorded_at", "—"),
                commit=rec.get("git_commit", "—"),
                js_raw=float(rec.get("js_raw_kb", 0.0)),
                js_gzip=float(rec.get("js_gzip_kb", 0.0)),
                css_raw=float(rec.get("css_raw_kb", 0.0)),
                css_gzip=float(rec.get("css_gzip_kb", 0.0)),
                lraw=float(rec.get("largest_js_raw_kb", 0.0)),
                lgzip=float(rec.get("largest_js_gzip_kb", 0.0)),
            )
        )
    return header + "".join(rows)


def _format_candidates_table(candidates: Sequence[LazyLoadCandidate]) -> str:
    if not candidates:
        return "_（暂无候选）_\n"
    header = (
        "| 候选 | 类型 | 源文件 | 预估影响 | 拆分理由 |\n"
        "| --- | --- | --- | --- | --- |\n"
    )
    rows = []
    for c in candidates:
        rows.append(
            f"| {c.name} | {c.kind} | `{c.source}` | {c.estimated_impact} "
            f"| {c.rationale} |\n"
        )
    return header + "".join(rows)


def generate_report(
    measurement: Optional[BundleMeasurement],
    history: Sequence[dict],
    candidates: Sequence[LazyLoadCandidate],
    vite_warn_threshold_kb: int = VITE_DEFAULT_WARN_KB,
    now_iso: Optional[str] = None,
) -> str:
    """Render the markdown report. Pure function — no I/O.

    `measurement` may be None when the caller only wants to regenerate the
    report from history (e.g. `--report-only`). In that case the "latest
    snapshot" section renders from the last history entry.
    """
    if now_iso is None:
        now_iso = _dt.datetime.now().isoformat(timespec="seconds")

    latest_from_history = history[-1] if history else None
    if measurement is None and latest_from_history is not None:
        # Reconstruct a synthetic BundleMeasurement from the trend aggregates.
        # The trend log stores per-kind (js/css) aggregates plus the single
        # largest JS asset, so we synthesize one js and one css AssetMeasurement
        # for display purposes (per-file detail is only available on fresh
        # measurements — that is intentional, the trend log is compact).
        largest_path = latest_from_history.get("largest_js_path") or "index-*.js"
        measurement = BundleMeasurement(
            recorded_at=latest_from_history.get("recorded_at", now_iso),
            git_commit=latest_from_history.get("git_commit", "unknown"),
            assets=(
                AssetMeasurement(
                    path=largest_path,
                    kind="js",
                    raw_kb=float(latest_from_history.get("js_raw_kb", 0.0)),
                    gzip_kb=float(latest_from_history.get("js_gzip_kb", 0.0)),
                ),
                AssetMeasurement(
                    path="index-*.css",
                    kind="css",
                    raw_kb=float(latest_from_history.get("css_raw_kb", 0.0)),
                    gzip_kb=float(latest_from_history.get("css_gzip_kb", 0.0)),
                ),
            ),
        )

    # Previous measurement (for delta calc). The trend log is append-only;
    # depending on the caller, `history` may or may not already contain the
    # current measurement (the CLI appends before calling us; tests may not).
    previous: Optional[dict] = None
    if measurement is None:
        # report-only: history[-1] is "current", second-to-last is previous.
        if len(history) >= 2:
            previous = history[-2]
    elif history and history[-1].get("recorded_at") == measurement.recorded_at:
        # Caller already appended the current measurement to history.
        if len(history) >= 2:
            previous = history[-2]
    elif history:
        # Measurement not yet in history; latest on disk is the previous.
        previous = history[-1]

    lines: List[str] = []
    lines.append("# 前端 Bundle 体积趋势报告（PERF-006）\n")
    lines.append(
        "> 建立：2026-06-27 · 任务 `PERF-006` · "
        "代码标注 `# [PERF-006] frontend_bundle_trend`\n"
    )
    lines.append(
        "> 本报告由 `scripts/measure_frontend_bundle.py` 自动生成，"
        "记录前端构建产物的 js/css gzip 体积趋势，并给出懒加载候选建议。"
        "**本任务不强制 code split**，候选仅供后续优化参考。\n"
    )
    lines.append("\n")
    lines.append(f"_报告生成时间：{now_iso}_\n\n")

    # ── Section 1: latest snapshot ──
    lines.append("## 1. 最新构建快照\n\n")
    if measurement is None:
        lines.append(
            "_（暂无测量数据。请先运行 "
            "`python scripts/measure_frontend_bundle.py --run-build --write`）_\n\n"
        )
    else:
        lines.append(
            f"- **记录时间**：{measurement.recorded_at}\n"
            f"- **commit**：`{measurement.git_commit}`\n"
            f"- **总 JS**：raw "
            f"{sum(a.raw_kb for a in measurement.js_assets):.2f} kB / "
            f"gzip {sum(a.gzip_kb for a in measurement.js_assets):.2f} kB\n"
            f"- **总 CSS**：raw "
            f"{sum(a.raw_kb for a in measurement.css_assets):.2f} kB / "
            f"gzip {sum(a.gzip_kb for a in measurement.css_assets):.2f} kB\n"
        )
        if measurement.largest_js is not None:
            over = (
                measurement.largest_js.raw_kb > vite_warn_threshold_kb
            )
            flag = " ⚠️ **超 Vite 默认告警阈值**" if over else ""
            lines.append(
                f"- **最大 JS chunk**：`{measurement.largest_js.path}` "
                f"raw {measurement.largest_js.raw_kb:.2f} kB / "
                f"gzip {measurement.largest_js.gzip_kb:.2f} kB"
                f"{flag}\n"
            )
        if previous:
            lines.append("\n**与上次记录的变化**：\n\n")
            lines.append(
                "| 指标 | 上次 | 本次 | 变化 |\n| --- | ---: | ---: | --- |\n"
            )
            lines.append(
                "| JS gzip | {prev:.2f} kB | {cur:.2f} kB | {delta} |\n".format(
                    prev=float(previous.get("js_gzip_kb", 0.0)),
                    cur=sum(a.gzip_kb for a in measurement.js_assets),
                    delta=_fmt_delta(
                        sum(a.gzip_kb for a in measurement.js_assets),
                        float(previous.get("js_gzip_kb", 0.0)),
                    ),
                )
            )
            lines.append(
                "| CSS gzip | {prev:.2f} kB | {cur:.2f} kB | {delta} |\n".format(
                    prev=float(previous.get("css_gzip_kb", 0.0)),
                    cur=sum(a.gzip_kb for a in measurement.css_assets),
                    delta=_fmt_delta(
                        sum(a.gzip_kb for a in measurement.css_assets),
                        float(previous.get("css_gzip_kb", 0.0)),
                    ),
                )
            )
            if measurement.largest_js is not None:
                lines.append(
                    "| 最大 JS gzip | {prev:.2f} kB | {cur:.2f} kB | {delta} |\n".format(
                        prev=float(previous.get("largest_js_gzip_kb", 0.0)),
                        cur=measurement.largest_js.gzip_kb,
                        delta=_fmt_delta(
                            measurement.largest_js.gzip_kb,
                            float(previous.get("largest_js_gzip_kb", 0.0)),
                        ),
                    )
                )
        lines.append("\n")

    # ── Section 2: trend table ──
    lines.append("## 2. 历史趋势\n\n")
    lines.append(
        "> 数据源：`docs/perf/frontend_bundle_trend.jsonl` "
        "（每次 `--write` 追加一行；不重写历史）。\n\n"
    )
    lines.append(_format_trend_table(history))
    lines.append("\n")

    # ── Section 3: lazy-load candidates ──
    lines.append("## 3. 懒加载候选（建议，不强制执行）\n\n")
    lines.append(
        "> PERF-006 验收准则：**不要求实际 code split**。"
        "下列候选由源码静态分析得出，供后续优化任务参考。\n\n"
    )
    lines.append(_format_candidates_table(candidates))
    lines.append("\n")
    lines.append(
        "### 3.1 推荐落地顺序（不强制）\n\n"
        "1. **路由级 `React.lazy()`**：把 `TradeFlow / Reports / "
        "TrackingBoard / Portfolio / Settings` 等大页改为 "
        "`const TradeFlow = React.lazy(() => import('./pages/TradeFlow'))`，"
        "并用 `<Suspense fallback={…}>` 包裹路由。这是收益最大、风险最低的"
        "改造，能直接把首屏 JS gzip 拉到 ~250 kB 以下。\n"
        "2. **图表组件懒加载**：`AgentCollaboration / KlinePanel / MiniKline` "
        "依赖 `@xyflow/react` 与 `lightweight-charts`，可在父组件内通过 "
        "`React.lazy` 在用户展开时再加载，避免首屏引入 chart 引擎。\n"
        "3. **vendor chunk 拆分**（可选）：在 `vite.config.ts` 中配置 "
        "`build.rollupOptions.output.manualChunks`，把 "
        "`lightweight-charts` / `@xyflow/react` / `@dnd-kit/*` 拆成独立 "
        "vendor chunk，提升缓存命中率。\n"
        "4. **检查意外整包 import**：例如 `lucide-react` 务必按图标 import "
        "（`import { IconX } from 'lucide-react'`），不要 `import * as`。\n\n"
    )

    # ── Section 4: constraints & how-to ──
    lines.append("## 4. 约束与运行方式\n\n")
    lines.append(
        "- ❌ 本任务不进行大规模前端重构。\n"
        "- ❌ 本任务不改变路由行为。\n"
        "- ❌ 性能测试不依赖绝对耗时，只验证解析逻辑与报告格式。\n"
        "- ✅ 每次构建产物以一行 JSON 追加到 "
        "`docs/perf/frontend_bundle_trend.jsonl`，可重放历史。\n\n"
    )
    lines.append("### 4.1 如何刷新本报告\n\n")
    lines.append(
        "```bash\n"
        "# 1. 在 frontend/ 跑一次构建，捕获 stdout 到文件\n"
        "cd frontend && npm run build > /tmp/build.log 2>&1\n"
        "\n"
        "# 2. 解析日志 + 追加趋势 + 重写报告\n"
        "python scripts/measure_frontend_bundle.py --from-log /tmp/build.log --write\n"
        "\n"
        "# 或者直接由脚本调用 npm run build\n"
        "python scripts/measure_frontend_bundle.py --run-build --write\n"
        "\n"
        "# 只重渲染报告（不重新构建）\n"
        "python scripts/measure_frontend_bundle.py --report-only\n"
        "```\n\n"
    )

    return "".join(lines)


# ═══════════════════════════════════════════════════════════════════
# [PERF-006] CLI entry
# ═══════════════════════════════════════════════════════════════════
def _build_measurement_from_log(log: str) -> BundleMeasurement:
    assets = parse_vite_output(log)
    if not assets:
        raise ValueError(
            "no js/css assets parsed from build log — "
            "did the build actually emit assets?"
        )
    return BundleMeasurement(
        recorded_at=_dt.datetime.now().isoformat(timespec="seconds"),
        git_commit=_run_git_commit(),
        assets=tuple(assets),
        build_log=log,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Parse vite build output, record bundle size trend, "
        "and emit docs/perf/frontend_bundle_report.md (PERF-006).",
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument(
        "--from-log",
        metavar="PATH",
        help="parse an existing vite build log file",
    )
    src.add_argument(
        "--run-build",
        action="store_true",
        help="run `npm run build` in ./frontend and parse its output",
    )
    src.add_argument(
        "--report-only",
        action="store_true",
        help="skip measurement; regenerate report from existing trend",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="append measurement to trend jsonl and rewrite the report md",
    )
    parser.add_argument(
        "--trend-path",
        type=Path,
        default=DEFAULT_TREND_PATH,
        help=f"trend jsonl path (default: {DEFAULT_TREND_PATH})",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help=f"report md path (default: {DEFAULT_REPORT_PATH})",
    )
    parser.add_argument(
        "--frontend-dir",
        type=Path,
        default=DEFAULT_FRONTEND_DIR,
        help=f"frontend dir for --run-build (default: {DEFAULT_FRONTEND_DIR})",
    )
    args = parser.parse_args(argv)

    measurement: Optional[BundleMeasurement] = None
    if args.report_only:
        # No new measurement; just regenerate the report from history.
        pass
    elif args.from_log:
        log = Path(args.from_log).read_text(encoding="utf-8", errors="replace")
        measurement = _build_measurement_from_log(log)
    elif args.run_build:
        log = _run_build(args.frontend_dir)
        measurement = _build_measurement_from_log(log)
    else:
        parser.error(
            "must specify one of --from-log / --run-build / --report-only"
        )

    if args.write:
        if measurement is not None:
            append_trend(measurement, args.trend_path)
        history = read_trend(args.trend_path)
        report = generate_report(
            measurement=measurement,
            history=history,
            candidates=LAZY_LOAD_CANDIDATES,
        )
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(report, encoding="utf-8")
        print(
            f"[PERF-006] wrote trend -> {args.trend_path} "
            f"({len(history)} records)"
        )
        print(f"[PERF-006] wrote report -> {args.report_path}")
    elif measurement is not None:
        # Dry-run: print parsed summary, do not write.
        snap = measurement.to_trend_dict()
        print(json.dumps(snap, ensure_ascii=False, indent=2))
    else:
        # report-only without --write: print current report to stdout.
        history = read_trend(args.trend_path)
        print(
            generate_report(
                measurement=None,
                history=history,
                candidates=LAZY_LOAD_CANDIDATES,
            )
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
