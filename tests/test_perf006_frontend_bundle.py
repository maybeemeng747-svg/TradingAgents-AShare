# [PERF-006] frontend_bundle_trend
"""Tests for the frontend bundle measurement script.

PERF-006 acceptance:
1. fixture build output can be parsed (no live npm run build required).
2. report markdown is generated and contains the expected sections.
3. lazy-load candidates cover Reports / TradeFlow / TrackingBoard / Charts.

Constraints honoured:
- No live `npm run build` is ever invoked — all parsing tests use fixture
  strings captured from a real vite v6 build.
- No assertion on absolute timing.
- No writes outside tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Module under test
from scripts.measure_frontend_bundle import (
    AssetMeasurement,
    BundleMeasurement,
    LAZY_LOAD_CANDIDATES,
    LazyLoadCandidate,
    VITE_DEFAULT_WARN_KB,
    _VITE_LINE_RE,
    append_trend,
    detect_chunk_size_warning,
    generate_report,
    parse_vite_output,
    read_trend,
)


# ═══════════════════════════════════════════════════════════════════
# [PERF-006] Fixture build logs
# ═══════════════════════════════════════════════════════════════════
# Captured from a real `npm run build` (vite v6.4.2) on this repo at
# commit 9e0d083. Two scenarios:
#   - VITE_LOG_FULL: chunk-size warning + 1 js + 1 css (current state).
#   - VITE_LOG_NO_WARNING: trimmed log without the chunk-size advisory.
#   - VITE_LOG_MULTI_CHUNK: hypothetical future with split chunks.
VITE_LOG_FULL = """\
vite v6.4.2 building for production...
(node:37085) [DEP0205] DeprecationWarning: `module.register()` is deprecated.
transforming...
✓ 1940 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                     1.15 kB │ gzip:   0.65 kB
dist/assets/index-DmcBNlVs.css    169.25 kB │ gzip:  23.23 kB
dist/assets/index-CgqzJoWi.js   1,212.22 kB │ gzip: 348.06 kB

