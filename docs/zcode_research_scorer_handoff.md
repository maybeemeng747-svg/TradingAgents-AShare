# ZCode `research-scorer` 开发任务包

> 归属：ZCode / 知识库接入项目  
> 消费方：TradingAgents-AShare（TA / TradeFlow）  
> 原则：ZCode 读取现有知识库并写研究快照；TA 只读快照并补动态时机、账户适配、门禁和阶段。

现有知识页可以继续由 Tree Work 维护，历史路径和任务名称不做全局改名；从
`research-scorer`、契约发布、快照写入和 API 对接开始，新增开发统一使用 ZCode。

## 1. 它是什么

`research-scorer` 是知识库的研究判断引擎，不是 TA Agent，也不是交易决策器。

它把已经消化的研报、公告、财报和研究笔记整理为可追溯的慢变量快照：

```text
原始资料
  -> evidence_items（证据条目）
  -> investment_theses（投资假设）
  -> research-scorer（确定性规则）
  -> research_score_snapshot（版本化只读产物）
```

TA/TradeFlow 只消费 `research_score_snapshot`，不得重新计算或覆盖知识分。

## 2. 所有权与禁止越界

| 数据/规则 | 正式写入方 | TA 是否可改 |
|---|---|---|
| `evidence_items` | 知识库 | 否 |
| `investment_theses` | 知识库 | 否 |
| `research_score_snapshots` | 知识库 | 否 |
| 证据分/逻辑分公式及版本 | 知识库 | 否 |
| `entry_timing` | TradeFlow | 是 |
| `portfolio_fit` | TA/账户上下文 | 是 |
| `vetoes`、阶段、动作、决策日志 | TA | 是 |

知识库产物禁止包含或决定：`entry_timing`、`portfolio_fit`、`action`、
`execution_action`、`playbook_stage`、`planned_position`、`buy_level`、
`risk_level`。

## 3. 正式快照契约

> 2026-07-14 契约确认：ZCode 已交付并通过 Codex 审核的 Schema v1.1.0、
> 本地 Registry 校验器、五状态 fixture 与 `validate-pair`。当前仅有
> `603629.SH` 的 `LOW_CONFIDENCE` 双文件草案，尚无正式发布快照；TA 的
> SCORE-001 可以据此开发只读 loader，但 SCORE-006 仍不得把草案冒充生产验收。

知识库至少发布以下结构；缺失项使用 `null` 与显式状态，不得用 `0` 冒充已评分：

```json
{
  "schema_version": "1.1.0",
  "snapshot_id": "605589.SH-20260713-r1",
  "symbol": "605589.SH",
  "name": "圣泉集团",
  "as_of": "2026-07-13",
  "created_at": "2026-07-13T20:00:00+08:00",
  "source_cutoff_at": "2026-07-13T15:00:00+08:00",
  "rubric_id": "research-score-generic",
  "rubric_version": "1.0.0",
  "status": "HAS_DATA",
  "scores": {
    "research_evidence_confidence": 82,
    "thesis_quality": 78
  },
  "thesis_breakdown": {},
  "theses": [
    {
      "thesis_id": "605589.SH-thesis-001",
      "symbol": "605589.SH",
      "as_of": "2026-07-13",
      "topic": "示例投资假设",
      "direction": "bullish",
      "status": "active",
      "core_hypothesis": "示例假设已由原始财报与独立研报交叉核验",
      "supporting_evidence_ids": ["fixture-605589-2026h1-fact-001", "fixture-605589-thesis-002"],
      "counter_evidence_ids": [],
      "missing_evidence": [],
      "upgrade_conditions": [],
      "downgrade_conditions": [],
      "invalidation_conditions": []
    }
  ],
  "score_change": {
    "previous_snapshot_id": null,
    "previous": {},
    "current": {},
    "reasons": []
  },
  "evidence_refs": [
    {
      "evidence_id": "fixture-605589-2026h1-fact-001",
      "claim": "半年报关键财务事实已完成原文定位",
      "claim_type": "financial_fact",
      "source_path": "fixtures/research_score/605589.SH/2026H1-facts.md",
      "source_type": "financial_report",
      "source_quality_tier": "original_filing",
      "report_date": "2026-07-13",
      "financial_period": "2026H1",
      "locator": "facts.key_financials"
    },
    {
      "evidence_id": "fixture-605589-thesis-002",
      "claim": "核心投资假设已由独立来源交叉核验",
      "claim_type": "thesis_support",
      "source_path": "fixtures/research_score/605589.SH/thesis-support.md",
      "source_type": "research_report",
      "source_quality_tier": "broker_research",
      "report_date": "2026-07-12",
      "financial_period": null,
      "locator": "thesis.supporting_evidence"
    }
  ],
  "missing_evidence": [],
  "upgrade_conditions": [],
  "downgrade_conditions": [],
  "invalidation_conditions": [],
  "warnings": ["EXAMPLE_FIXTURE_ONLY"]
}
```

