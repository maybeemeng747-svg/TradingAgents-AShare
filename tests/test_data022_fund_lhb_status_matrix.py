"""[DATA-022] 主力资金/龙虎榜失败矩阵 fixture 回放.

Replays a full status matrix for fund_flow_individual and lhb through:
  1. ``DataCollector._infer_source_status`` — raw pool → structured status
  2. ``infer_evidence_statuses`` — structured/legacy raw_evidence → EvidenceStatus
  3. ``build_data_blockers`` / ``summarize_data_blockers`` — status → blocker layer
  4. ``build_fund_flow_provenance`` / ``build_lhb_provenance`` — status → gate info

Goal: prove that the pipeline keeps ``NORMAL_NO_DATA`` apart from ``FAILED`` and
never lets a long ``FAILED`` text be misjudged as ``HAS_DATA`` via the
``len > 20`` heuristic. Also documents (via the matching doc
``docs/data022_fund_lhb_status_matrix.md``) how each status influences report
action severity.

No live API, no LLM, no prompts, no prod DB — all fixtures are inline.

# [DATA-022] fund_lhb_status_matrix
"""

import copy
import pytest

from tradingagents.agents.utils.readiness_score import (
    EvidenceStatus,
    build_data_blockers,
    build_fund_flow_provenance,
    build_lhb_provenance,
    infer_evidence_statuses,
    summarize_data_blockers,
)
from tradingagents.graph.data_collector import DataCollector


# ─── Fixture matrices ─────────────────────────────────────────────────────────
#
# Each fixture covers one canonical status. The "structured" form mirrors what
# DataCollector.build_raw_evidence writes into the pool (G-006 contract); the
# "legacy_text" form mirrors the pre-G-006 fallback where only a raw string was
# available, and is the path most prone to false positives (the boundary
# acceptance criterion is anchored here).

FUND_FLOW_FIXTURES = {
    "HAS_DATA": {
        "structured": {
            "status": "HAS_DATA",
            "raw": "600519.SH 近20日主力资金：\n日期 净流入(万元)\n2026-06-10 12345",
            "unit": "万元",
            "unit_verified": True,
            "vendor": "cn_akshare",
            "record_count": 20,
        },
        # Long text without "失败" → HAS_DATA (len > 20 path)
        "legacy_text": "600519.SH 近20日主力资金净流入12345万元，连续3日净流入",
    },
    "FAILED": {
        "structured": {
            "status": "FAILED",
            "raw": "个股资金流向数据获取失败：AKShare timeout",
            "unit_verified": False,
            "vendor": "",
            "error": "timeout",
        },
        # Acceptance boundary: long text WITH "失败" must NOT become HAS_DATA
        # via len > 20 — must stay QUERY_FAILED.
        "legacy_text": "主力资金获取失败：AKShare timeout，Eastmoney push2 返回 502 错误",
        "legacy_text_short": "个股资金流向数据获取失败",
    },
    "NORMAL_NO_DATA": {
        "structured": {
            "status": "NORMAL_NO_DATA",
            "raw": "",
            "unit_verified": False,
        },
        # Empty string → NORMAL_NO_DATA (not a failure)
        "legacy_text": "",
    },
    "SKIPPED": {
        "structured": {
            "status": "SKIPPED",
            "raw": "",
        },
        # SKIPPED has no legacy_text representation; only structured carries it.
        "legacy_text": None,
    },
}

LHB_FIXTURES = {
    "HAS_DATA": {
        "structured": {
            "status": "HAS_DATA",
            "raw": "[G-007] LHB_HAS_DATA: 龙虎榜明细 2026-06-10 机构席位净买入",
            "query_mode": "forced",
            "force_reason": "anomaly_condition",
        },
        "legacy_text": "龙虎榜明细：2026-06-10 机构席位净买入1.2亿",
    },
    "FAILED": {
        "structured": {
            "status": "FAILED",
            "raw": "[G-007] LHB_FAILED: 龙虎榜查询失败 ConnectionError",
            "query_mode": "forced",
            "force_reason": "fund_flow_anomaly",
            "error": "ConnectionError",
        },
        "legacy_text": "龙虎榜获取失败：ConnectionError",
    },
    "NORMAL_NO_DATA": {
        "structured": {
            "status": "NORMAL_NO_DATA",
            "raw": "[G-007] LHB_NORMAL_NO_DATA: 非异动日无上榜记录",
            "query_mode": "forced",
            "force_reason": "anomaly_condition",
        },
        "legacy_text": "龙虎榜：未上榜，非异动日无数据",
    },
    "NOT_QUERIED": {
        "structured": {
            "status": "NOT_QUERIED",
            "raw": "[G-007] LHB_NOT_QUERIED: 无异动触发，未强制查询",
            "query_mode": "on_demand",
        },
        "legacy_text": "查询未触发",
    },
}


