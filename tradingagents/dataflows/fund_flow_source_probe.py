# [DATA-024] fund_flow_source_probe
"""主力资金供应商 fallback live-smoke dry-run 与错误归因.

为主力资金 (``fund_flow`` / ``get_individual_fund_flow``) 提供一个独立的探针:

1. **fixture 回放** (默认): 用 5 类 + HAS_DATA 基线 fixture 跑错误归因分类器,
   证明 network/field-change/no-data/rate-limit/unknown-unit 五类典型故障
   能被正确识别, 不会被误判成 HAS_DATA.
2. **live-smoke** (显式开关): 通过 ``TA_LIVE_DATA_SMOKE=1`` 与 ``live_smoke=True``
   双重门禁后, 才会真正调用 ``route_to_vendor`` 走完整 fallback 链, 抽样验证
   cn_akshare / cn_astock 实际可用性, 并归因到上述 5 类.
3. **错误归因报告**: 渲染成 markdown 表格, 写到
   ``docs/data_source_reports/fund-flow-probe-YYYY-MM-DD.md``.
4. **接入 DATA-023 能力矩阵**: 通过 ``build_capability_matrix_overlay`` 输出
   一个只读 overlay, 汇总最近一次 probe 的 5 类计数, 供 capability matrix
   API / 文档附加显示, 不修改 matrix 本身.

执行约束 (与任务一致):
- 默认 fixture; live-smoke 必须显式开关 + 环境变量双重门禁.
- 不打印密钥 / cookie / token / Authorization header.
- 不把板块资金流 (board_fund_flow) 当个股资金流 (fund_flow).
- 不写生产 tradingagents.db; 不调用 LLM; 不改 prompts.

使用示例 (库):
    from tradingagents.dataflows.fund_flow_source_probe import (
        run_fund_flow_probe,
        render_fund_flow_probe_report,
        build_capability_matrix_overlay,
        FundFlowErrorType,
    )
    report = run_fund_flow_probe()                      # fixture dry-run
    print(render_fund_flow_probe_report(report))
    overlay = build_capability_matrix_overlay(report)

使用示例 (CLI):
    python scripts/run_fund_flow_source_probe.py                 # fixture dry-run
    TA_LIVE_DATA_SMOKE=1 python scripts/run_fund_flow_source_probe.py --live-smoke --symbols 600519.SH
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .source_catalog import DataType, get_fallback_chain, get_primary_source
from .source_freshness_report import (
    _detect_failed,
    _detect_normal_no_data,
    _detect_rate_limited,
)


# ── 常量 ───────────────────────────────────────────────────────────────

_LIVE_ENV = "TA_LIVE_DATA_SMOKE"
_MAX_SYMBOLS = 3
DEFAULT_PROBE_SYMBOLS: List[str] = ["600519.SH", "000001.SZ", "603629.SH"]

# 个股资金流的预期单位 — 来自 source_catalog DataType.FUND_FLOW primary 源
EXPECTED_UNIT = "万元"

# route_to_vendor 上 fund_flow 的方法名
FUND_FLOW_METHOD = "get_individual_fund_flow"


# ── 错误归因枚举 ──────────────────────────────────────────────────────


class FundFlowErrorType:
    """主力资金 probe 错误归因类型.

    与 ``SourceFreshnessStatus`` 的 6 态对应关系:
      - NETWORK_ERROR  ↔ FAILED (网络/连接/超时)
      - RATE_LIMITED   ↔ RATE_LIMITED
      - FIELD_CHANGE   ↔ UNIT_UNVERIFIED (单位 / 字段契约变更)
      - UNKNOWN_UNIT   ↔ UNIT_UNVERIFIED (数据存在但单位探测不到)
      - NO_DATA        ↔ NORMAL_NO_DATA
      - OK             ↔ HAS_DATA
      - UNKNOWN        ↔ 兜底
    """

    OK = "ok"
    NETWORK_ERROR = "network_error"
    RATE_LIMITED = "rate_limited"
    FIELD_CHANGE = "field_change"
    UNKNOWN_UNIT = "unknown_unit"
    NO_DATA = "no_data"
    UNKNOWN = "unknown"

    ALL = [
        OK,
        NETWORK_ERROR,
        RATE_LIMITED,
        FIELD_CHANGE,
        UNKNOWN_UNIT,
        NO_DATA,
        UNKNOWN,
    ]

    # 5 类任务明确要求的归因 (不含 OK / UNKNOWN)
    REQUIRED_ATTRIBUTION = [
        NETWORK_ERROR,
        FIELD_CHANGE,
        NO_DATA,
        RATE_LIMITED,
        UNKNOWN_UNIT,
    ]

    LABEL_CN: Dict[str, str] = {
        OK: "正常",
        NETWORK_ERROR: "网络失败",
        RATE_LIMITED: "限流",
        FIELD_CHANGE: "接口字段变更",
        UNKNOWN_UNIT: "单位不明",
        NO_DATA: "正常无数据",
        UNKNOWN: "未知",
    }

    # 对应 SourceFreshnessStatus (DATA-018) 的桥接映射, 用于和现有 freshness
    # 报告对齐语义. UNKNOWN 默认落到 FAILED.
    TO_FRESHNESS_STATUS: Dict[str, str] = {
        OK: "HAS_DATA",
        NETWORK_ERROR: "FAILED",
        RATE_LIMITED: "RATE_LIMITED",
        FIELD_CHANGE: "UNIT_UNVERIFIED",
        UNKNOWN_UNIT: "UNIT_UNVERIFIED",
        NO_DATA: "NORMAL_NO_DATA",
        UNKNOWN: "FAILED",
    }


# ── 数据模型 ──────────────────────────────────────────────────────────


@dataclass
class FundFlowProbeResult:
    """单次 fund_flow probe 结果.

    ``error_type`` 是 5 类错误归因之一 (见 ``FundFlowErrorType``);
    ``status`` 沿用 SourceFreshnessStatus 的 6 态语义, 方便和现有
    data health 报告联动.
    """

    symbol: str = ""
    vendor: str = ""
    status: str = "NOT_RUN"  # SourceFreshnessStatus 6 态之一 / SKIPPED / NOT_RUN
    error_type: str = FundFlowErrorType.UNKNOWN
    unit: str = ""
    unit_verified: bool = False
    record_count: int = 0
    latency_ms: float = 0.0
    is_fallback: bool = False
    fallback_from: str = ""
    error: str = ""
    diagnosis: str = ""
    fixture_id: str = ""
    probe_mode: str = "fixture"  # fixture / live / skipped

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "vendor": self.vendor,
            "status": self.status,
            "error_type": self.error_type,
            "error_type_label_cn": FundFlowErrorType.LABEL_CN.get(
                self.error_type, FundFlowErrorType.UNKNOWN
            ),
            "unit": self.unit,
            "unit_verified": self.unit_verified,
            "record_count": self.record_count,
            "latency_ms": round(self.latency_ms, 1),
            "is_fallback": self.is_fallback,
            "fallback_from": self.fallback_from,
            "error": (self.error or "")[:200],
            "diagnosis": self.diagnosis,
            "fixture_id": self.fixture_id,
            "probe_mode": self.probe_mode,
        }


@dataclass
class FundFlowProbeReport:
    """probe 顶层报告."""

    run_at: str = ""
    date: str = ""
    mode: str = "fixture"  # fixture / live / skipped
    env_gated: bool = True
    symbols: List[str] = field(default_factory=list)
    results: List[FundFlowProbeResult] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)
    fallback_chain: List[str] = field(default_factory=list)
    expected_unit: str = EXPECTED_UNIT

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_at": self.run_at,
            "date": self.date,
            "mode": self.mode,
            "env_gated": self.env_gated,
            "symbols": self.symbols,
            "results": [r.to_dict() for r in self.results],
            "summary": self.summary,
            "fallback_chain": self.fallback_chain,
            "expected_unit": self.expected_unit,
        }


# ── fixture: 5 类失败/无数据 + HAS_DATA 基线 ─────────────────────────
#
# 每个 fixture 用与 DataCollector.build_raw_evidence / fixture_replay 一致
# 的结构化 raw_evidence 形态 (status / raw / unit / unit_verified /
# vendor / record_count / error), 这样分类器面对的是真实数据形态而不是
# 测试专用 shortcut.
#
# 板块资金流 (board_fund_flow) 故意不在这里 — 任务执行约束 §3 明确禁止
# 把板块资金流当个股资金流.

PROBE_FIXTURES: Dict[str, Dict[str, Any]] = {
    "HAS_DATA": {
        "description": "cn_akshare 正常返回 20 日个股资金流",
        "symbol": "600519.SH",
        "vendor": "cn_akshare",
        "raw": (
            "600519.SH 近20日主力资金净流向：\n"
            "日期  收盘价  主力净流入-净额  主力净流入-净占比\n"
            "2026-06-10  1689.00  12345.00  12.34\n"
            "2026-06-09  1675.50  9876.00   9.88\n"
            "2026-06-08  1660.20  -5432.00  -5.43\n"
            "单位：万元"
        ),
        "status": "HAS_DATA",
        "unit": "万元",
        "unit_verified": True,
        "record_count": 20,
        "error": "",
        "expected_error_type": FundFlowErrorType.OK,
    },
    "NETWORK_ERROR": {
        "description": "AKShare ConnectionError — 需 fallback 到 cn_astock/Eastmoney",
        "symbol": "000001.SZ",
        "vendor": "cn_akshare",
        "raw": "个股资金流向数据获取失败：ConnectionError: HTTPSConnectionPool(host=push2his.eastmoney.com, port=443): Max retries exceeded with url:",
        "status": "FAILED",
        "unit": "",
        "unit_verified": False,
        "record_count": 0,
        "error": "ConnectionError: HTTPSConnectionPool Max retries exceeded",
        "expected_error_type": FundFlowErrorType.NETWORK_ERROR,
    },
    "RATE_LIMITED": {
        "description": "Eastmoney push2his 429 — 限流, 退避后重试或切 fallback",
        "symbol": "603629.SH",
        "vendor": "cn_astock",
        "raw": "个股资金流向数据获取失败：HTTPError 429: Too Many Requests — 请求过于频繁，请稍后重试",
        "status": "FAILED",
        "unit": "",
        "unit_verified": False,
        "record_count": 0,
        "error": "HTTPError 429: Too Many Requests — 请求过于频繁",
        "expected_error_type": FundFlowErrorType.RATE_LIMITED,
    },
    "FIELD_CHANGE": {
        "description": "字段契约变更: 单位从 万元 变成 元 — 数值不能直接展示",
        "symbol": "600519.SH",
        "vendor": "cn_akshare",
        "raw": (
            "600519.SH 近20日主力资金：\n"
            "日期  主力净流入-元\n"
            "2026-06-10  123450000.00\n"
            "单位：元"
        ),
        "status": "HAS_DATA",
        "unit": "元",
        "unit_verified": False,
        "record_count": 20,
        "error": "",
        "expected_error_type": FundFlowErrorType.FIELD_CHANGE,
    },
    "NORMAL_NO_DATA": {
        "description": "接口正常但标的近期无资金流记录 (新股 / 停牌 / 退市)",
        "symbol": "600519.SH",
        "vendor": "cn_akshare",
        "raw": "",
        "status": "NORMAL_NO_DATA",
        "unit": "",
        "unit_verified": False,
        "record_count": 0,
        "error": "",
        "expected_error_type": FundFlowErrorType.NO_DATA,
    },
    "UNKNOWN_UNIT": {
        "description": "返回了表格数据但找不到单位标记 — 单位不明, 不能直接入账",
        "symbol": "000001.SZ",
        "vendor": "cn_astock",
        "raw": (
            "000001.SZ 近20日主力资金：\n"
            "日期  主力净流入  小单净流入\n"
            "2026-06-10  12345.00  -6789.00\n"
            "2026-06-09  9876.00   -5432.00"
        ),
        "status": "HAS_DATA",
        "unit": "",
        "unit_verified": False,
        "record_count": 2,
        "error": "",
        "expected_error_type": FundFlowErrorType.UNKNOWN_UNIT,
    },
}


# ── 探测辅助 ──────────────────────────────────────────────────────────

_WAN_YUAN_PATTERN = re.compile(r"单位[：:]\s*万元")
_YUAN_PATTERN = re.compile(r"单位[：:]\s*元")


def _detect_unit(text: str) -> str:
    """从文本里探测显式单位标记; 探测不到返回空串."""
    if not text:
        return ""
    if _WAN_YUAN_PATTERN.search(text):
        return "万元"
    if _YUAN_PATTERN.search(text):
        return "元"
    return ""


def _count_data_lines(text: str) -> int:
    """估算返回文本里的数据行数 (去掉标题 / 空行 / 单位说明)."""
    if not text:
        return 0
    lines: List[str] = []
    for ln in (text or "").strip().split("\n"):
        s = ln.strip()
        if not s:
            continue
        if s.startswith("#"):
            continue
        if s.startswith("单位"):
            continue
        if "近" in s and "主力资金" in s and "：" in s:
            # 标题行: "600519.SH 近20日主力资金净流向："
            continue
        lines.append(s)
    # 第一行通常是表头
    return max(0, len(lines) - 1)


def classify_fund_flow_error_type(
    raw_value: Any = None,
    status: str = "",
    error: str = "",
    unit: str = "",
    unit_verified: Optional[bool] = None,
    expected_unit: str = EXPECTED_UNIT,
) -> str:
    """把一条 fund_flow 探测结果归因到 ``FundFlowErrorType`` 之一.

    优先级 (与 source_freshness_report.classify_source_status 对齐):
      1. RATE_LIMITED — error / raw 命中限流模式
      2. NETWORK_ERROR — error / raw 命中失败模式 (排除限流)
      3. NO_DATA — status=NORMAL_NO_DATA 或 raw 空
      4. FIELD_CHANGE — 数据存在但单位与 expected 不同 (非空)
      5. UNKNOWN_UNIT — 数据存在但单位探测不到
      6. OK — 数据存在且单位校验通过
      7. UNKNOWN — 兜底

    *expected_unit* 默认 ``万元`` (来自 source_catalog DataType.FUND_FLOW).
    """
    raw_str = raw_value if isinstance(raw_value, str) else (
        "" if raw_value is None else str(raw_value)
    )
    err_str = error or ""

    # 1. 限流优先 (最具体的失败类型)
    if err_str and _detect_rate_limited(err_str):
        return FundFlowErrorType.RATE_LIMITED
    if raw_str and _detect_rate_limited(raw_str):
        return FundFlowErrorType.RATE_LIMITED

    # 2. 网络/接口失败模式
    if err_str and _detect_failed(err_str):
        return FundFlowErrorType.NETWORK_ERROR
    if raw_str and _detect_failed(raw_str):
        return FundFlowErrorType.NETWORK_ERROR
    if status == "FAILED":
        return FundFlowErrorType.NETWORK_ERROR

    # 3. 正常无数据
    if status == "NORMAL_NO_DATA":
        return FundFlowErrorType.NO_DATA
    if status in ("NOT_QUERIED", "SKIPPED"):
        return FundFlowErrorType.NO_DATA
    if not raw_str and not err_str:
        return FundFlowErrorType.NO_DATA
    if raw_str and _detect_normal_no_data(raw_str) and "近" not in raw_str[:20]:
        # 文本里只有 NORMAL_NO_DATA marker / "暂无" / "未查询到"
        return FundFlowErrorType.NO_DATA

    # 4-6. 数据存在的细分
    has_data = bool(raw_str) and (
        status in ("HAS_DATA", "") or not status.startswith("FAILED")
    )
    if has_data:
        # 4. 字段契约变更: 单位非空且与预期不同
        if unit and expected_unit and unit != expected_unit:
            return FundFlowErrorType.FIELD_CHANGE
        # 单位探测: 优先用文本里显式标记, 否则 fallback 到入参 unit
        detected = _detect_unit(raw_str) or unit
        if detected and expected_unit and detected != expected_unit:
            return FundFlowErrorType.FIELD_CHANGE
        # 5. 单位不明: 没有任何单位信号 + unit_verified=False
        if not detected and unit_verified is False:
            return FundFlowErrorType.UNKNOWN_UNIT
        if not detected and unit_verified is None:
            return FundFlowErrorType.UNKNOWN_UNIT
        # 6. OK
        return FundFlowErrorType.OK

    return FundFlowErrorType.UNKNOWN


# ── 单条探测 ──────────────────────────────────────────────────────────


def _probe_fixture(
    fixture_id: str,
    fixture: Dict[str, Any],
    *,
    expected_unit: str = EXPECTED_UNIT,
) -> FundFlowProbeResult:
    """对单个 fixture 跑分类器, 生成 probe result."""
    raw = fixture.get("raw", "")
    status = fixture.get("status", "")
    error = fixture.get("error", "")
    unit = fixture.get("unit", "")
    unit_verified = fixture.get("unit_verified")

    error_type = classify_fund_flow_error_type(
        raw_value=raw,
        status=status,
        error=error,
        unit=unit,
        unit_verified=unit_verified,
        expected_unit=expected_unit,
    )

    # 优先用 fixture 显式声明的 record_count, 否则从 raw 估算
    record_count = fixture.get("record_count")
    if record_count is None:
        record_count = _count_data_lines(raw)

    detected_unit = unit or _detect_unit(raw)

    return FundFlowProbeResult(
        symbol=fixture.get("symbol", ""),
        vendor=fixture.get("vendor", ""),
        status=FundFlowErrorType.TO_FRESHNESS_STATUS.get(
            error_type, status or "FAILED"
        ),
        error_type=error_type,
        unit=detected_unit,
        unit_verified=(unit_verified is True),
        record_count=int(record_count or 0),
        latency_ms=0.0,
        is_fallback=False,
        fallback_from="",
        error=error,
        diagnosis=fixture.get("description", ""),
        fixture_id=fixture_id,
        probe_mode="fixture",
    )


def _probe_live_symbol(
    symbol: str,
    *,
    fetch_fn: Optional[Callable[[str], Any]] = None,
    expected_unit: str = EXPECTED_UNIT,
    last_hit_vendor_fn: Optional[Callable[[], str]] = None,
) -> FundFlowProbeResult:
    """对一个 symbol 真实调用 route_to_vendor 拉个股资金流并归因.

    *fetch_fn* / *last_hit_vendor_fn* 用于测试注入; 默认走
    ``tradingagents.dataflows.interface.route_to_vendor`` 全链路 fallback.
    """
    from .interface import get_last_hit_vendor, route_to_vendor

    if fetch_fn is None:
        fetch_fn = lambda sym: route_to_vendor(FUND_FLOW_METHOD, sym)  # noqa: E731
    if last_hit_vendor_fn is None:
        last_hit_vendor_fn = lambda: get_last_hit_vendor(FUND_FLOW_METHOD)  # noqa: E731

    result = FundFlowProbeResult(
        symbol=symbol,
        probe_mode="live",
    )

    t0 = time.monotonic()
    try:
        raw = fetch_fn(symbol)
        result.latency_ms = (time.monotonic() - t0) * 1000
        if isinstance(raw, str):
            detected_unit = _detect_unit(raw)
            record_count = _count_data_lines(raw)
            # 调用方层 (cn_astock) 一般会带 "单位：万元" 标记;
            # cn_akshare 不显式带但契约已知, 默认按 catalog unit 校验.
            unit_for_classify = detected_unit or expected_unit
            unit_verified = bool(detected_unit and detected_unit == expected_unit) or (
                not detected_unit and record_count > 0
            )
            error_type = classify_fund_flow_error_type(
                raw_value=raw,
                status="",
                error="",
                unit=unit_for_classify,
                unit_verified=unit_verified,
                expected_unit=expected_unit,
            )
            result.error_type = error_type
            result.status = FundFlowErrorType.TO_FRESHNESS_STATUS.get(
                error_type, "HAS_DATA"
            )
            result.unit = detected_unit or (expected_unit if unit_verified else "")
            result.unit_verified = unit_verified
            result.record_count = record_count
            result.vendor = last_hit_vendor_fn() or ""
            result.is_fallback = bool(result.vendor) and "astock" in result.vendor
            result.fallback_from = "cn_akshare" if result.is_fallback else ""
            if error_type == FundFlowErrorType.OK:
                result.diagnosis = f"live ok, vendor={result.vendor or 'unknown'}"
            else:
                result.diagnosis = (
                    f"live classified as {error_type}; "
                    f"unit={detected_unit or '(none)'} record_count={record_count}"
                )
        else:
            result.error_type = FundFlowErrorType.UNKNOWN
            result.status = "FAILED"
            result.error = f"unexpected return type: {type(raw).__name__}"
            result.diagnosis = "live returned non-string"
    except Exception as exc:
        result.latency_ms = (time.monotonic() - t0) * 1000
        err_str = f"{type(exc).__name__}: {exc}"
        result.error = err_str
        result.error_type = classify_fund_flow_error_type(
            raw_value="",
            status="FAILED",
            error=err_str,
            unit="",
            unit_verified=False,
            expected_unit=expected_unit,
        )
        result.status = FundFlowErrorType.TO_FRESHNESS_STATUS.get(
            result.error_type, "FAILED"
        )
        result.diagnosis = f"live exception: {err_str}"

    return result


# ── 顶层入口 ──────────────────────────────────────────────────────────


def is_live_probe_enabled() -> bool:
    """live-smoke 是否被环境变量放行."""
    return os.getenv(_LIVE_ENV, "").strip() == "1"


def _get_fallback_chain() -> List[str]:
    try:
        return list(get_fallback_chain(DataType.FUND_FLOW))
    except Exception:
        return []


def _get_primary_vendor() -> str:
    try:
        primary = get_primary_source(DataType.FUND_FLOW)
        return primary.vendor if primary else ""
    except Exception:
        return ""


def run_fund_flow_probe(
    symbols: Optional[List[str]] = None,
    *,
    live_smoke: bool = False,
    fetch_fn: Optional[Callable[[str], Any]] = None,
    last_hit_vendor_fn: Optional[Callable[[], str]] = None,
    today: Optional[str] = None,
    expected_unit: str = EXPECTED_UNIT,
) -> FundFlowProbeReport:
    """运行 fund_flow probe.

    Args:
        symbols: live-smoke 抽样标的, 默认 ``DEFAULT_PROBE_SYMBOLS``.
        live_smoke: True 才允许走真实网络调用; 仍需 ``TA_LIVE_DATA_SMOKE=1``
            双重门禁, 否则自动降级为 SKIPPED.
        fetch_fn / last_hit_vendor_fn: 测试注入点; 默认走真实 route_to_vendor.
        today: 测试用固定日期 (YYYY-MM-DD).

    Returns:
        ``FundFlowProbeReport`` — fixture 模式一定有 6 条 result;
        live 模式按 symbols 数量产出; gated 模式产 SKIPPED 行.
    """
    now = datetime.now()
    if today:
        date_str = today[:10]
    else:
        date_str = now.strftime("%Y-%m-%d")

    report = FundFlowProbeReport(
        run_at=now.strftime("%Y-%m-%d %H:%M:%S") if not today else f"{today} 00:00:00",
        date=date_str,
        fallback_chain=_get_fallback_chain(),
        expected_unit=expected_unit,
    )

    live_env_ok = is_live_probe_enabled()
    wants_live = bool(live_smoke)

    if not wants_live:
        # —— fixture dry-run: 跑 5 类 + HAS_DATA 基线 ——
        report.mode = "fixture"
        report.env_gated = True
        report.symbols = list(symbols) if symbols else list(DEFAULT_PROBE_SYMBOLS)
        for fid, fixture in PROBE_FIXTURES.items():
            report.results.append(
                _probe_fixture(fid, fixture, expected_unit=expected_unit)
            )
        report.summary = _compute_summary(report.results)
        return report

    # —— live-smoke 路径 ——
    report.mode = "live"
    report.env_gated = not live_env_ok
    if symbols is None:
        symbols = list(DEFAULT_PROBE_SYMBOLS)
    report.symbols = list(symbols[:_MAX_SYMBOLS])

    if not live_env_ok:
        # 双重门禁未通过 → 输出 SKIPPED 行, 不发任何网络请求
        for sym in report.symbols:
            report.results.append(
                FundFlowProbeResult(
                    symbol=sym,
                    status="SKIPPED",
                    error_type=FundFlowErrorType.UNKNOWN,
                    probe_mode="skipped",
                    diagnosis=(
                        "live-smoke requested but TA_LIVE_DATA_SMOKE!=1; "
                        "no network call made"
                    ),
                )
            )
        report.summary = _compute_summary(report.results)
        return report

    for sym in report.symbols:
        report.results.append(
            _probe_live_symbol(
                sym,
                fetch_fn=fetch_fn,
                last_hit_vendor_fn=last_hit_vendor_fn,
                expected_unit=expected_unit,
            )
        )

    report.summary = _compute_summary(report.results)
    return report


# ── 汇总 ──────────────────────────────────────────────────────────────


def _compute_summary(results: List[FundFlowProbeResult]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "total": len(results),
        "by_error_type": {},
        "by_status": {},
        "fixture_coverage": [],
        "required_classes_covered": [],
        "required_classes_missing": [],
        "has_failures": False,
        "all_passed": False,
    }

    for r in results:
        et = r.error_type
        summary["by_error_type"][et] = summary["by_error_type"].get(et, 0) + 1
        st = r.status
        summary["by_status"][st] = summary["by_status"].get(st, 0) + 1
        if r.fixture_id:
            summary["fixture_coverage"].append(r.fixture_id)

    covered = set(summary["by_error_type"].keys())
    required = set(FundFlowErrorType.REQUIRED_ATTRIBUTION)
    summary["required_classes_covered"] = sorted(required & covered)
    summary["required_classes_missing"] = sorted(required - covered)

    runnable = [r for r in results if r.status != "SKIPPED"]
    failure_types = {
        FundFlowErrorType.NETWORK_ERROR,
        FundFlowErrorType.RATE_LIMITED,
        FundFlowErrorType.FIELD_CHANGE,
        FundFlowErrorType.UNKNOWN_UNIT,
        FundFlowErrorType.UNKNOWN,
    }
    summary["has_failures"] = any(
        r.error_type in failure_types and r.probe_mode == "live" for r in runnable
    )
    # fixture 模式: 每条 fixture 必须命中 expected_error_type 才算通过
    if runnable and all(r.probe_mode == "fixture" for r in runnable):
        summary["all_passed"] = all(
            r.error_type
            == PROBE_FIXTURES.get(r.fixture_id, {}).get(
                "expected_error_type", r.error_type
            )
            for r in runnable
        )
    else:
        summary["all_passed"] = (
            len(runnable) > 0
            and not summary["has_failures"]
            and all(r.error_type in (FundFlowErrorType.OK, FundFlowErrorType.NO_DATA) for r in runnable)
        )

    return summary


# ── 渲染 ──────────────────────────────────────────────────────────────


def render_fund_flow_probe_report(report: FundFlowProbeReport) -> str:
    """渲染成 markdown 报告."""
    lines: List[str] = []
    lines.append("# 主力资金供应商 Probe 报告 — fund_flow_source_probe")
    lines.append("")
    lines.append("> [DATA-024] 主力资金 fallback live-smoke dry-run 与错误归因.")
    lines.append(
        "> 区分 5 类典型故障: 网络失败 / 接口字段变更 / 正常无数据 / 限流 / 单位不明."
    )
    lines.append("")
    lines.append(f"- **Date**: {report.date}")
    lines.append(f"- **Run at**: {report.run_at}")
    lines.append(f"- **Mode**: `{report.mode}`")
    gate_text = (
        "Yes (TA_LIVE_DATA_SMOKE not set — no live network calls)"
        if report.env_gated and report.mode == "live"
        else ("No (live calls executed)" if report.mode == "live" else "n/a (fixture dry-run)")
    )
    lines.append(f"- **Env gated**: {gate_text}")
    lines.append(f"- **Symbols**: {', '.join(report.symbols) or '-'}")
    lines.append(f"- **Expected unit**: `{report.expected_unit}`")
    chain = " → ".join(report.fallback_chain) if report.fallback_chain else "-"
    lines.append(f"- **Fallback chain (catalog)**: {chain}")
    lines.append("")

    s = report.summary
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Total probes | {s.get('total', 0)} |")
    lines.append(f"| Required 5-class covered | {', '.join(s.get('required_classes_covered', [])) or '-'} |")
    missing = s.get("required_classes_missing", [])
    lines.append(f"| Required 5-class missing | {', '.join(missing) if missing else 'none ✓'} |")
    lines.append(f"| All runnable passed | {'Yes' if s.get('all_passed') else 'No'} |")
    lines.append(f"| Has live failures | {'Yes' if s.get('has_failures') else 'No'} |")
    lines.append("")

    by_et = s.get("by_error_type", {})
    if by_et:
        lines.append("### By error_type")
        lines.append("")
        lines.append("| error_type | label_cn | count |")
        lines.append("|------------|----------|-------|")
        for et in FundFlowErrorType.ALL:
            if et in by_et:
                lines.append(
                    f"| `{et}` | {FundFlowErrorType.LABEL_CN.get(et, et)} | {by_et[et]} |"
                )
        lines.append("")

    lines.append("## Probe Details")
    lines.append("")
    lines.append(
        "| Symbol | Vendor | Mode | Status | error_type | Unit | Verified |"
        " Records | Latency (ms) | Fixture | Diagnosis |"
    )
    lines.append(
        "|--------|--------|------|--------|------------|------|----------|"
        "---------|-------------|---------|----------|"
    )
    for r in report.results:
        diag = (r.diagnosis or "")[:60]
        if len(r.diagnosis) > 60:
            diag += "..."
        lines.append(
            f"| {r.symbol or '-'} | {r.vendor or '-'} | {r.probe_mode} "
            f"| {r.status} | `{r.error_type}` | {r.unit or '-'} "
            f"| {'Yes' if r.unit_verified else '-'} | {r.record_count} "
            f"| {r.latency_ms:.0f} | {r.fixture_id or '-'} | {diag or '-'} |"
        )
    lines.append("")

    if report.mode == "fixture":
        lines.append("## Fixture Coverage")
        lines.append("")
        lines.append("5 类失败/无数据 + HAS_DATA 基线都已回放, 用于证明错误归因分类器对每类典型输入都能给出正确标签.")
        lines.append("")
        lines.append("| fixture_id | expected → actual | description |")
        lines.append("|------------|-------------------|-------------|")
        for r in report.results:
            if not r.fixture_id:
                continue
            expected = PROBE_FIXTURES.get(r.fixture_id, {}).get(
                "expected_error_type", "?"
            )
            ok_mark = "✓" if expected == r.error_type else "✗"
            lines.append(
                f"| `{r.fixture_id}` | `{expected}` → `{r.error_type}` {ok_mark} "
                f"| {r.diagnosis} |"
            )
        lines.append("")

    if report.mode == "live" and report.env_gated:
        lines.append("## Live-Smoke Gate")
        lines.append("")
        lines.append(
            "本报告为 live-smoke 模式但环境变量 `TA_LIVE_DATA_SMOKE` 未设置为 `1`, "
            "因此未发起任何真实网络调用. 所有标的标记为 `SKIPPED`."
        )
        lines.append("")
        lines.append(
            "如需实盘抽样, 请显式设置环境变量后重跑:"
        )
        lines.append("")
        lines.append("```bash")
        lines.append(
            "TA_LIVE_DATA_SMOKE=1 python scripts/run_fund_flow_source_probe.py "
            "--live-smoke --symbols 600519.SH,000001.SZ"
        )
        lines.append("```")
        lines.append("")

    lines.append("---")
    lines.append("*Generated by fund_flow_source_probe.py — `[DATA-024] fund_flow_source_probe`*")
    lines.append("")
    return "\n".join(lines)


def save_fund_flow_probe_report(
    report: FundFlowProbeReport,
    output_dir: str = "docs/data_source_reports",
) -> str:
    """把 markdown 报告写到 ``docs/data_source_reports/fund-flow-probe-YYYY-MM-DD.md``."""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"fund-flow-probe-{report.date}.md")
    md = render_fund_flow_probe_report(report)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return path


# ── DATA-023 能力矩阵 overlay (只读附加, 不改 matrix 本身) ────────────


def build_capability_matrix_overlay(
    report: FundFlowProbeReport,
) -> Dict[str, Any]:
    """从 probe 报告生成一个 overlay, 供 capability matrix API / 文档附加.

    设计原则:
    - **不修改** ``source_capability_matrix.get_source_capability_matrix()`` 的输出
      (避免破坏 DATA-023 的 31 个测试与已发布 docs/SOURCE_CAPABILITY_MATRIX.md).
    - overlay 只提供 *最近一次 probe 的统计快照*, 调用方 (API / UI) 可以把它
      并到 matrix 的 ``fund_flow`` entry 里展示, 也可以独立显示.
    - 不输出任何敏感字段.
    """
    by_et = dict(report.summary.get("by_error_type", {}))
    return {
        "data_type": DataType.FUND_FLOW.value,
        "probe_source": "[DATA-024] fund_flow_source_probe",
        "probe_run_at": report.run_at,
        "probe_mode": report.mode,
        "env_gated": report.env_gated,
        "expected_unit": report.expected_unit,
        "fallback_chain_catalog": report.fallback_chain,
        "total_probes": report.summary.get("total", 0),
        "by_error_type": by_et,
        "required_classes_covered": list(
            report.summary.get("required_classes_covered", [])
        ),
        "required_classes_missing": list(
            report.summary.get("required_classes_missing", [])
        ),
        "all_passed": bool(report.summary.get("all_passed")),
        "has_live_failures": bool(report.summary.get("has_failures")),
        "notes": (
            "fixture dry-run 覆盖 5 类失败/无数据 + HAS_DATA 基线; "
            "live-smoke 默认关闭, 需 TA_LIVE_DATA_SMOKE=1 + live_smoke=True 双重门禁."
        ),
    }


def get_fund_flow_capability_matrix_item(
    *,
    include_probe_overlay: bool = True,
    probe_report: Optional[FundFlowProbeReport] = None,
) -> Dict[str, Any]:
    """获取 fund_flow 的能力矩阵 entry, 可选附加 probe overlay.

    用于让 capability matrix API 在不修改 matrix 模块的前提下,
    把 fund_flow probe 状态一起返回.
    """
    from .source_capability_matrix import get_matrix_item

    item = get_matrix_item(DataType.FUND_FLOW.value)
    if item is None:
        return {}
    if not include_probe_overlay:
        return item
    if probe_report is None:
        probe_report = run_fund_flow_probe()
    item = dict(item)
    item["probe_overlay"] = build_capability_matrix_overlay(probe_report)
    return item
