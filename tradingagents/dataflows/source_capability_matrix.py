# [DATA-023] source_capability_matrix
"""A股数据源能力矩阵导出。

把分散在 `source_catalog`、docs 与报告里的数据源能力信息，
统一导出成 agent 可读、用户可查的供应商能力矩阵。

每条 matrix entry 描述一种 `data_type`（行情 / 资金 / 龙虎榜 / 公告 /
评级 / 回购 / 研报 ...）的：
  - primary_vendor        — 当前首选数据源 vendor
  - primary_endpoint      — 首选 vendor 的端点
  - fallback_vendor       — 备选 vendor（fallback chain 中 priority 最小的下一个）
  - fallback_chain        — 全量 fallback vendor 列表（按 priority 排序）
  - freshness             — 实时性（realtime / intraday / daily / delayed / stale / unknown）
  - unit                  — 字段单位（万元 / 元 / 股 / 条 等）
  - known_limits          — 已知限制摘要（rate_limit_risk + known_gaps）
  - status_semantics      — 状态语义说明（告诉 agent 这条数据可用边界）
  - fields                — 主要字段
  - rate_limit_risk       — 限流风险等级
  - notes                 — 备注说明

设计原则：
  - 不调用任何 live API；完全基于 source_catalog 静态导出。
  - 不读取 / 输出任何 API Key 或密钥相关内容。
  - 不改变 provider 路由 / fallback 行为。
  - 与 `MODEL_API_CATALOG` 类似，输出给 agent / UI / 文档复用。

使用示例：
    from tradingagents.dataflows.source_capability_matrix import (
        get_source_capability_matrix,
        render_source_capability_matrix_markdown,
        validate_matrix_coverage,
    )
    matrix = get_source_capability_matrix()
    print(render_source_capability_matrix_markdown(matrix))
    issues = validate_matrix_coverage(matrix)
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

from .source_catalog import (
    DataType,
    Freshness,
    RateLimitRisk,
    get_fallback_chain,
    get_primary_source,
    get_sources_for_type,
)


SOURCE_CAPABILITY_MATRIX_VERSION = "2026-06-27"


# ── 新鲜度语义说明 ────────────────────────────────────────────────────
# 告诉 agent / 用户每种 freshness 值代表的可用边界。
FRESHNESS_STATUS_SEMANTICS: Dict[str, str] = {
    Freshness.REALTIME.value: (
        "盘中实时推送（秒级）。最接近实时价，可作盘中决策依据；"
        "停牌 / 非交易时段无更新。"
    ),
    Freshness.INTRADAY.value: (
        "盘中更新但有延迟（分钟级）。适合盘中观察，不宜做毫秒级套利。"
    ),
    Freshness.DAILY.value: (
        "收盘后至次日开市前更新。盘中为前一交易日快照，不可用于实时决策。"
    ),
    Freshness.DELAYED.value: (
        "延迟 >1 个交易日（如 T+1 / T+2 财务数据）。仅适合长期回看与季度评估。"
    ),
    Freshness.STALE.value: (
        "数据陈旧或长期不更新。不可作为决策依据，需触发 fallback 或人工核实。"
    ),
    Freshness.UNKNOWN.value: (
        "新鲜度未知。使用前必须 verify，未 verify 不得作为 primary source。"
    ),
}


# ── 限流风险中文标签 ──────────────────────────────────────────────────
RATE_LIMIT_RISK_LABEL: Dict[str, str] = {
    RateLimitRisk.LOW.value: "低（可频繁调用）",
    RateLimitRisk.MEDIUM.value: "中（建议带缓存与退避）",
    RateLimitRisk.HIGH.value: "高（必须限流 + 可切换 fallback）",
    RateLimitRisk.UNKNOWN.value: "未知",
}


# ── data_type 中文标签 ────────────────────────────────────────────────
DATA_TYPE_LABEL_CN: Dict[str, str] = {
    DataType.QUOTE.value: "行情快照",
    DataType.OHLCV.value: "K 线 / 历史行情",
    DataType.FUND_FLOW.value: "个股资金流",
    DataType.BOARD_FUND_FLOW.value: "板块资金流",
    DataType.LHB.value: "龙虎榜",
    DataType.MARGIN_TRADING.value: "融资融券",
    DataType.NOTICE.value: "公司公告",
    DataType.REPORT.value: "券商研报",
    DataType.RATING.value: "分析师评级",
    DataType.NEWS.value: "个股新闻",
    DataType.GLOBAL_NEWS.value: "全市场快讯",
    DataType.FINANCIALS.value: "财务三表",
    DataType.INSIDER.value: "内部交易 / 股东",
    DataType.HOT_STOCKS.value: "热门股票",
    DataType.ZT_POOL.value: "涨停池",
    DataType.REALTIME_QUOTES.value: "实时行情",
    DataType.BUYBACK.value: "回购",
}


def _format_known_limits(
    rate_limit_risk: str,
    known_gaps: List[str],
) -> str:
    """把 rate_limit_risk 与 known_gaps 合并成一段可读的限制摘要。"""
    parts: List[str] = []
    risk_label = RATE_LIMIT_RISK_LABEL.get(
        rate_limit_risk, RATE_LIMIT_RISK_LABEL[RateLimitRisk.UNKNOWN.value]
    )
    parts.append(f"限流风险：{risk_label}")
    if known_gaps:
        parts.append("已知缺口：" + "；".join(known_gaps))
    return " | ".join(parts)


def _format_status_semantics(
    freshness_value: str,
    rate_limit_risk: str,
    known_gaps: List[str],
    is_primary: bool,
) -> str:
    """生成 status_semantics 字符串，说明这条数据的可用边界。

    包含：freshness 含义 + primary/fallback 角色提示 + 关键风险。
    """
    base = FRESHNESS_STATUS_SEMANTICS.get(
        freshness_value, FRESHNESS_STATUS_SEMANTICS[Freshness.UNKNOWN.value]
    )
    role = "首选源（primary）" if is_primary else "备选源（fallback）"
    extras: List[str] = [f"角色：{role}"]
    if rate_limit_risk == RateLimitRisk.HIGH.value:
        extras.append("高限流风险，必须启用 fallback 与重试退避")
    if any("force" in g or "NOT_QUERIED" in g for g in known_gaps):
        extras.append("需 force=True 才会实际查询，默认返回 NOT_QUERIED")
    if any("不稳定" in g or "字段覆盖" in g for g in known_gaps):
        extras.append("字段覆盖不稳定，需校验后再信任")
    return base + "；" + "；".join(extras)


def _build_matrix_entry(data_type: DataType) -> Optional[Dict[str, Any]]:
    """为单个 data_type 构造 matrix entry；无任何 source 时返回 None。"""
    sources = get_sources_for_type(data_type)
    if not sources:
        return None

    primary = get_primary_source(data_type)
    primary_source = primary if primary is not None else sources[0]
    fallback_chain = get_fallback_chain(data_type)

    # fallback_vendor：剔除 primary 后下一个 vendor
    fallback_vendor = ""
    for vendor in fallback_chain:
        if vendor != primary_source.vendor:
            fallback_vendor = vendor
            break

    known_gaps = list(primary_source.known_gaps)
    freshness_value = primary_source.freshness.value
    rate_limit_risk = primary_source.rate_limit_risk.value

    return {
        "data_type": data_type.value,
        "label_cn": DATA_TYPE_LABEL_CN.get(data_type.value, data_type.value),
        "primary_vendor": primary_source.vendor,
        "primary_endpoint": primary_source.endpoint,
        "is_primary_confirmed": primary is not None,
        "fallback_vendor": fallback_vendor,
        "fallback_chain": fallback_chain,
        "freshness": freshness_value,
        "unit": primary_source.unit,
        "fields": list(primary_source.fields),
        "rate_limit_risk": rate_limit_risk,
        "known_limits": _format_known_limits(rate_limit_risk, known_gaps),
        "status_semantics": _format_status_semantics(
            freshness_value,
            rate_limit_risk,
            known_gaps,
            primary is not None,
        ),
        "source_count": len(sources),
        "notes": primary_source.notes,
    }


def get_source_capability_matrix(
    *,
    include_research_report_free_sources: bool = True,
) -> Dict[str, Any]:
    """导出统一的数据源能力矩阵。

    返回结构：
        {
            "version": "2026-06-27",
            "items": [ ... matrix entry ... ],
            "freshness_legend": {freshness_value: 说明},
            "rate_limit_legend": {risk_value: 说明},
            "research_report_free_sources": [ ... DATA-025 free sources ... ],
        }

    返回深拷贝，调用方修改不会影响模块内部状态。
    ``research_report_free_sources`` ([DATA-025]) 默认附加, 提供免费研报来源目录,
    不修改 items (避免破坏已发布的 docs/SOURCE_CAPABILITY_MATRIX.md).
    """
    items: List[Dict[str, Any]] = []
    for data_type in DataType:
        entry = _build_matrix_entry(data_type)
        if entry is not None:
            items.append(entry)

    matrix: Dict[str, Any] = {
        "version": SOURCE_CAPABILITY_MATRIX_VERSION,
        "items": items,
        "freshness_legend": dict(FRESHNESS_STATUS_SEMANTICS),
        "rate_limit_legend": dict(RATE_LIMIT_RISK_LABEL),
    }
    if include_research_report_free_sources:
        # 延迟导入避免循环依赖; supplement 是只读附加, 不改 items.
        try:
            from .research_report_sources import build_capability_matrix_supplement
            matrix["research_report_free_sources"] = build_capability_matrix_supplement()
        except Exception:
            # supplement 失败不应影响主矩阵输出
            pass
    return matrix


def get_matrix_item(data_type: str) -> Optional[Dict[str, Any]]:
    """按 data_type 查询单条 matrix entry。"""
    matrix = get_source_capability_matrix()
    for item in matrix["items"]:
        if item["data_type"] == data_type:
            return deepcopy(item)
    return None


def validate_matrix_coverage(
    matrix: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """检查 matrix 覆盖是否完整。

    验收要求：新增 DataType 不会漏进矩阵；每条 entry 关键字段非空。

    返回 issue 列表（空列表表示通过）。
    """
    if matrix is None:
        matrix = get_source_capability_matrix()

    issues: List[str] = []

    # 1. 每个 source_catalog 中的 DataType 必须有对应 matrix entry
    expected_types = {dt.value for dt in DataType}
    present_types = {item["data_type"] for item in matrix.get("items", [])}
    # 只校验 source_catalog 中确实有 source 注册的 data_type
    registered_types = set()
    for dt in DataType:
        if get_sources_for_type(dt):
            registered_types.add(dt.value)

    missing = registered_types - present_types
    for dt_value in sorted(missing):
        issues.append(
            f"data_type '{dt_value}' 在 source_catalog 已注册但未出现在 matrix"
        )

    extra = present_types - expected_types
    for dt_value in sorted(extra):
        issues.append(
            f"matrix 出现未注册的 data_type '{dt_value}'"
        )

    # 2. 每条 entry 关键字段非空
    required_fields = (
        "data_type",
        "primary_vendor",
        "primary_endpoint",
        "fallback_chain",
        "freshness",
        "unit",
        "known_limits",
        "status_semantics",
    )
    for item in matrix.get("items", []):
        for field_name in required_fields:
            value = item.get(field_name)
            if value is None or value == "" or value == []:
                # unit / fallback_chain 对个别纯文本类 data_type 允许为空
                if field_name == "unit" and item.get("data_type") in (
                    DataType.NOTICE.value,
                    DataType.REPORT.value,
                    DataType.NEWS.value,
                    DataType.GLOBAL_NEWS.value,
                    DataType.RATING.value,
                    DataType.INSIDER.value,
                    DataType.ZT_POOL.value,
                    DataType.HOT_STOCKS.value,
                ):
                    continue
                if field_name == "fallback_chain" and item.get("source_count", 0) >= 1:
                    continue
                issues.append(
                    f"data_type '{item.get('data_type')}' 字段 '{field_name}' 为空"
                )

    # 3. status_semantics 必须引用合法 freshness 值
    valid_freshness = set(FRESHNESS_STATUS_SEMANTICS.keys())
    for item in matrix.get("items", []):
        if item.get("freshness") not in valid_freshness:
            issues.append(
                f"data_type '{item.get('data_type')}' freshness='"
                f"{item.get('freshness')}' 不在合法枚举中"
            )

    return issues


# ── 渲染：Markdown 文档 ──────────────────────────────────────────────
def render_source_capability_matrix_markdown(
    matrix: Optional[Dict[str, Any]] = None,
) -> str:
    """把 matrix 渲染成 Markdown 文档字符串（agent 可读）。"""
    if matrix is None:
        matrix = get_source_capability_matrix()

    lines: List[str] = []
    lines.append("# A 股数据源能力矩阵")
    lines.append("")
    lines.append(
        "> 本文档由 `tradingagents/dataflows/source_capability_matrix.py` 自动生成。"
        "记录每个 data_type 的首选 vendor、fallback、实时性、单位、已知限制与状态语义。"
        "不包含任何 API Key 或密钥信息。"
    )
    lines.append("")
    lines.append(f"- 代码目录：`tradingagents/dataflows/source_capability_matrix.py`")
    lines.append(f"- 数据源目录：`tradingagents/dataflows/source_catalog.py`")
    lines.append(f"- API 目录：`GET /v1/config/source-capability-matrix`")
    lines.append(f"- 矩阵版本：`{matrix.get('version', '')}`")
    lines.append(f"- data_type 数量：`{len(matrix.get('items', []))}`")
    lines.append("")

    lines.append("## 矩阵总览")
    lines.append("")
    lines.append(
        "| data_type | 中文名 | primary_vendor | primary_endpoint | "
        "fallback_vendor | freshness | unit | 限流风险 | source_count |"
    )
    lines.append(
        "|-----------|--------|----------------|------------------|"
        "-----------------|-----------|------|---------|--------------|"
    )
    for item in matrix.get("items", []):
        lines.append(
            f"| `{item['data_type']}` | {item.get('label_cn', '')} | "
            f"{item['primary_vendor']} | `{item['primary_endpoint']}` | "
            f"{item.get('fallback_vendor') or '-'} | `{item['freshness']}` | "
            f"{item.get('unit') or '-'} | `{item.get('rate_limit_risk', '')}` | "
            f"{item.get('source_count', 0)} |"
        )
    lines.append("")

    lines.append("## 状态语义详情")
    lines.append("")
    lines.append(
        "下表说明每个 data_type 的实时性边界、已知限制与 agent 应当如何使用。"
    )
    lines.append("")
    for item in matrix.get("items", []):
        lines.append(f"### `{item['data_type']}` — {item.get('label_cn', '')}")
        lines.append("")
        lines.append(f"- **首选源**：`{item['primary_vendor']}` / `{item['primary_endpoint']}`")
        if item.get("fallback_chain"):
            chain = " → ".join(item["fallback_chain"])
            lines.append(f"- **Fallback 链**：{chain}")
        lines.append(f"- **实时性**：`{item['freshness']}`")
        lines.append(f"- **单位**：{item.get('unit') or '-'}")
        lines.append(f"- **字段**：{', '.join(item.get('fields', [])) or '-'}")
        lines.append(f"- **已知限制**：{item.get('known_limits', '-')}")
        lines.append(f"- **状态语义**：{item.get('status_semantics', '-')}")
        if item.get("notes"):
            lines.append(f"- **备注**：{item['notes']}")
        lines.append("")

    lines.append("## Freshness 语义图例")
    lines.append("")
    lines.append("| freshness | 说明 |")
    lines.append("|-----------|------|")
    for key, value in matrix.get("freshness_legend", {}).items():
        lines.append(f"| `{key}` | {value} |")
    lines.append("")

    lines.append("## 限流风险图例")
    lines.append("")
    lines.append("| rate_limit_risk | 说明 |")
    lines.append("|-----------------|------|")
    for key, value in matrix.get("rate_limit_legend", {}).items():
        lines.append(f"| `{key}` | {value} |")
    lines.append("")

    lines.append("## 字段说明")
    lines.append("")
    lines.append("- `data_type`：数据类型枚举（quote / ohlcv / fund_flow / lhb / notice / report / rating / news / buyback ...）。")
    lines.append("- `primary_vendor`：当前首选数据源 vendor（cn_akshare / cn_astock / cn_baostock ...）。")
    lines.append("- `fallback_vendor`：首选 vendor 失败时的下一个 fallback vendor。")
    lines.append("- `fallback_chain`：完整 fallback vendor 列表（按 priority 排序）。")
    lines.append("- `freshness`：实时性等级（realtime / intraday / daily / delayed / stale / unknown）。")
    lines.append("- `unit`：主要字段单位（万元 / 元 / 股 / 条 ...）。")
    lines.append("- `known_limits`：已知限制摘要（限流风险 + known_gaps）。")
    lines.append("- `status_semantics`：状态语义说明（freshness 含义 + 角色提示 + 关键风险）。")
    lines.append("- `source_count`：该 data_type 在 source_catalog 中登记的 source 数量。")
    lines.append("")

    return "\n".join(lines)


def render_source_capability_matrix_text(
    matrix: Optional[Dict[str, Any]] = None,
) -> str:
    """把 matrix 渲染成纯文本（用于 agent 紧凑上下文）。"""
    if matrix is None:
        matrix = get_source_capability_matrix()

    lines: List[str] = []
    lines.append("[Source Capability Matrix]")
    lines.append(f"version: {matrix.get('version', '')}")
    for item in matrix.get("items", []):
        chain = ",".join(item.get("fallback_chain", []))
        lines.append(
            f"- {item['data_type']} | primary={item['primary_vendor']}/{item['primary_endpoint']} "
            f"| fallback_chain={chain} | freshness={item['freshness']} "
            f"| unit={item.get('unit') or '-'} | rate_limit={item.get('rate_limit_risk', '')} "
            f"| limits={item.get('known_limits', '')} | semantics={item.get('status_semantics', '')}"
        )
    return "\n".join(lines)