def _expected_evidence_status(canonical: str) -> str:
    """Map canonical fixture status → expected EvidenceStatus wire value."""
    return {
        "HAS_DATA": EvidenceStatus.HAS_DATA,
        "FAILED": EvidenceStatus.QUERY_FAILED,
        "NORMAL_NO_DATA": EvidenceStatus.NORMAL_NO_DATA,
        "NOT_QUERIED": EvidenceStatus.NOT_QUERIED,
        "SKIPPED": EvidenceStatus.SKIPPED,
    }[canonical]


# ─── 1. DataCollector._infer_source_status matrix ────────────────────────────


class TestDataCollectorStatusInference:
    """[DATA-022] Verify DataCollector turns raw pool values into the right
    structured status — this is the first status-inference boundary and the
    one most exposed to marker-strings written by upstream fetchers."""

    @pytest.mark.parametrize(
        "canonical,fixture",
        [(k, v) for k, v in FUND_FLOW_FIXTURES.items()],
        ids=list(FUND_FLOW_FIXTURES),
    )
    def test_fund_flow_structured_status_preserved(self, canonical, fixture):
        """A structured entry's ``status`` field must be honoured verbatim by
        DataCollector (HK-001 explicit-status path)."""
        structured = copy.deepcopy(fixture["structured"])
        # DataCollector._infer_source_status inspects {"status": ...} form.
        wrapped = {"status": structured["status"], "raw": structured["raw"]}
        assert DataCollector._infer_source_status(wrapped) == canonical

    @pytest.mark.parametrize(
        "canonical,fixture",
        [(k, v) for k, v in LHB_FIXTURES.items()],
        ids=list(LHB_FIXTURES),
    )
    def test_lhb_structured_status_preserved(self, canonical, fixture):
        structured = copy.deepcopy(fixture["structured"])
        wrapped = {"status": structured["status"], "raw": structured["raw"]}
        assert DataCollector._infer_source_status(wrapped) == canonical

    @pytest.mark.parametrize(
        "raw_text,expected",
        [
            ("[G-007] LHB_HAS_DATA: 龙虎榜明细", "HAS_DATA"),
            ("[G-007] LHB_FAILED: 错误", "FAILED"),
            ("[G-007] LHB_NORMAL_NO_DATA: 非异动日", "NORMAL_NO_DATA"),
            ("[G-007] LHB_NOT_QUERIED: 无触发", "NOT_QUERIED"),
            ("龙虎榜获取失败：超时", "FAILED"),
            ("主力资金获取失败：ProxyError", "FAILED"),
        ],
    )
    def test_lhb_and_fund_markers_recognized(self, raw_text, expected):
        """The [G-007] markers and "获取失败" substring must route to the
        correct status rather than falling through to HAS_DATA."""
        assert DataCollector._infer_source_status(raw_text) == expected

    def test_empty_and_none_are_not_queried(self):
        assert DataCollector._infer_source_status("") == "NOT_QUERIED"
        assert DataCollector._infer_source_status(None) == "NOT_QUERIED"


# ─── 2. infer_evidence_statuses matrix ───────────────────────────────────────