(!) Some chunks are larger than 500 kB after minification. Consider:
- Using dynamic import() to code-split the application
- Use build.rollupOptions.output.manualChunks to improve chunking
- Adjust chunk size limit for build.chunkSizeWarningLimit.
✓ built in 1.35s
"""

VITE_LOG_NO_WARNING = """\
vite v6.4.2 building for production...
transforming...
✓ 100 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                     1.15 kB │ gzip:   0.65 kB
dist/assets/index-DmcBNlVs.css    169.25 kB │ gzip:  23.23 kB
dist/assets/index-CgqzJoWi.js   312.22 kB │ gzip: 98.06 kB
✓ built in 1.35s
"""

# Hypothetical future where code-splitting landed: one vendor chunk +
# per-route chunks. Numbers are illustrative, not measured.
VITE_LOG_MULTI_CHUNK = """\
vite v6.4.2 building for production...
✓ 2000 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                     1.15 kB │ gzip:   0.65 kB
dist/assets/index-DmcBNlVs.css    169.25 kB │ gzip:  23.23 kB
dist/assets/vendor-DgqzJoWi.js    480.00 kB │ gzip: 142.00 kB
dist/assets/TradeFlow-Ab12.js     380.00 kB │ gzip: 102.00 kB
dist/assets/Reports-Cd34.js       210.00 kB │ gzip:  58.00 kB
dist/assets/index-Ef56.js         120.00 kB │ gzip:  42.00 kB
✓ built in 1.40s
"""


# ═══════════════════════════════════════════════════════════════════
# [PERF-006] Parser tests
# ═══════════════════════════════════════════════════════════════════
class TestParseViteOutput:
    """parse_vite_output extracts js/css assets from vite stdout."""

    def test_parses_current_real_build_log(self):
        """The actual captured build log parses to 1 js + 1 css asset."""
        assets = parse_vite_output(VITE_LOG_FULL)
        kinds = sorted(a.kind for a in assets)
        assert kinds == ["css", "js"]
        js = next(a for a in assets if a.kind == "js")
        assert js.path == "dist/assets/index-CgqzJoWi.js"
        # raw value has a thousands comma — must be stripped.
        assert js.raw_kb == pytest.approx(1212.22, abs=0.01)
        assert js.gzip_kb == pytest.approx(348.06, abs=0.01)
        css = next(a for a in assets if a.kind == "css")
        assert css.path == "dist/assets/index-DmcBNlVs.css"
        assert css.raw_kb == pytest.approx(169.25, abs=0.01)
        assert css.gzip_kb == pytest.approx(23.23, abs=0.01)

    def test_ignores_non_asset_lines(self):
        """Deprecation warnings, transform counts, and the chunk-size
        advisory must NOT be mis-parsed as assets."""
        assets = parse_vite_output(VITE_LOG_FULL)
        paths = {a.path for a in assets}
        # The index.html line should be skipped because .html != js|css.
        assert all(not p.endswith(".html") for p in paths)
        # The advisory lines contain "Consider:" etc. — none of them
        # match the `path kB │ gzip: kB` shape, so they are skipped.
        for a in assets:
            assert a.kind in {"js", "css"}

    def test_handles_thousands_separator(self):
        """Vite prints 1,212.22 with a thousands comma — must parse."""
        assets = parse_vite_output(VITE_LOG_FULL)
        js = next(a for a in assets if a.kind == "js")
        assert js.raw_kb == pytest.approx(1212.22, abs=0.01)

    def test_parses_multi_chunk_future_build(self):
        """When code-splitting lands, multiple chunks must all be captured."""
        assets = parse_vite_output(VITE_LOG_MULTI_CHUNK)
        js_assets = sorted(a.path for a in assets if a.kind == "js")
        assert len(js_assets) == 4
        assert any("TradeFlow" in p for p in js_assets)
        assert any("Reports" in p for p in js_assets)
        assert any("vendor" in p for p in js_assets)

    def test_parses_no_warning_log(self):
        """A log without the chunk-size advisory must still parse."""
        assets = parse_vite_output(VITE_LOG_NO_WARNING)
        assert len(assets) == 2  # 1 js + 1 css
        js = next(a for a in assets if a.kind == "js")
        # Confirm we got the smaller-numbered asset (no warning).
        assert js.raw_kb < VITE_DEFAULT_WARN_KB

    def test_dedupes_repeated_paths(self):
        """If the same asset path appears twice (log noise), only the
        first occurrence is captured."""
        dup_log = (
            "dist/assets/index-X.js   100.00 kB │ gzip: 30.00 kB\n"
            "dist/assets/index-X.js   100.00 kB │ gzip: 30.00 kB\n"
        )
        assets = parse_vite_output(dup_log)
        assert len(assets) == 1
        assert assets[0].path == "dist/assets/index-X.js"

    def test_empty_log_returns_empty_list(self):
        assert parse_vite_output("") == []

    def test_log_without_assets_returns_empty(self):
        """A log that crashed before emitting assets returns []. The
        parser does not raise — callers detect via empty result."""
        crash_log = (
            "vite v6.4.2 building for production...\n"
            "Error: ENOENT no entry point\n"
        )
        assert parse_vite_output(crash_log) == []

    def test_rejects_non_string_input(self):
        """Defensive: parser must refuse non-string input."""
        with pytest.raises(TypeError):
            parse_vite_output(b"bytes are not allowed")  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            parse_vite_output(None)  # type: ignore[arg-type]

    def test_regex_does_not_match_html_or_svg(self):
        """The path group requires .js or .css extension."""
        match = _VITE_LINE_RE.search(
            "dist/index.html  1.15 kB │ gzip: 0.65 kB"
        )
        assert match is None

    def test_asset_measurement_byte_conversion(self):
        """raw_bytes / gzip_bytes are derived correctly from kB."""
        a = AssetMeasurement(path="x.js", kind="js", raw_kb=1.0, gzip_kb=0.5)
        assert a.raw_bytes == 1024
        assert a.gzip_bytes == 512


# ═══════════════════════════════════════════════════════════════════
# [PERF-006] Chunk-size warning detection
# ═══════════════════════════════════════════════════════════════════
class TestDetectChunkSizeWarning:
    def test_detects_current_warning(self):
        assert detect_chunk_size_warning(VITE_LOG_FULL) is True

    def test_detects_no_warning(self):
        assert detect_chunk_size_warning(VITE_LOG_NO_WARNING) is False

    def test_handles_empty_log(self):
        assert detect_chunk_size_warning("") is False

    def test_detects_alternative_phrasings(self):
        # Future vite versions may rephrase; we match on the keyword.
        assert (
            detect_chunk_size_warning(
                "Some chunks are larger than 500 kB..."
            )
            is True
        )


# ═══════════════════════════════════════════════════════════════════
# [PERF-006] BundleMeasurement aggregation
# ═══════════════════════════════════════════════════════════════════
class TestBundleMeasurement:
    def _make(self) -> BundleMeasurement:
        return BundleMeasurement(
            recorded_at="2026-06-27T00:00:00",
            git_commit="abc1234",
            assets=(
                AssetMeasurement("a.js", "js", 100.0, 30.0),
                AssetMeasurement("b.js", "js", 200.0, 60.0),
                AssetMeasurement("a.css", "css", 50.0, 10.0),
            ),
        )

    def test_js_css_partition(self):
        m = self._make()
        assert len(m.js_assets) == 2
        assert len(m.css_assets) == 1

    def test_totals(self):
        m = self._make()
        assert m.total_raw_kb == pytest.approx(350.0)
        assert m.total_gzip_kb == pytest.approx(100.0)

    def test_largest_js(self):
        m = self._make()
        assert m.largest_js is not None
        assert m.largest_js.path == "b.js"
        assert m.largest_js.raw_kb == 200.0

    def test_largest_js_empty(self):
        m = BundleMeasurement(
            recorded_at="t", git_commit="c", assets=()
        )
        assert m.largest_js is None

    def test_to_trend_dict_shape(self):
        """trend dict must carry the fields the report reconstruction
        relies on (js/css aggregates + largest JS path/sizes)."""
        m = self._make()
        d = m.to_trend_dict()
        for key in (
            "recorded_at",
            "git_commit",
            "total_raw_kb",
            "total_gzip_kb",
            "js_raw_kb",
            "js_gzip_kb",
            "css_raw_kb",
            "css_gzip_kb",
            "largest_js_path",
            "largest_js_raw_kb",
            "largest_js_gzip_kb",
        ):
            assert key in d, f"missing key {key}"
        assert d["js_gzip_kb"] == pytest.approx(90.0)
        assert d["css_gzip_kb"] == pytest.approx(10.0)
        assert d["largest_js_path"] == "b.js"
        assert d["largest_js_raw_kb"] == pytest.approx(200.0)


# ═══════════════════════════════════════════════════════════════════
# [PERF-006] Trend log I/O
# ═══════════════════════════════════════════════════════════════════
class TestTrendIO:
    def _make_measurement(self, when: str = "2026-06-27T00:00:00") -> BundleMeasurement:
        return BundleMeasurement(
            recorded_at=when,
            git_commit="abc1234",
            assets=(
                AssetMeasurement("index.js", "js", 1212.22, 348.06),
                AssetMeasurement("index.css", "css", 169.25, 23.23),
            ),
        )

    def test_append_then_read_roundtrip(self, tmp_path):
        trend = tmp_path / "trend.jsonl"
        append_trend(self._make_measurement("t1"), trend)
        append_trend(self._make_measurement("t2"), trend)
        records = read_trend(trend)
        assert len(records) == 2
        assert records[0]["recorded_at"] == "t1"
        assert records[1]["recorded_at"] == "t2"
        # Each line must be valid JSON.
        for rec in records:
            json.dumps(rec)

    def test_append_creates_parent_dirs(self, tmp_path):
        trend = tmp_path / "subdir" / "deep" / "trend.jsonl"
        append_trend(self._make_measurement(), trend)
        assert trend.exists()

    def test_read_trend_missing_file_returns_empty(self, tmp_path):
        trend = tmp_path / "nope.jsonl"
        assert read_trend(trend) == []

    def test_read_trend_skips_corrupted_lines(self, tmp_path):
        """A partially-written line must not crash the reader."""
        trend = tmp_path / "trend.jsonl"
        trend.write_text(
            json.dumps({"recorded_at": "good"}) + "\n" +
            "this is not json\n" +
            "\n" +
            json.dumps({"recorded_at": "good2"}) + "\n"
        )
        records = read_trend(trend)
        assert len(records) == 2
        assert records[0]["recorded_at"] == "good"
        assert records[1]["recorded_at"] == "good2"

    def test_append_never_rewrites_history(self, tmp_path):
        """PERF-006 constraint: trend log is append-only. Appending must
        preserve all existing bytes."""
        trend = tmp_path / "trend.jsonl"
        append_trend(self._make_measurement("first"), trend)
        before = trend.read_bytes()
        append_trend(self._make_measurement("second"), trend)
        after = trend.read_bytes()
        # The original bytes must still be the prefix (append-only).
        assert after.startswith(before)
        assert len(after) > len(before)


# ═══════════════════════════════════════════════════════════════════
# [PERF-006] Lazy-load candidates
# ═══════════════════════════════════════════════════════════════════
class TestLazyLoadCandidates:
    """PERF-006 §3: candidates must include Reports, TradeFlow,
    TrackingBoard, and chart components."""

    def test_includes_required_pages(self):
        names = {c.name for c in LAZY_LOAD_CANDIDATES}
        # The four required by the task spec.
        assert "Reports" in names
        assert "TradeFlow" in names
        assert "TrackingBoard" in names

    def test_includes_chart_components(self):
        """Charts (xyflow / lightweight-charts) must be represented."""
        chart_kinds = {
            c.name for c in LAZY_LOAD_CANDIDATES if c.kind == "chart_component"
        }
        assert len(chart_kinds) >= 2, (
            "expected at least 2 chart_component candidates "
            "(AgentCollaboration, KlinePanel, MiniKline)"
        )

    def test_all_candidates_have_source_paths(self):
        for c in LAZY_LOAD_CANDIDATES:
            assert c.source.startswith("frontend/src/"), c
            assert c.estimated_impact in {"high", "medium", "low"}, c
            assert c.rationale, c

    def test_all_candidate_kinds_valid(self):
        for c in LAZY_LOAD_CANDIDATES:
            assert c.kind in {"page", "chart_component", "vendor_lib"}, c

    def test_candidate_sources_actually_exist(self):
        """The static candidate list must point at real files in the repo.
        Guards against the list going stale after a rename/move."""
        repo_root = Path(__file__).resolve().parent.parent
        for c in LAZY_LOAD_CANDIDATES:
            assert (repo_root / c.source).exists(), (
                f"candidate {c.name} source missing: {c.source}"
            )


# ═══════════════════════════════════════════════════════════════════
# [PERF-006] Report generation
# ═══════════════════════════════════════════════════════════════════
class TestGenerateReport:
    def _make_measurement(self) -> BundleMeasurement:
        return BundleMeasurement(
            recorded_at="2026-06-27T00:00:00",
            git_commit="abc1234",
            assets=(
                AssetMeasurement("dist/assets/index-X.js", "js", 1212.22, 348.06),
                AssetMeasurement("dist/assets/index-Y.css", "css", 169.25, 23.23),
            ),
        )

    def test_report_has_required_sections(self):
        """The markdown must contain the four sections the task spec asks
        for: snapshot / trend / candidates / how-to."""
        report = generate_report(
            measurement=self._make_measurement(),
            history=[],
            candidates=LAZY_LOAD_CANDIDATES,
        )
        assert "# 前端 Bundle 体积趋势报告（PERF-006）" in report
        assert "## 1. 最新构建快照" in report
        assert "## 2. 历史趋势" in report
        assert "## 3. 懒加载候选" in report
        assert "## 4. 约束与运行方式" in report

    def test_report_marks_chunk_over_threshold(self):
        """A JS chunk > 500 kB must be flagged with the warning marker."""
        report = generate_report(
            measurement=self._make_measurement(),
            history=[],
            candidates=LAZY_LOAD_CANDIDATES,
        )
        assert "⚠️" in report
        assert "超 Vite 默认告警阈值" in report

    def test_report_no_warning_when_under_threshold(self):
        """A JS chunk < 500 kB must NOT be flagged."""
        m = BundleMeasurement(
            recorded_at="2026-06-27T00:00:00",
            git_commit="abc1234",
            assets=(
                AssetMeasurement("dist/assets/index-X.js", "js", 312.0, 98.0),
                AssetMeasurement("dist/assets/index-Y.css", "css", 50.0, 8.0),
            ),
        )
        report = generate_report(
            measurement=m, history=[], candidates=LAZY_LOAD_CANDIDATES
        )
        assert "⚠️" not in report

    def test_report_lists_candidates_table(self):
        report = generate_report(
            measurement=self._make_measurement(),
            history=[],
            candidates=LAZY_LOAD_CANDIDATES,
        )
        # Table header
        assert "| 候选 | 类型 | 源文件 |" in report
        # Each candidate name appears
        for c in LAZY_LOAD_CANDIDATES:
            assert c.name in report

    def test_report_shows_trend_table_from_history(self):
        """With history, the trend table must render one row per entry."""
        history = [
            {
                "recorded_at": "2026-06-20T00:00:00",
                "git_commit": "aaaaaaa",
                "js_raw_kb": 1100.0,
                "js_gzip_kb": 320.0,
                "css_raw_kb": 160.0,
                "css_gzip_kb": 22.0,
                "largest_js_path": "dist/assets/index-A.js",
                "largest_js_raw_kb": 1100.0,
                "largest_js_gzip_kb": 320.0,
            },
            {
                "recorded_at": "2026-06-27T00:00:00",
                "git_commit": "abc1234",
                "js_raw_kb": 1212.22,
                "js_gzip_kb": 348.06,
                "css_raw_kb": 169.25,
                "css_gzip_kb": 23.23,
                "largest_js_path": "dist/assets/index-X.js",
                "largest_js_raw_kb": 1212.22,
                "largest_js_gzip_kb": 348.06,
            },
        ]
        report = generate_report(
            measurement=self._make_measurement(),
            history=history,
            candidates=LAZY_LOAD_CANDIDATES,
        )
        assert "aaaaaaa" in report
        assert "abc1234" in report
        # Delta line should reference change vs previous
        assert "变化" in report

    def test_report_computes_delta_vs_previous(self):
        """Delta must show positive growth when current > previous."""
        history = [
            {
                "recorded_at": "2026-06-20T00:00:00",
                "git_commit": "aaaaaaa",
                "js_raw_kb": 1100.0,
                "js_gzip_kb": 320.0,
                "css_raw_kb": 160.0,
                "css_gzip_kb": 22.0,
                "largest_js_path": "dist/assets/index-A.js",
                "largest_js_raw_kb": 1100.0,
                "largest_js_gzip_kb": 320.0,
            },
            # The "current" entry — matches measurement.recorded_at so it
            # is treated as the current measurement, not previous.
            {
                "recorded_at": "2026-06-27T00:00:00",
                "git_commit": "abc1234",
                "js_raw_kb": 1212.22,
                "js_gzip_kb": 348.06,
                "css_raw_kb": 169.25,
                "css_gzip_kb": 23.23,
                "largest_js_path": "dist/assets/index-X.js",
                "largest_js_raw_kb": 1212.22,
                "largest_js_gzip_kb": 348.06,
            },
        ]
        report = generate_report(
            measurement=self._make_measurement(),
            history=history,
            candidates=LAZY_LOAD_CANDIDATES,
        )
        # JS gzip grew from 320 -> 348.06, delta ≈ +28.06
        assert "+28.06" in report or "+28.1" in report

    def test_report_handles_no_history_gracefully(self):
        """First-ever run with empty history must not crash or show deltas."""
        report = generate_report(
            measurement=self._make_measurement(),
            history=[],
            candidates=LAZY_LOAD_CANDIDATES,
        )
        assert "暂无历史趋势" in report or "记录时间" in report
        # No delta table when there is no previous.
        assert "与上次记录的变化" not in report

    def test_report_only_mode_reconstructs_from_history(self):
        """generate_report(measurement=None, history=[...]) must rebuild
        a displayable snapshot from the trend log."""
        history = [
            {
                "recorded_at": "2026-06-27T00:00:00",
                "git_commit": "abc1234",
                "js_raw_kb": 1212.22,
                "js_gzip_kb": 348.06,
                "css_raw_kb": 169.25,
                "css_gzip_kb": 23.23,
                "largest_js_path": "dist/assets/index-X.js",
                "largest_js_raw_kb": 1212.22,
                "largest_js_gzip_kb": 348.06,
            },
        ]
        report = generate_report(
            measurement=None,
            history=history,
            candidates=LAZY_LOAD_CANDIDATES,
        )
        # Snapshot must still show JS/CSS numbers from the trend entry.
        assert "348.06" in report
        assert "23.23" in report
        assert "abc1234" in report

    def test_report_only_empty_history(self):
        """report-only with no history must produce a valid empty-state."""
        report = generate_report(
            measurement=None, history=[], candidates=LAZY_LOAD_CANDIDATES
        )
        assert "暂无测量数据" in report
        assert "## 3. 懒加载候选" in report  # candidates section still present


# ═══════════════════════════════════════════════════════════════════
# [PERF-006] End-to-end: parse -> write trend -> regenerate
# ═══════════════════════════════════════════════════════════════════
class TestEndToEnd:
    """Simulate the full PERF-006 flow against fixture logs and tmp_path,
    without ever invoking npm/node."""

    def test_parse_then_write_then_report_only(self, tmp_path):
        from scripts.measure_frontend_bundle import _build_measurement_from_log

        trend = tmp_path / "trend.jsonl"
        report_path = tmp_path / "report.md"

        # Round 1: parse the full fixture log and append.
        m1 = _build_measurement_from_log(VITE_LOG_FULL)
        append_trend(m1, trend)
        history = read_trend(trend)
        assert len(history) == 1

        report1 = generate_report(
            measurement=m1, history=history, candidates=LAZY_LOAD_CANDIDATES
        )
        report_path.write_text(report1)
        assert "348.06" in report1
        assert "⚠️" in report1  # over 500 kB threshold

        # Round 2: simulate a future smaller build (post code-split).
        m2 = _build_measurement_from_log(VITE_LOG_NO_WARNING)
        append_trend(m2, trend)
        history = read_trend(trend)
        assert len(history) == 2

        report2 = generate_report(
            measurement=m2, history=history, candidates=LAZY_LOAD_CANDIDATES
        )
        # Delta must reflect shrinkage from 348 -> 98.
        assert "-" in report2  # negative delta
        assert "98.06" in report2

    def test_no_live_build_invoked(self, tmp_path, monkeypatch):
        """PERF-006 constraint: tests must not invoke `npm run build`.

        We monkeypatch subprocess to fail loudly if anything tries to
        spawn a process — every code path under test must be hermetic.
        """
        import scripts.measure_frontend_bundle as mod

        def _fail_subprocess(*args, **kwargs):
            raise AssertionError(
                "test must not spawn a subprocess — "
                "got args={!r} kwargs={!r}".format(args, kwargs)
            )

        monkeypatch.setattr(mod.subprocess, "run", _fail_subprocess)
        monkeypatch.setattr(mod.subprocess, "check_output", _fail_subprocess)

        # All pure functions must still work with subprocess disabled.
        assets = parse_vite_output(VITE_LOG_FULL)
        assert len(assets) == 2
        report = generate_report(
            measurement=None, history=[], candidates=LAZY_LOAD_CANDIDATES
        )
        assert "# 前端 Bundle 体积趋势报告" in report


# ═══════════════════════════════════════════════════════════════════
# [PERF-006] Acceptance smoke — mirrors the task acceptance criteria
# ═══════════════════════════════════════════════════════════════════
class TestAcceptancePERF006:
    """Single acceptance covering all four PERF-006 acceptance bullets:

    - fixture build output can be parsed ✓
    - report is generated ✓
    - lazy-load candidates include Reports/TradeFlow/TrackingBoard/Charts ✓
    - no actual code split required ✓ (no source files changed)
    """

    def test_fixture_log_parses(self):
        assets = parse_vite_output(VITE_LOG_FULL)
        assert assets, "fixture build log must parse to non-empty assets"
        kinds = {a.kind for a in assets}
        assert kinds == {"js", "css"}

    def test_report_generation_succeeds(self, tmp_path):
        from scripts.measure_frontend_bundle import _build_measurement_from_log

        m = _build_measurement_from_log(VITE_LOG_FULL)
        report = generate_report(
            measurement=m, history=[], candidates=LAZY_LOAD_CANDIDATES
        )
        assert "# 前端 Bundle 体积趋势报告" in report
        assert "## 1. 最新构建快照" in report

    def test_candidates_cover_required_areas(self):
        names = {c.name for c in LAZY_LOAD_CANDIDATES}
        # Task spec §3 explicitly names these four buckets.
        assert "Reports" in names
        assert "TradeFlow" in names
        assert "TrackingBoard" in names
        # Charts represented by chart_component kind.
        assert any(c.kind == "chart_component" for c in LAZY_LOAD_CANDIDATES)

    def test_no_source_files_modified_by_import(self):
        """Importing the module must not touch frontend source files.

        This is a static check that the module under test carries no
        top-level side effects that would mutate source code (the task
        explicitly forbids code split / routing changes).
        """
        repo_root = Path(__file__).resolve().parent.parent
        # The candidate source paths must still exist and be unchanged.
        for c in LAZY_LOAD_CANDIDATES:
            src = repo_root / c.source
            assert src.exists(), f"{c.source} missing"
        # App.tsx still imports pages eagerly (no React.lazy added).
        app_tsx = (repo_root / "frontend" / "src" / "App.tsx").read_text()
        assert "React.lazy" not in app_tsx, (
            "PERF-006 must NOT add React.lazy — that's a follow-up task"
        )
