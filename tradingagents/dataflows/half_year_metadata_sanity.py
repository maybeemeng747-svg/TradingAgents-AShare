# [HY-011] half_year_metadata_sanity
r"""半年报报告期 / 披露日 / 修订版本元数据 sanity check。

在 HY-001（财报页字段缺失/格式 lint）和 KB-014（来源可信度分层）之上，本模块
对半年报元数据做**组合关系校验**，阻止错期、未来日期、symbol/name 错配和
修订稿覆盖原稿等污染。任务 docs/TASKS.md HY-011。

与 HY-001（``HYF-`` 规则族）的关系：
  - HY-001 回答“字段是否齐全、格式是否合法”（单字段存在性 / 格式）。
  - HY-011 回答“字段之间是否自洽、跨页是否冲突”（组合关系 / 跨页版本）。
  - 本模块**复用** ``local_knowledge_lint.LintFinding`` / ``PageLintResult``，
    不重复定义数据类；只产出 finding 列表，由 ``lint_single_page`` /
    ``lint_local_knowledge`` 接入既有报告通道。

设计约束（对应任务 HY-011）：
  - **只读 lint/sanity**：纯函数 + 标准库，不写知识库文件、不调 LLM、不联网。
  - **不猜缺失日期**：``financial_period`` / ``period_end_date`` / ``disclosure_date``
    任一缺失时，**跳过**对应的组合校验，不假定默认值。缺失（HYF-001/003 已覆盖）
    与非法 / 不自洽（HYM-* 覆盖）严格分开。
  - **修订稿只影响事实索引选择**：HYM-006（info）只标记“检测到修订稿”，
    不生成交易动作、不删除原稿来源路径；原稿/修订稿路径都保留在
    ``LintFinding.fix_suggestion`` 中，由 HY-003 事实索引 provider 决定优先级。
  - **不阻塞 TA**：HYM 规则最高级别为 error，但 ``local_knowledge_lint`` 的
    readiness 计算已经吸收 error/warning，本模块不改 readiness 阈值。

规则族（``HYM-`` 前缀，与 ``HYF-`` 严格区分）：
  - HYM-001（warning）：``disclosure_date`` 是未来日期（晚于 today）。
    ``disclosure_date`` 的契约语义是“已发生的交易所披露日”，因此不依赖
    ``period_end_date``；预排期应另用 ``scheduled_disclosure_date``。
  - HYM-002（error）：``period_end_date`` 晚于 ``disclosure_date``（报告期末日
    不可能晚于公告日）。仅当两者均可解析为日期时触发。
  - HYM-003（warning）：``report_type ∈ {半年报, 中报}`` 但 ``financial_period``
    不是半年报周期（如 ``2025Q1`` / ``2025年报``）。``财报分析`` 是通用类型，
    不触发——它可能点评任何周期。
  - HYM-004（warning）：``symbols`` 字段中 CODE/NAME 不一致，或 CODE 不符合
    知识库支持的 A/H/US 市场代码格式。若 frontmatter 显式提供
    ``name`` 字段，进一步校验 ``name`` 与 ``symbols`` 内 NAME 一致。
  - HYM-005（warning）：跨页——同 symbol + 同 ``financial_period`` 出现多条
    页面，且**全部**无修订标记（``revision`` / ``is_revised`` / ``supersedes``）。
    每个冲突页各加 1 条 warning，``fix_suggestion`` 列出全部冲突 rel_path。
  - HYM-006（info）：跨页——同 symbol + 同 ``financial_period`` 检测到修订稿
    （任一页带 ``revision`` / ``is_revised`` / ``supersedes``）。提示事实索引
    优先选修订稿；不删除原稿路径。
  - HYM-007（warning）：``period_end_date`` 非法，或与 ``financial_period`` 不一致
    （如 ``2025H1`` 期望 ``period_end_date=2025-06-30``，实际 ``2025-12-31``）。
    仅当 ``period_end_date`` 存在且 ``financial_period`` 可推导期望期末日时触发。

使用示例::

    from tradingagents.dataflows.half_year_metadata_sanity import (
        check_half_year_metadata_sanity_single,
        check_half_year_metadata_cross_page,
    )
    findings = check_half_year_metadata_sanity_single(frontmatter)
    cross = check_half_year_metadata_cross_page(page_results)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Tuple

from tradingagents.dataflows.local_knowledge_audit import _safe_str

# [HY-011] half_year_metadata_sanity
# 避免循环导入：``local_knowledge_lint`` 在模块顶部 import 本模块，因此本模块
# 不能在顶部反向 import ``local_knowledge_lint``。改为：
#   - 常量（SEVERITY_* / HALF_YEAR_REPORT_TYPES）：本地定义，与 lint 保持同步
#     （与 ``citation_policy.py`` 处理 ``SOURCE_TYPE_*`` 的策略一致）。
#   - ``LintFinding`` / ``_add`` / 校验函数：函数内 late import，运行时
#     ``local_knowledge_lint`` 已完成初始化，可安全引用。
if TYPE_CHECKING:  # pragma: no cover - 仅类型检查器用
    from tradingagents.dataflows.local_knowledge_lint import LintFinding

# 与 local_knowledge_lint.SEVERITY_* 保持同步（lint 报告依赖这些字面值）。
SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

# 与 local_knowledge_lint.HALF_YEAR_REPORT_TYPES 保持同步。
HALF_YEAR_REPORT_TYPES: Tuple[str, ...] = ("财报分析", "半年报", "中报")

# [HY-011] half_year_metadata_sanity
# 严格半年报 report_type（用于 HYM-003 周期一致性校验）。
# 注意：``财报分析`` 是通用类型，不进入此列表——它可能点评任一周期。
STRICT_HALF_YEAR_REPORT_TYPES: Tuple[str, ...] = ("半年报", "中报")

# 视为“半年报周期”的 financial_period 标识（H1 / 中报 / 半年报 / Q2 ≈ H1 期末）。
# HYM-003 用它判断 period 是否与 report_type=半年报/中报 自洽。
_HALF_YEAR_PERIOD_TOKENS: Tuple[str, ...] = ("H1", "中报", "半年报", "Q2")
# 视为“年报周期”的 token（用于排除——年报周期不是半年报）。
_ANNUAL_PERIOD_TOKENS: Tuple[str, ...] = ("年报", "H2", "Q4")
# 视为“一季报/三季报周期”的 token。
_OTHER_PERIOD_TOKENS: Tuple[str, ...] = ("一季报", "三季报", "Q1", "Q3")

# 与知识库基础契约一致：支持 A 股、港股、美股研报代码。
_A_SHARE_CODE_RE = re.compile(r"^\d{6}(?:\.(?:SH|SZ|BJ))?$", re.IGNORECASE)
_HK_CODE_RE = re.compile(r"^\d{4,5}\.HK$", re.IGNORECASE)
_US_CODE_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}\.US$", re.IGNORECASE)

# period_end_date / disclosure_date 解析：接受 ``YYYY-MM-DD`` / ``YYYY/MM/DD`` / ``YYYYMMDD``。
_DATE_SEP_RE = re.compile(r"^(\d{4})[-/]?(\d{2})[-/]?(\d{2})$")

# [HY-011] financial_period 合法格式（与 local_knowledge_lint._PERIOD_PATTERNS 同步）。
# 用于 HYM-003 / HYM-007 判断 period 是否合法（缺失/非法由 HYF-001 覆盖，这里
# 只在合法时做组合校验）。
_PERIOD_PATTERNS: Tuple[re.Pattern, ...] = (
    re.compile(r"^\d{4}H[12]$"),
    re.compile(r"^\d{4}半年报$"),
    re.compile(r"^\d{4}中报$"),
    re.compile(r"^\d{4}年报$"),
    re.compile(r"^\d{4}一季报$"),
    re.compile(r"^\d{4}三季报$"),
    re.compile(r"^\d{4}Q[1-4]$"),
    re.compile(r"^FY\d{2,4}Q[1-4]$", re.IGNORECASE),
    re.compile(r"^FY\d{2,4}H[12]$", re.IGNORECASE),
)

# financial_period → 期望期末日（月, 日）映射。用于 HYM-007 一致性校验。
# 仅覆盖半年报相关周期；其他周期（一季报/三季报/年报）由各自期末日推导。
_PERIOD_EXPECTED_END: Dict[str, Tuple[int, int]] = {
    "H1": (6, 30),
    "中报": (6, 30),
    "半年报": (6, 30),
    "Q2": (6, 30),
    "H2": (12, 31),
    "年报": (12, 31),
    "Q1": (3, 31),
    "Q3": (9, 30),
    "一季报": (3, 31),
    "三季报": (9, 30),
}

# 修订标记字段：frontmatter 中任一字段非空即视为修订稿。
# - ``revision``：版本号（int/str，如 2 / "v2"）
# - ``is_revised``：布尔（true/yes/1）
# - ``supersedes``：被取代的原稿路径/标识（str/list）
# - ``amendment``：订正说明（str）
REVISION_FIELDS: Tuple[str, ...] = ("revision", "is_revised", "supersedes", "amendment")

# HYM 规则 ID 列表（稳定，供 lint 报告 / 测试断言）。
HYM_RULE_IDS: Tuple[str, ...] = (
    "HYM-001",
    "HYM-002",
    "HYM-003",
    "HYM-004",
    "HYM-005",
    "HYM-006",
    "HYM-007",
)


# ── 工具函数 ──────────────────────────────────────────────────────────


def _late_imports() -> Tuple[Any, Any]:
    """[HY-011] 函数内 late import，避免模块加载时的循环导入。

    返回 ``(LintFinding_cls, add_fn)``。``local_knowledge_lint`` 在被调用时
    已完成初始化，可安全引用。
    """
    from tradingagents.dataflows.local_knowledge_lint import LintFinding as _LF, _add as _a
    return _LF, _a


def _is_valid_financial_period(value: Optional[str]) -> bool:
    """[HY-011] 校验 ``financial_period`` 格式（与 lint ``_is_valid_financial_period`` 同步）。

    接受：``2025H1`` / ``2025H2`` / ``2025中报`` / ``2025年报`` /
    ``2025一季报`` / ``2025三季报`` / ``2025Q1`` / ``FY26Q1`` / ``FY26H1``。
    """
    if not value:
        return False
    text = value.strip()
    if not text:
        return False
    return any(p.match(text) for p in _PERIOD_PATTERNS)


def _add_finding(
    findings: List[Any],
    rule_id: str,
    severity: str,
    message: str,
    fix_suggestion: str,
    field_name: Optional[str] = None,
) -> None:
    """[HY-011] 包装 lint ``_add``，确保 late import 已就绪。"""
    _, add_fn = _late_imports()
    add_fn(
        findings,
        rule_id,
        severity,
        message,
        fix_suggestion,
        field_name=field_name,
    )


def _parse_date(text: Optional[str]) -> Optional[date]:
    """[HY-011] 解析 ``YYYY-MM-DD`` / ``YYYY/MM/DD`` / ``YYYYMMDD`` 为 :class:`date`。

    无法解析（含 ``None`` / 空串 / 非日期格式 / 不真实日历日如 2/30）返回 ``None``。
    与 HY-001 ``_is_valid_disclosure_date``（只校验格式）的区别：本函数进一步
    校验日历真实性，避免 ``2026-13-40`` 这种“格式合法但非日历日”的值污染
    组合校验（HYM-001/002/007）。
    """
    if not text:
        return None
    raw = text.strip()
    if not raw:
        return None
    m = _DATE_SEP_RE.match(raw)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _parse_symbol_entry(entry: Any) -> Optional[Tuple[str, str]]:
    """[HY-011] 解析 ``symbols`` 单条 ``"CODE NAME"`` 为 ``(code, name)``。

    接受 ``"603296.SH 华勤技术"`` / ``"603296 华勤技术"`` / ``"603296.SH  华勤技术 "``。
    无法解析（无空格 / 空 NAME）返回 ``None``。仅取第一个空格分隔，CODE 后内容
    一律视为 NAME（即使 NAME 含空格）。
    """
    text = _safe_str(entry)
    if not text:
        return None
    parts = text.strip().split(maxsplit=1)
    if len(parts) != 2:
        return None
    code, name = parts[0].strip(), parts[1].strip()
    if not code or not name:
        return None
    return code, name


def _is_supported_symbol_code(code: str) -> bool:
    """是否符合知识库支持的 A/H/US 市场代码格式。"""
    return any(
        pattern.fullmatch(code)
        for pattern in (_A_SHARE_CODE_RE, _HK_CODE_RE, _US_CODE_RE)
    )


def _canonical_symbol_group_key(code: str) -> str:
    """把跨页分组用 symbol 归一化，兼容 A 股裸代码/交易所后缀别名。"""
    normalized = code.strip().upper()
    if _A_SHARE_CODE_RE.fullmatch(normalized):
        return normalized.split(".", 1)[0]
    return normalized


def _canonical_period_group_key(period: str) -> str:
    """把同一财务周期的常见写法归一到稳定跨页分组键。"""
    text = period.strip()
    if not text:
        return ""
    if text.upper().startswith("FY"):
        return text.upper()
    year_match = re.match(r"^(\d{4})", text)
    token = _extract_period_token(text)
    if not year_match or not token:
        return text.upper()
    aliases = {
        "H1": "H1",
        "Q2": "H1",
        "中报": "H1",
        "半年报": "H1",
        "Q1": "Q1",
        "一季报": "Q1",
        "Q3": "Q3",
        "三季报": "Q3",
        "H2": "H2",
        "Q4": "Q4",
        "年报": "FY",
    }
    return f"{year_match.group(1)}{aliases.get(token, token)}"


def _extract_period_token(period: Optional[str]) -> Optional[str]:
    """[HY-011] 从 ``financial_period`` 提取周期 token（H1/H2/Q1-Q4/中报/年报/...）。

    例：``2025H1`` → ``H1``；``2025中报`` → ``中报``；``FY26Q1`` → ``Q1``；
    ``2025一季报`` → ``一季报``。无法识别返回 ``None``。
    """
    if not period:
        return None
    text = period.strip()
    if not text:
        return None
    # 按长度倒序匹配，避免 ``半年报`` 被 ``年报`` 提前命中。
    for token in (
        "半年报",
        "中报",
        "年报",
        "一季报",
        "三季报",
        "H1",
        "H2",
        "Q1",
        "Q2",
        "Q3",
        "Q4",
    ):
        if token in text:
            return token
    return None


def _expected_period_end(period: str) -> Optional[date]:
    """返回日历制 ``financial_period`` 对应的完整报告期末日。

    ``FY`` 前缀可能代表非自然财年，无法仅凭字符串可靠推导日历期末日，因此
    保守跳过。普通 ``YYYYH1/Q3/中报`` 等格式同时校验年份和月日，避免
    ``2024-06-30`` 被错误视为与 ``2025H1`` 一致。
    """
    text = period.strip()
    if not text or text.upper().startswith("FY"):
        return None
    year_match = re.match(r"^(\d{4})", text)
    token = _extract_period_token(text)
    expected_month_day = _PERIOD_EXPECTED_END.get(token or "")
    if not year_match or not expected_month_day:
        return None
    year = int(year_match.group(1))
    month, day = expected_month_day
    return date(year, month, day)


def detect_revision_marker(frontmatter: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """[HY-011] 判断 frontmatter 是否标记为修订稿。

    返回 ``(is_revision, hit_fields)``。``hit_fields`` 列出实际命中字段，供
    HYM-006 提示信息使用。判断逻辑（任一命中即视为修订稿）：
      - ``revision``：非空字符串/数字（``0`` / ``""`` 视为未标记）
      - ``is_revised``：true/yes/1（不区分大小写）
      - ``supersedes``：非空 str/list
      - ``amendment``：非空 str
    """
    hits: List[str] = []
    revision_val = frontmatter.get("revision")
    if revision_val is not None:
        text = _safe_str(revision_val)
        if text and text not in ("0", "v0"):
            hits.append("revision")
        elif isinstance(revision_val, int) and revision_val > 0:
            hits.append("revision")

    is_revised_val = frontmatter.get("is_revised")
    if is_revised_val is not None:
        text = _safe_str(is_revised_val)
        if text and text.strip().lower() in ("true", "yes", "1"):
            hits.append("is_revised")
        elif isinstance(is_revised_val, bool) and is_revised_val:
            hits.append("is_revised")

    supersedes_val = frontmatter.get("supersedes")
    if isinstance(supersedes_val, (list, tuple)):
        if any(bool(str(s).strip()) for s in supersedes_val if s is not None):
            hits.append("supersedes")
    elif isinstance(supersedes_val, str):
        if supersedes_val.strip():
            hits.append("supersedes")

    amendment_val = frontmatter.get("amendment")
    if _safe_str(amendment_val):
        hits.append("amendment")

    return (bool(hits), hits)


def _today(today: Optional[date] = None) -> date:
    """[HY-011] 获取 today（可注入，便于测试）。"""
    return today if today is not None else date.today()


# ── 单页 sanity 规则 ──────────────────────────────────────────────────


def check_half_year_metadata_sanity_single(
    frontmatter: Dict[str, Any],
    *,
    has_symbols: bool = False,
    today: Optional[date] = None,
) -> List[LintFinding]:
    """[HY-011] 对单篇财报/半年报/中报页跑 4 条 HYM 单页规则。

    返回 finding 列表（可能为空）。非财报页（``report_type`` 不在
    :data:`HALF_YEAR_REPORT_TYPES`）直接返回空，不报任何 finding——避免
    普通公司点评页被误判。

    参数:
        frontmatter: KB-001/KB-002 解析出的 frontmatter dict。
        has_symbols: 是否有非空 ``symbols`` 字段（HY-001 已计算，复用避免重复）。
        today: 注入的 today（测试用）；默认 :func:`date.today`。
    """
    if not isinstance(frontmatter, dict):
        return []

    report_type = _safe_str(frontmatter.get("report_type")) or ""
    if report_type not in HALF_YEAR_REPORT_TYPES:
        return []

    findings: List[LintFinding] = []
    ref_today = _today(today)

    # ── HYM-001：disclosure_date 是未来日期（warning）
    # disclosure_date 仅表示已发生的交易所披露；预排期使用独立字段。
    period_end_raw = _safe_str(frontmatter.get("period_end_date"))
    disclosure_raw = _safe_str(frontmatter.get("disclosure_date"))
    if disclosure_raw:
        disclosure_dt = _parse_date(disclosure_raw)
        if disclosure_dt is None and _DATE_SEP_RE.match(disclosure_raw.strip()):
            _add_finding(
                findings,
                "HYM-001",
                SEVERITY_WARNING,
                f"``disclosure_date={disclosure_raw}`` 不是合法日历日期",
                "核对月份和日期并按 ``YYYY-MM-DD`` 填写实际交易所披露日。",
                field_name="disclosure_date",
            )
        elif disclosure_dt and disclosure_dt > ref_today:
            _add_finding(
                findings,
                "HYM-001",
                SEVERITY_WARNING,
                f"``disclosure_date={disclosure_raw}`` 晚于今日（{ref_today.isoformat()}），"
                "疑似未来披露日",
                "核对披露日是否填写错误；若为预排期，请改填 "
                "``scheduled_disclosure_date``，不要占用实际 ``disclosure_date``。",
                field_name="disclosure_date",
            )

    # ── HYM-002：period_end_date 晚于 disclosure_date（error）
    if period_end_raw and disclosure_raw:
        period_end_dt = _parse_date(period_end_raw)
        disclosure_dt = _parse_date(disclosure_raw)
        if period_end_dt and disclosure_dt and period_end_dt > disclosure_dt:
            _add_finding(
                findings,
                "HYM-002",
                SEVERITY_ERROR,
                f"``period_end_date={period_end_raw}`` 晚于 ``disclosure_date={disclosure_raw}``，"
                "报告期末日不可能晚于公告日",
                "核对 ``period_end_date``（如 2025H1 → 2025-06-30）与 "
                "``disclosure_date``（公告披露日）是否填反。",
                field_name="period_end_date",
            )

    # ── HYM-003：report_type=半年报/中报 但 period 非半年报周期（warning）
    if report_type in STRICT_HALF_YEAR_REPORT_TYPES:
        period = _safe_str(frontmatter.get("financial_period"))
        # 仅在 period 格式合法时做周期一致性校验；缺失/格式非法由 HYF-001 覆盖。
        if period and _is_valid_financial_period(period):
            token = _extract_period_token(period)
            if token and (
                token in _ANNUAL_PERIOD_TOKENS
                or token in _OTHER_PERIOD_TOKENS
            ):
                _add_finding(
                    findings,
                    "HYM-003",
                    SEVERITY_WARNING,
                    f"``report_type={report_type}`` 期望半年报周期，"
                    f"但 ``financial_period={period}`` 非半年报（{token}）",
                    "核对 ``financial_period``：半年报应为 ``2025H1`` / ``2025中报`` / "
                    "``2025半年报``；或将 ``report_type`` 改为 ``财报分析``（通用类型）。",
                    field_name="financial_period",
                )

    # ── HYM-004：symbols CODE/NAME 错配（warning）
    _check_symbol_name_consistency(frontmatter, has_symbols, findings)

    # ── HYM-007：period_end_date 与 financial_period 不一致（warning）
    if period_end_raw:
        period = _safe_str(frontmatter.get("financial_period"))
        if period and _is_valid_financial_period(period):
            _check_period_end_consistency(
                period_end_raw, period, findings
            )

    return findings


def _check_symbol_name_consistency(
    frontmatter: Dict[str, Any],
    has_symbols: bool,
    findings: List[LintFinding],
) -> None:
    """[HY-011] HYM-004：校验 symbols 字段 CODE/NAME 格式与一致性。"""
    if not has_symbols:
        return  # symbols 缺失由 HYF-002 覆盖，HYM-004 不重复报。
    symbols_raw = frontmatter.get("symbols")
    if isinstance(symbols_raw, str):
        entries = [symbols_raw]
    elif isinstance(symbols_raw, (list, tuple)):
        entries = [s for s in symbols_raw if s is not None]
    else:
        return

    parsed: List[Tuple[str, str]] = []
    parse_failed = False
    for entry in entries:
        parsed_entry = _parse_symbol_entry(entry)
        if parsed_entry is None:
            parse_failed = True
            continue
        parsed.append(parsed_entry)

    if parse_failed:
        _add_finding(
            findings,
            "HYM-004",
            SEVERITY_WARNING,
            "``symbols`` 字段存在无法解析为 ``CODE NAME`` 的条目",
            "每条 symbols 应为 ``603296.SH 华勤技术``（代码+空格+简称）；"
            "缺失空格分隔或 NAME 为空都会被 HYM-004 标记。",
            field_name="symbols",
        )
        return

    # 校验 CODE 格式
    bad_codes = [code for code, _ in parsed if not _is_supported_symbol_code(code)]
    if bad_codes:
        _add_finding(
            findings,
            "HYM-004",
            SEVERITY_WARNING,
            f"``symbols`` 中 CODE 不符合支持的市场代码格式：{bad_codes}",
            "代码应使用 A 股 ``603296.SH``、港股 ``0700.HK`` 或美股 "
            "``DELL.US`` 等标准格式。",
            field_name="symbols",
        )
        return

    # 若 frontmatter 显式提供 name 字段，校验与 symbols 内 NAME 一致
    explicit_name = _safe_str(frontmatter.get("name"))
    if explicit_name and parsed:
        names_in_symbols = {name for _, name in parsed}
        if explicit_name not in names_in_symbols:
            _add_finding(
                findings,
                "HYM-004",
                SEVERITY_WARNING,
                f"frontmatter ``name={explicit_name}`` 与 ``symbols`` 内简称 "
                f"{sorted(names_in_symbols)} 不一致",
                "统一 ``name`` 字段与 ``symbols`` 中 ``CODE NAME`` 的 NAME 部分；"
                "HY-003 事实索引按 symbol 建表，错配会导致同公司分裂为两个主体。",
                field_name="name",
            )


def _check_period_end_consistency(
    period_end_raw: str,
    period: str,
    findings: List[LintFinding],
) -> None:
    """[HY-011] HYM-007：校验 period_end_date 合法且与报告期一致。"""
    period_end_dt = _parse_date(period_end_raw)
    if not period_end_dt:
        _add_finding(
            findings,
            "HYM-007",
            SEVERITY_WARNING,
            f"``period_end_date={period_end_raw}`` 不是合法日历日期",
            "按 ``YYYY-MM-DD`` 填写财务报告期末日，例如 ``2025H1`` 对应 "
            "``2025-06-30``；不要填公告发布日。",
            field_name="period_end_date",
        )
        return
    expected_end = _expected_period_end(period)
    if expected_end is None:
        return
    if period_end_dt != expected_end:
        _add_finding(
            findings,
            "HYM-007",
            SEVERITY_WARNING,
            f"``period_end_date={period_end_raw}`` 与 ``financial_period={period}`` "
            f"期望期末日（{expected_end.isoformat()}）不一致",
            f"``{period}`` 的报告期末日应为 {expected_end.isoformat()}；"
            "核对 ``period_end_date`` 是否填错为资料发布日期或公告日。",
            field_name="period_end_date",
        )


# ── 跨页 sanity 规则 ──────────────────────────────────────────────────


@dataclass
class _PageMeta:
    """[HY-011] 跨页检查需要的最小页元信息（避免携带整页正文）。"""

    rel_path: str
    symbols: List[str]  # 解析后的 CODE 列表（用于分组）
    period: Optional[str]  # financial_period（已校验合法）
    is_revision: bool
    revision_fields: List[str]


def _extract_page_meta_for_cross_check(
    page: Any,
) -> Optional[_PageMeta]:
    """[HY-011] 从 PageLintResult 提取跨页检查所需的最小元信息。

    只读取 frontmatter-derived 字段（symbols / period / 修订标记），不读正文。
    非 half_year_report 页直接返回 ``None``，不参与跨页分组——避免普通公司点评
    页被误并入冲突组。
    """
    # PageLintResult 可能未带 frontmatter 字段；通过 rel_path 重新读取会破坏只读
    # 约束。改由调用方在 lint_single_page 阶段把 frontmatter 缓存到 page 上。
    # 这里只做最小提取。
    if not getattr(page, "is_half_year_report", False):
        return None
    period = getattr(page, "half_year_period", None)
    if not period:
        return None
    # symbols 由调用方在 lint_single_page 阶段缓存（避免再次解析 frontmatter）。
    symbols = list(getattr(page, "_hym_symbol_codes", []) or [])
    if not symbols:
        return None
    is_rev = bool(getattr(page, "_hym_is_revision", False))
    rev_fields = list(getattr(page, "_hym_revision_fields", []) or [])
    return _PageMeta(
        rel_path=getattr(page, "rel_path", ""),
        symbols=symbols,
        period=period,
        is_revision=is_rev,
        revision_fields=rev_fields,
    )


def check_half_year_metadata_cross_page(
    pages: Iterable[Any],
) -> Dict[str, List[LintFinding]]:
    """[HY-011] 跨页 HYM-005/006：同 symbol + 同 financial_period 多版本冲突 / 修订。

    参数:
        pages: ``PageLintResult`` 可迭代对象（已跑完单页 lint，并带 ``_hym_*``
            缓存字段——由 ``lint_local_knowledge`` 在调用本函数前注入）。

    返回:
        ``{rel_path: [LintFinding, ...]}`` 映射；只包含命中的页。未命中页不在
        字典中。

    逻辑：
      1. 提取每页的 ``(symbols, period, is_revision)``，过滤掉非半年报页。
      2. 按 ``(symbol_code, period)`` 分组。
      3. 组内 >1 页时：
         - 若**全部**无修订标记 → HYM-005（warning）每个冲突页各加 1 条。
         - 若**至少一个**有修订标记 → HYM-006（info）每个有修订标记的页加 1 条，
           提示事实索引优先选修订稿。
    """
    metas: List[_PageMeta] = []
    for page in pages:
        meta = _extract_page_meta_for_cross_check(page)
        if meta is not None:
            metas.append(meta)

    if not metas:
        return {}

    # 按规范化 (symbol_code, period) 分组。一个页有多个 symbol 时进入多个组；
    # 603296/603296.SH 与 2025H1/2025中报/2025Q2 视为同一组。
    groups: Dict[Tuple[str, str], List[_PageMeta]] = {}
    for meta in metas:
        for code in meta.symbols:
            key = (
                _canonical_symbol_group_key(code),
                _canonical_period_group_key(meta.period or ""),
            )
            groups.setdefault(key, []).append(meta)

    out: Dict[str, List[LintFinding]] = {}
    for (code, period), members in groups.items():
        if len(members) < 2:
            continue
        # 同一 rel_path 可能因多 symbol 重复进入；组内按 rel_path 去重。
        seen_paths: set = set()
        unique_members: List[_PageMeta] = []
        for m in members:
            if m.rel_path not in seen_paths:
                seen_paths.add(m.rel_path)
                unique_members.append(m)
        if len(unique_members) < 2:
            continue

        any_revision = any(m.is_revision for m in unique_members)
        all_paths = sorted(m.rel_path for m in unique_members)
        all_paths_str = " / ".join(f"``{p}``" for p in all_paths)

        if not any_revision:
            # HYM-005：多版本无修订标记
            for m in unique_members:
                others = [p for p in all_paths if p != m.rel_path]
                others_str = " / ".join(f"``{p}``" for p in others)
                _add_finding(
                    out.setdefault(m.rel_path, []),
                    "HYM-005",
                    SEVERITY_WARNING,
                    f"同 ``{code}`` + ``financial_period={period}`` 出现 "
                    f"{len(unique_members)} 个版本但均无修订标记：{all_paths_str}",
                    "为修订版补 ``revision: 2`` / ``is_revised: true`` / "
                    f"``supersedes: <原稿路径>``；或合并/删除冗余版本。"
                    f"其他冲突页：{others_str}。事实索引（HY-003）遇到无修订标记的"
                    "多版本会随机取最新 updated，存在错期风险。",
                )
        else:
            # HYM-006：检测到修订稿（info，不阻塞）
            revised = [m for m in unique_members if m.is_revision]
            for m in revised:
                _add_finding(
                    out.setdefault(m.rel_path, []),
                    "HYM-006",
                    SEVERITY_INFO,
                    f"同 ``{code}`` + ``financial_period={period}`` 检测到修订稿"
                    f"（命中字段 {m.revision_fields}）",
                    "事实索引（HY-003）应优先选择本修订稿；原稿来源路径保留在 "
                    "``source_links`` 中不删除。其他版本："
                    + all_paths_str,
                )

    return out


__all__ = [
    "HYM_RULE_IDS",
    "REVISION_FIELDS",
    "STRICT_HALF_YEAR_REPORT_TYPES",
    "detect_revision_marker",
    "check_half_year_metadata_sanity_single",
    "check_half_year_metadata_cross_page",
]