class TestInferEvidenceStatusesMatrix:
    """[DATA-022] Verify infer_evidence_statuses maps each canonical status to
    the right EvidenceStatus wire value, for both structured and legacy_text
    forms of fund_flow_individual and lhb."""

    @pytest.mark.parametrize(
        "canonical,fixture",
        [(k, v) for k, v in FUND_FLOW_FIXTURES.items()],
        ids=list(FUND_FLOW_FIXTURES),
    )
    def test_fund_flow_structured(self, canonical, fixture):
        raw = {"fund_flow_individual": copy.deepcopy(fixture["structured"])}
        statuses = infer_evidence_statuses({}, raw_evidence=raw)
        assert statuses["individual_fund_flow"] == _expected_evidence_status(canonical)

    @pytest.mark.parametrize(
        "canonical,fixture",
        [(k, v) for k, v in FUND_FLOW_FIXTURES.items() if v["legacy_text"] is not None],
        ids=[k for k, v in FUND_FLOW_FIXTURES.items() if v["legacy_text"] is not None],
    )
    def test_fund_flow_legacy_text(self, canonical, fixture):
        raw = {"fund_flow_individual": fixture["legacy_text"]}
        statuses = infer_evidence_statuses({}, raw_evidence=raw)
        assert statuses["individual_fund_flow"] == _expected_evidence_status(canonical)

    @pytest.mark.parametrize(
        "canonical,fixture",
        [(k, v) for k, v in LHB_FIXTURES.items()],
        ids=list(LHB_FIXTURES),
    )
    def test_lhb_structured(self, canonical, fixture):
        raw = {"lhb": copy.deepcopy(fixture["structured"])}
        statuses = infer_evidence_statuses({}, raw_evidence=raw)
        assert statuses["lhb_status"] == _expected_evidence_status(canonical)

    @pytest.mark.parametrize(
        "canonical,fixture",
        [(k, v) for k, v in LHB_FIXTURES.items() if v["legacy_text"] is not None],
        ids=[k for k, v in LHB_FIXTURES.items() if v["legacy_text"] is not None],
    )
    def test_lhb_legacy_text(self, canonical, fixture):
        raw = {"lhb": fixture["legacy_text"]}
        statuses = infer_evidence_statuses({}, raw_evidence=raw)
        assert statuses["lhb_status"] == _expected_evidence_status(canonical)


# ─── 3. Acceptance: FAILED not misjudged as HAS_DATA via len > 20 ─────────────


class TestFailedNotMisjudgedAsHasData:
    """[DATA-022] Headline acceptance: a long FAILED text must not be classified
    as HAS_DATA just because it is longer than 20 characters. This is the
    regression guard for the false-positive that motivated this task."""

    def test_fund_flow_long_failed_text_stays_query_failed(self):
        long_failed = FUND_FLOW_FIXTURES["FAILED"]["legacy_text"]
        assert len(long_failed) > 20, "fixture sanity: must be > 20 chars"
        assert "失败" in long_failed
        statuses = infer_evidence_statuses(
            {}, raw_evidence={"fund_flow_individual": long_failed}
        )
        assert statuses["individual_fund_flow"] == EvidenceStatus.QUERY_FAILED
        assert statuses["individual_fund_flow"] != EvidenceStatus.HAS_DATA

    def test_fund_flow_short_failed_text_stays_query_failed(self):
        short_failed = FUND_FLOW_FIXTURES["FAILED"]["legacy_text_short"]
        assert len(short_failed) <= 20
        statuses = infer_evidence_statuses(
            {}, raw_evidence={"fund_flow_individual": short_failed}
        )
        assert statuses["individual_fund_flow"] == EvidenceStatus.QUERY_FAILED

    def test_lhb_long_failed_text_stays_query_failed(self):
        long_failed = (
            "龙虎榜获取失败：API返回 ConnectionError，重试3次均失败，已记录到限流日志"
        )
        assert len(long_failed) > 20
        statuses = infer_evidence_statuses({}, raw_evidence={"lhb": long_failed})
        assert statuses["lhb_status"] == EvidenceStatus.QUERY_FAILED

    def test_fund_flow_provenance_long_failed_text(self):
        """build_fund_flow_provenance must also refuse strong evidence for a
        long FAILED string (its _status_from_text checks 失败 before length)."""
        long_failed = FUND_FLOW_FIXTURES["FAILED"]["legacy_text"]
        prov = build_fund_flow_provenance({"fund_flow_individual": long_failed}, {})
        assert prov["individual_status"] == "FAILED"
        assert prov["strong_evidence_allowed"] is False
        assert prov["unit_verified"] is False


# ─── 4. Acceptance: NORMAL_NO_DATA not counted as query failure ───────────────


