# 半年报 fixture 样本集与契约回放基线（KB-013）

> 生成时间：2026-07-07
> 任务：`KB-013` — 半年报 fixture 样本集与契约回放基线（P1）
> 代码标注：`# [KB-013] half_year_fixture_baseline`
> 契约基线：`docs/local_knowledge_contract.md`（`kb-002-v1` + HY-001 扩展）

## 1. 目的

为 HY 系列任务（HY-001 / HY-003 / HY-005 / HY-008）提供**不依赖真实知识库**的、
字段 / 状态 / 失败路径全部固定的半年报样本集。任何 HY 任务在编写测试时都应优先复用
本样本集，而不是各自构造 inline 字符串或读取 `~/Documents/knowledge`。

设计约束（来自 KB-013 任务）：

- 不修改 `~/Documents/knowledge/`；
- fixture 不包含长篇研报 / 财报原文（受 KB-003 `_SUMMARY_MAX_CHARS=200` 约束）；
- 不调用 live LLM；
- fixture 测试可独立运行；
- 可被 HY-001 / HY-003 / HY-005 后续任务复用。

## 2. 字段命名规约

KB-013 统一了 `symbol / name / source_type / financial_period` 四个最易漂移的字段命名，
全部与 `docs/local_knowledge_contract.md`（§2 + §10）保持一致：

| 字段 | 规约 | 示例 |
|------|------|------|
| `symbols` / name | `<6位代码>.<交易所> <简称>`（A 股 SH/SZ/BJ，港股 HK，美股 US） | `000977.SZ 浪潮信息` |
| `source_type` | 事实类：`exchange_filing` / `fact_table` / `management_commentary`；观点类：`broker_report` / `media` | `[exchange_filing, broker_report]` |
| `financial_period` | 优先 `YYYYH1` / `YYYYH2`（兼容 `YYYY中报` / `FY26Q1`） | `2025H1` |
| `report_type`（HYF 触发） | `财报分析` / `半年报` / `中报` 三者之一才进入 HYF 扩展 | `半年报` |
| `disclosure_date` | `YYYY-MM-DD` | `2026-08-29` |

`source_type` 取值表在 fixture 模块中直接从 `local_knowledge_lint` 导入
（`SOURCE_TYPE_FACT_VALUES` / `SOURCE_TYPE_OPINION_VALUES` / `SOURCE_TYPE_ALL_VALUES`），
保证与 lint 契约同源、不漂移。

## 3. 样本清单

所有样本定义在 `tests/half_year_fixtures.py`，每条样本对应一个
`HalfYearFixtureSpec`，预先固定该样本在 KB-002 + HY-001 lint 下的预期结果。

| name | 文件名 | 场景 | is_half_year | readiness | HYF error | HYF warning | 其它 warning | period | opinion_only | 复用对象 |
|------|--------|------|--------------|-----------|-----------|-------------|--------------|--------|--------------|----------|
| `qualified` | 浪潮信息000977-2025H1半年报.md | 合格半年报 | ✓ | high | — | — | — | 2025H1 | ✗ | HY-001/003/004/008 |
| `missing_period` | 华勤技术603296-缺报告期半年报.md | 缺报告期 | ✓ | medium | HYF-001 | — | — | — | ✗ | HY-001/002 |
| `fact_opinion_mix` | 海康威视002415-2025H1半年报-事实观点混用.md | 事实/观点混用 | ✓ | high | — | — | — | 2025H1 | ✗ | HY-005/014, KB-014 |
| `expired` | 贵州茅台600519-2024H1半年报-已过期.md | 过期 | ✓ | medium | — | — | STALE-002 | 2024H1 | ✗ | HY-003/004 |
| `weakened_old_opinion` | 宁德时代300750-2025H1半年报-旧观点被削弱.md | 旧观点被削弱 | ✓ | high | — | — | — | 2025H1 | ✗ | HY-005/008 |
| `non_financial_control` | 平安银行000001-公司点评.md | 控制组（非财报） | ✗ | high | — | — | — | — | ✗ | HY-001/003 |

### 3.1 各样本语义

- **`qualified`（合格半年报）**
  - 公司 `000977.SZ 浪潮信息`，`financial_period=2025H1`。
  - KB-002 通用契约 + HY-001 全字段满足，0 finding，`readiness=high`。
  - 契约黄金样本：任何 lint 改动不应使其产生 finding。

- **`missing_period`（缺报告期）**
  - 公司 `603296.SH 华勤技术`，`report_type=半年报` 但缺 `financial_period`。
  - 仅命中 HYF-001（error），其余 HY 字段齐全，单点失败便于回归。
  - `readiness` 被压到 medium（1 error）。

- **`fact_opinion_mix`（事实/观点混用）**
  - 公司 `002415.SZ 海康威视`，`source_type=[exchange_filing, broker_report]`。
  - 同时含事实类与观点类 → **不**触发 HYF-007（非“全是观点”）。
  - 为 HY-005 / HY-014 / KB-014 提供基线：lint 不判观点冒充事实，但下游须把
    `broker_report` 部分降权，不得作为事实进入反证。