上述路径和内容仅用于契约示例。ZCode 交付测试 fixture 时必须同时创建这些相对路径，
并验证每条 `evidence_ref` 能在 fixture 根目录内解析；正式快照不得引用示例文件。

合法状态至少包括：`HAS_DATA / STALE / LOW_CONFIDENCE /
NORMAL_NO_DATA / FAILED`。历史回放必须按 `as_of <= analysis_time` 读取，禁止使用未来快照。

## 4. 知识库开发任务

### 4.0 2026-07-14 磁盘实审结论与执行顺序

本次按实际文件复核，不按对话汇报判定完成度：

| 能力 | 实际状态 | 证据 / 缺口 |
|------|----------|-------------|
| Schema + 本地 `$ref` Registry | ✅ 已完成 | 3 个 schema，v1.1.0；41 tests passed |
| JSON/Markdown 双文件校验 | ✅ 已完成 | `validate` + `validate-pair`，603629 草案通过 |
| 不可覆盖发布器 | ✅ 基础完成 | `publish` 可校验后原子写入且拒绝覆盖 |
| `/score` 可发现命令 | ❌ 未完成 | skill 内有自然语言说明，但 `.zcode/commands/score.md` 不存在，所以客户端无 `/score` |
| 证据可信度确定性计分 | ❌ 未完成 | 当前脚本明确声明 `does not calculate research scores` |
| 投资逻辑质量确定性计分 | ❌ 未完成 | 仅有 schema/rules，没有版本化 rubric 执行器 |
| snapshot delta/lifecycle | 🟡 部分完成 | schema 有 `score_change`，但没有根据前一快照生成 delta/hash/review 状态的引擎 |
| symbol + as_of 只读查询 | ❌ 未完成 | 只有文件 load/publish，无 latest-valid 查询入口 |
| 正式真实快照 | ❌ 未完成 | 正式目录为空；603629 仍在 `drafts/`，不得给 TA 当生产数据 |
| 历史评分对抗回放 | 🟡 部分完成 | 41 条契约测试通过，但没有多版本真实评分可复现回放 |

ZCode 按以下顺序开发，不能先跳到正式发布：

1. **ZC-RS-000（P0）命令接线**：新增 `.zcode/commands/score.md`，调用同一 skill/rule；自然语言和 `/score` 必须走同一双文件、validate、validate-pair 流程，默认只生成 draft。
2. **ZC-RS-002（P0）证据分执行器**：版本化、确定性、可复现，输入 evidence_items，输出 coverage、分项、总分/null 和逐项引用；禁止 LLM 直接填最终分。
3. **ZC-RS-003（P0）逻辑分执行器**：通用 rubric + 行业 profile，输出 thesis_breakdown、证据闭包和 invalidated 语义。
4. **ZC-RS-004（P1）生命周期生成器**：读取上一正式快照，生成 previous/current/delta/reasons、内容 hash、人工 review 状态和不可变新版本。
5. **ZC-RS-005（P1）只读查询器**：按 `symbol + as_of <= analysis_time` 返回最新合法正式快照；排除 drafts，路径失败关闭。
6. **ZC-RS-006（P1）真实验收**：八类对抗 fixture + 至少一只真实股票经人工确认后正式发布；再通知 TA 释放 SCORE-006。