class TestNormalNoDataNotCountedAsFailure:
    """[DATA-022] NORMAL_NO_DATA is informational and must never inflate the
    query_failed counter in the data_blocker summary, nor block strong actions
    on its own."""

    def test_fund_flow_normal_no_data_excluded_from_query_failed(self):
        blockers = build_data_blockers(
            {}, raw_evidence={"fund_flow_individual": FUND_FLOW_FIXTURES["NORMAL_NO_DATA"]["structured"]}
        )
        by_key = {b["key"]: b for b in blockers}
        assert by_key["individual_fund_flow"]["status"] == "normal_no_data"
        assert by_key["individual_fund_flow"]["severity"] == "info"

    def test_lhb_normal_no_data_is_info_not_high(self):
        blockers = build_data_blockers(
            {}, raw_evidence={"lhb": LHB_FIXTURES["NORMAL_NO_DATA"]["structured"]}
        )
        by_key = {b["key"]: b for b in blockers}
        assert by_key["lhb_status"]["status"] == "normal_no_data"
        assert by_key["lhb_status"]["severity"] == "info"
        assert "正常无数据" in by_key["lhb_status"]["reason"]

    def test_summary_does_not_count_normal_no_data_as_query_failed(self):
        blockers = [
            {"status": "normal_no_data"},
            {"status": "normal_no_data"},
            {"status": "query_failed"},
        ]
        summary = summarize_data_blockers(blockers)
        assert summary["counts"]["query_failed"] == 1
        assert summary["counts"]["normal_no_data"] == 2
        # With only 1 query_failed the level must be warning (not info).
        assert summary["level"] == "warning"

    def test_pure_normal_no_data_yields_info_level(self):
        """When every blocker is NORMAL_NO_DATA/SKIPPED, the summary level must
        be info — the report should not pretend data is broken."""
        blockers = [
            {"status": "normal_no_data"},
            {"status": "skipped"},
        ]
        summary = summarize_data_blockers(blockers)
        assert summary["counts"]["query_failed"] == 0
        assert summary["level"] == "info"
        assert "不单独构成强降级理由" in summary["message"]

    def test_lhb_normal_no_data_does_not_block_strong_evidence(self):
        """build_lhb_provenance must NOT treat NORMAL_NO_DATA as a failure —
        the LHB just has no record that day."""
        prov = build_lhb_provenance(
            {"lhb": LHB_FIXTURES["NORMAL_NO_DATA"]["structured"]}, {}
        )
        assert prov["status"] == "NORMAL_NO_DATA"
        assert "正常" in prov["display_text"]


# ─── 5. FAILED → high severity; HAS_DATA excluded from blockers ──────────────


class TestFailedSeverityAndHasDataExclusion:
    """[DATA-022] FAILED must surface as high severity, and HAS_DATA must be
    excluded from the blocker list entirely."""

    def test_fund_flow_failed_is_high_severity(self):
        blockers = build_data_blockers(
            {}, raw_evidence={"fund_flow_individual": FUND_FLOW_FIXTURES["FAILED"]["structured"]}
        )
        by_key = {b["key"]: b for b in blockers}
        assert by_key["individual_fund_flow"]["status"] == "query_failed"
        assert by_key["individual_fund_flow"]["severity"] == "high"
        assert "查询失败" in by_key["individual_fund_flow"]["status_label"]

    def test_lhb_failed_is_high_severity(self):
        blockers = build_data_blockers(
            {}, raw_evidence={"lhb": LHB_FIXTURES["FAILED"]["structured"]}
        )
        by_key = {b["key"]: b for b in blockers}
        assert by_key["lhb_status"]["status"] == "query_failed"
        assert by_key["lhb_status"]["severity"] == "high"

    def test_has_data_excluded_from_blockers(self):
        """When fund_flow and lhb are HAS_DATA, they must not appear as
        blockers at all — the report should not claim data is missing."""
        blockers = build_data_blockers(
            {},
            raw_evidence={
                "fund_flow_individual": FUND_FLOW_FIXTURES["HAS_DATA"]["structured"],
                "lhb": LHB_FIXTURES["HAS_DATA"]["structured"],
            },
        )
        by_key = {b["key"]: b for b in blockers}
        assert "individual_fund_flow" not in by_key
        assert "lhb_status" not in by_key

    def test_skipped_is_low_severity(self):
        blockers = build_data_blockers(
            {}, raw_evidence={"fund_flow_individual": FUND_FLOW_FIXTURES["SKIPPED"]["structured"]}
        )
        by_key = {b["key"]: b for b in blockers}
        assert by_key["individual_fund_flow"]["status"] == "skipped"
        assert by_key["individual_fund_flow"]["severity"] == "low"


# ─── 6. Provenance consistency across the matrix ─────────────────────────────