- **`expired`（过期）**
  - 公司 `600519.SH 贵州茅台`，`valid_until=2025-12-31`（已过期），`financial_period=2024H1`。
  - 命中 STALE-002（warning）+ `is_stale=True`；HY 字段齐全（仍跑 HYF 规则，无 HYF finding）。
  - `is_stale` 阻止 high → 落到 medium；TA 命中后须标 STALE，不得用于实时结论。

- **`weakened_old_opinion`（旧观点被削弱）**
  - 公司 `300750.SZ 宁德时代`，`financial_period=2025H1`。
  - `management_commentary` 持有 2026-02 旧观点（“储能业务下半年将迎爆发式增长”），
    `financial_facts`（2026-08 披露）显示“储能系统营收 -20.0% YoY” → 事实与旧观点矛盾。
  - **当前 lint 不检测矛盾**（事实字段齐全 → `readiness=high`），是 HY-005 事实反证
    检测的**数据种子**；HY-005 将基于本样本比较 management_commentary/forward_guidance
    （观点）与 financial_facts（事实）实现反证。

- **`non_financial_control`（控制组）**
  - 公司 `000001.SZ 平安银行`，`report_type=公司点评`。
  - 负样本控制：确保 HYF 规则不会误伤非财报页（`is_half_year_report=False`，无 HYF finding）。

## 4. 复用方式

```python
from tests.half_year_fixtures import (
    FIXTURE_SPECS,
    FIXTURES_BY_NAME,
    build_half_year_fixture_kb,
)
from tradingagents.dataflows.local_knowledge_lint import lint_local_knowledge

def test_my_hy_feature(tmp_path):
    # 写入全部样本
    root = build_half_year_fixture_kb(tmp_path)
    result = lint_local_knowledge(str(root))
    assert result.page_count == 6

    # 只写指定样本（按 name）
    root = build_half_year_fixture_kb(tmp_path, include=["qualified", "weakened_old_opinion"])

    # 按 name 取单个 spec 的预期契约
    spec = FIXTURES_BY_NAME["expired"]
    assert spec.expected_other_warnings == ["STALE-002"]
```

`build_half_year_fixture_kb` 在 `tmp_path` 下构造微型 Tree Work 知识库：

```
tmp_path/
  wiki/
    index.md
    log.md
    investment/
      <fixture filename>.md
      ...
```

可直接传给 `lint_local_knowledge(knowledge_root)`。`include=None` 写入全部样本；
传入未知 name 抛 `KeyError`，避免静默漏写。

## 5. 与 HY-001 inline fixture 的关系

HY-001 的 `tests/test_hy001_half_year_contract.py` 已有 7 个 inline fixture
（合格页 / 缺报告期+缺代码 / opinion_only / 缺事实+缺风险 / 缺披露日 / 缺 source_type / 非财报）。
KB-013 的样本集与 HY-001 互补，**不替换**它：

| 维度 | HY-001 inline fixture | KB-013 共享样本集 |
|------|----------------------|------------------|
| 归属 | 单测试文件私有 | `tests/half_year_fixtures.py` 共享模块 |
| 覆盖 | HYF-001~007 各规则单测 | 5 个业务场景 + 控制组 |
| 缺口 | 无“过期” / “旧观点被削弱”场景 | 补齐 expired / weakened_old_opinion |
| 复用 | 仅 HY-001 自身 | HY-001/003/005/008/014/KB-014 均 `import` |

KB-013 新增的**净贡献**场景：`expired`（STALE-002 与 HYF 规则的交互）、
`weakened_old_opinion`（HY-005 事实反证的数据种子）。其余场景与 HY-001 同契约、
不同公司，提供更丰富的 symbol 区分度（6 个不同代码）便于 HY-003 provider 查询测试。

## 6. 验证

- 测试文件：`tests/test_kb013_half_year_fixture_baseline.py`（103 tests passed）。
- 独立运行：`pytest tests/test_kb013_half_year_fixture_baseline.py -q` 不依赖真实知识库。
- 无回归：`pytest tests/test_hy001_half_year_contract.py tests/test_kb002_local_knowledge_lint.py tests/test_kb013_half_year_fixture_baseline.py -q` → 226 passed。
- 只读安全：fixture 构造与 lint 跑两次幂等，不触碰 `~/Documents/knowledge`。
- 无长篇正文 / 无强动作词（立即买入/卖出/满仓/清仓/全仓/强烈推荐）。

## 7. 后续扩展建议（不在 KB-013 范围内）

- 港股 / 美股中报样本（`DELL.US Dell` + `FY26Q1`），供 HY-008 端到端回放。
- 多 symbol 产业链页样本（`report_type=产业链`），供 HY-003 segment_facts 聚合。
- `deprecated: true` 归档路径样本，供 KB-005 / KB-012 归档回放。