`ZC-RS-000~005` 可以在 ZCode 仓库内连续开发；第一次正式快照发布和
`ZC-RS-006` 必须停下来等人工确认来源与评分，不得自动把草案转正。

### ZC-RS-000: `/score` 命令发现与统一入口（P0）

- 新增 `.zcode/commands/score.md`，解决客户端重启后仍没有 `/score` 的真实缺口。
- 命令只负责编排：读取 `research-scorer.md` → 生成 MD/JSON draft → validate →
  validate-pair；不得复制评分公式或绕过校验器。
- 默认不 publish；只有用户明确批准并且校验全过才允许调用 publish。
- 测试自然语言“给 603629 打研究分”和 `/score 603629` 产物契约一致。

### ZC-RS-001: 证据条目与投资假设结构化契约

- 定义 `evidence_items`：claim、claim_type、source_path/source_url、source_type、
  source_quality_tier、report_date、financial_period、freshness、原文定位、symbol。
- 定义 `investment_theses`：主题、方向、核心假设、支持/反证、升级/降级/证伪条件。
- 复用现有 citation/source tier；不要再存第二套 A-E 等级，可在 UI 派生显示。
- 同一机构同一报告或同一事实转载必须去重，不能制造虚假多来源一致。

### ZC-RS-002: 研究证据可信度评分

- 只评价研究证据：来源质量、字段完整度、时效性、独立来源一致性和原文可追溯性。
- 每个得分必须能回溯 `evidence_refs`；无引用不得加分。
- 缺数据是 `null/insufficient_evidence`，不是 0 分事实；另行输出 coverage。
- 弱来源、旧资料被新公告覆盖、关键数字无法定位原文时必须降级。

### ZC-RS-003: 投资逻辑质量评分

- 通用七维：产业必要性、技术/认证壁垒、客户订单量产、收入利润兑现、竞争与国产替代、财务质量、成长与估值匹配。
- 使用 `rubric_id/rubric_version`；通用维度保持稳定，行业专属细则放 profile，不把 AI 材料规则硬套所有行业。
- LLM 只可抽取 claim/证据候选；最终计分必须由可复现规则执行。
- `growth_valuation_match` 只基于快照时点和明确估值假设，不读取 TA 实时行情，也不替代 `entry_timing`。

### ZC-RS-004: 版本化快照、分数变化与审计日志

- 每次新资料、修订财报或事实反证后生成不可变 snapshot；旧快照保留。
- 输出 previous/current/delta，以及逐条变化原因和对应新旧证据。
- 同机构重复报告不得推动分数；纯股价上涨不得推动 `thesis_quality`。
- 支持 `supersedes_snapshot_id`、内容 hash、规则版本和人工 review 状态。

### ZC-RS-005: 只读发布接口

- 提供稳定 JSON 文件、SQLite 只读表或本地 API 三者之一；推荐同时有 JSON fixture 与只读 API。
- 至少支持按 symbol + as_of 查询最新合法快照，并返回状态/错误，不返回长篇研报正文。
- 路径限制在知识根目录内；不得泄露 key、cookie、token 或任意本机路径。
- TA 无写权限；GPT/人工审核只能提交 review suggestion，不直接覆盖正式快照。

### ZC-RS-006: 历史回放与对抗验收

- 覆盖高质量多来源、只有券商观点、只有传闻、证据冲突、过期、修订稿、同机构重复、缺关键财务事实、逻辑被证伪。
- 验证评分可复现、重复执行幂等、旧快照不被覆盖、未来数据不穿越。
- 验收报告需列出 score、delta、evidence refs、missing evidence 和人工复核项。
- 不测试或输出买卖动作；跨项目动作验收由 TA 的 SCORE-006 完成。

## 5. 交付给 TA 的最小内容

ZCode 完成后，只需向 TA 提供：

1. 契约版本和一份合法 JSON Schema。
2. 至少八类 fixture 快照。
3. 至少一只真实股票的只读快照及来源路径。
4. 查询方式、状态语义、更新时间和失败语义。
5. 明确声明：快照不含交易动作，TA 不得回写。

TA 收到后执行本仓库 `SCORE-001~006`：只读接入 → entry timing →
portfolio fit → hard veto → 七阶段 → 对抗回放。