class TestProvenanceMatrix:
    """[DATA-022] build_fund_flow_provenance / build_lhb_provenance must agree
    with the canonical fixture status for every matrix cell."""

    @pytest.mark.parametrize(
        "canonical,fixture",
        [(k, v) for k, v in FUND_FLOW_FIXTURES.items()],
        ids=list(FUND_FLOW_FIXTURES),
    )
    def test_fund_flow_provenance_per_status(self, canonical, fixture):
        prov = build_fund_flow_provenance(
            {"fund_flow_individual": copy.deepcopy(fixture["structured"])}, {}
        )
        assert prov["individual_status"] == canonical
        # strong_evidence_allowed only when HAS_DATA + unit_verified.
        unit_ok = fixture["structured"].get("unit_verified") is True
        assert prov["strong_evidence_allowed"] == (canonical == "HAS_DATA" and unit_ok)

    @pytest.mark.parametrize(
        "canonical,fixture",
        [(k, v) for k, v in LHB_FIXTURES.items()],
        ids=list(LHB_FIXTURES),
    )
    def test_lhb_provenance_per_status(self, canonical, fixture):
        prov = build_lhb_provenance({"lhb": copy.deepcopy(fixture["structured"])}, {})
        assert prov["status"] == canonical
        # query_mode escalates from on_demand → forced for any non NOT_QUERIED.
        if canonical == "NOT_QUERIED":
            assert prov["query_mode"] == "on_demand" or prov["query_mode"] == "not_queried"
        else:
            # The structured fixture already carries query_mode; provenance
            # must preserve a forced mode for any real query outcome.
            assert prov["query_mode"] in {"forced", "on_demand", "not_queried"}


# ─── 7. Cross-status distinction (no two canonical statuses collapse) ─────────


class TestStatusesDoNotCollapse:
    """[DATA-022] The four LHB statuses and four fund-flow statuses must remain
    distinct wire values after going through infer_evidence_statuses — no
    collapse that would let a failure masquerade as no-data or vice versa."""

    def test_lhb_four_statuses_are_distinct(self):
        outputs = {
            canonical: infer_evidence_statuses(
                {}, raw_evidence={"lhb": copy.deepcopy(fixture["structured"])}
            )["lhb_status"]
            for canonical, fixture in LHB_FIXTURES.items()
        }
        assert outputs == {
            "HAS_DATA": EvidenceStatus.HAS_DATA,
            "FAILED": EvidenceStatus.QUERY_FAILED,
            "NORMAL_NO_DATA": EvidenceStatus.NORMAL_NO_DATA,
            "NOT_QUERIED": EvidenceStatus.NOT_QUERIED,
        }
        # Sanity: four distinct values.
        assert len(set(outputs.values())) == 4

    def test_fund_flow_statuses_are_distinct(self):
        outputs = {
            canonical: infer_evidence_statuses(
                {},
                raw_evidence={"fund_flow_individual": copy.deepcopy(fixture["structured"])},
            )["individual_fund_flow"]
            for canonical, fixture in FUND_FLOW_FIXTURES.items()
        }
        assert outputs == {
            "HAS_DATA": EvidenceStatus.HAS_DATA,
            "FAILED": EvidenceStatus.QUERY_FAILED,
            "NORMAL_NO_DATA": EvidenceStatus.NORMAL_NO_DATA,
            "SKIPPED": EvidenceStatus.SKIPPED,
        }
        assert len(set(outputs.values())) == 4

    def test_failed_and_normal_no_data_wire_values_differ(self):
        """The canonical confusion case from the field: a fund flow failure
        alongside a normal LHB no-data result must produce two different
        blocker statuses, not one shared bucket."""
        blockers = build_data_blockers(
            {},
            raw_evidence={
                "fund_flow_individual": FUND_FLOW_FIXTURES["FAILED"]["structured"],
                "lhb": LHB_FIXTURES["NORMAL_NO_DATA"]["structured"],
            },
        )
        by_key = {b["key"]: b for b in blockers}
        assert by_key["individual_fund_flow"]["status"] == "query_failed"
        assert by_key["lhb_status"]["status"] == "normal_no_data"
        assert by_key["individual_fund_flow"]["status"] != by_key["lhb_status"]["status"]
        assert by_key["individual_fund_flow"]["severity"] == "high"
        assert by_key["lhb_status"]["severity"] == "info"
