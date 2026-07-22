# FUND-007A 财报错误模式基准集回归报告

**生成时间**: 2026-07-22 21:31:59
**总用例**: 16（负向 11 + 正向 5）
**通过**: 16 / **失败**: 0
**耗时**: 0 ms

## 负向样本（错误模式检测）

| ID | 类别 | 描述 | 预期门禁 | 实际门禁 | 预期规则 | 实际规则 | 结果 |
|---|---|---|---|---|---|---|---|
| NEG-001 | identity_mismatch | 证券身份错配：无公司画像时从财务特征猜行业（603629 历… | NEEDS_REVIEW | NEEDS_REVIEW | IDENTITY_UNVERIFIED, CAUSE_UNSUPPORTED | IDENTITY_UNVERIFIED, CAUSE_UNSUPPORTED | **PASS** |
| NEG-002 | period_scope_error | 年度累计冒充 Q4：将全年 33 亿营收误当第四季度单季值… | NEEDS_REVIEW | NEEDS_REVIEW | CAUSE_UNSUPPORTED | CAUSE_UNSUPPORTED | **PASS** |
| NEG-003 | cross_date_calculation | 跨日期计算：用 2025-12-31 收入与 2025-09… | NEEDS_REVIEW | NEEDS_REVIEW | CAUSE_UNSUPPORTED | CAUSE_UNSUPPORTED | **PASS** |
| NEG-004 | cross_unit_calculation | 跨单位计算 + 无因果证据：收入用亿元、成本用万元，且因果解… | NEEDS_REVIEW | NEEDS_REVIEW | CAUSE_UNSUPPORTED | CAUSE_UNSUPPORTED | **PASS** |
| NEG-005 | unexplained_seasonal | 无官方解释的季节性猜测：公告无旺季/淡季说明，报告凭空猜测… | NEEDS_REVIEW | NEEDS_REVIEW | CAUSE_UNSUPPORTED | CAUSE_UNSUPPORTED | **PASS** |
| NEG-006 | accounting_policy_misread | 净额法/总额法误判：公告明确采用总额法，报告误写净额法… | NEEDS_REVIEW | NEEDS_REVIEW | ACCOUNTING_POLICY_UNKNOWN | ACCOUNTING_POLICY_UNKNOWN | **PASS** |
| NEG-007 | prepaid_cashflow_misread | 预收款与现金流因果误读：报告声称预收款推动现金流，公告只提预… | NEEDS_REVIEW | NEEDS_REVIEW | CAUSE_UNSUPPORTED | CAUSE_UNSUPPORTED | **PASS** |
| NEG-008 | source_missing | 来源全面缺失：无身份、无公告、无财务期间数据… | NEEDS_REVIEW | NEEDS_REVIEW | IDENTITY_UNVERIFIED, PERIOD_SCOPE_INVALID, CAUSE_UNSUPPORTED | IDENTITY_UNVERIFIED, PERIOD_SCOPE_INVALID, CAUSE_UNSUPPORTED | **PASS** |
| NEG-009 | evidence_conflict | 证据冲突：公告明确原材料上涨，报告却声称原材料下降… | NEEDS_REVIEW | NEEDS_REVIEW | CAUSE_UNSUPPORTED | CAUSE_UNSUPPORTED | **PASS** |
| NEG-010 | negation_accounting | 否定会计口径：公告明确未采用净额法（仍用总额法），报告忽略否… | NEEDS_REVIEW | NEEDS_REVIEW | ACCOUNTING_POLICY_UNKNOWN | ACCOUNTING_POLICY_UNKNOWN | **PASS** |
| NEG-011 | derivation_conflict | 单季度派生冲突：仅有单季度值缺累计输入，禁止以全年值替代… | NEEDS_REVIEW | NEEDS_REVIEW | DERIVATION_CONFLICT, CAUSE_UNSUPPORTED | DERIVATION_CONFLICT, CAUSE_UNSUPPORTED | **PASS** |

## 正向控制样本（不得误杀）

| ID | 类别 | 描述 | 预期门禁 | 实际门禁 | 结果 |
|---|---|---|---|---|---|
| POS-001 | valid_factual_report | 合法主营补全 + 同口径计算 + 无因果猜测：纯事实报告通过… | VALID | VALID | **PASS** |
| POS-002 | valid_causal_with_evidence | 有官方证据的因果解释：公告确认原材料下降推动营收增长，报告一… | VALID | VALID | **PASS** |
| POS-003 | valid_accounting_policy | 合法会计口径：公告和报告一致确认总额法… | VALID | VALID | **PASS** |
| POS-004 | valid_no_cause_keywords | 纯事实陈述不含因果关键词：不触发因果检查… | VALID | VALID | **PASS** |
| POS-005 | valid_negation_consistent | 否定会计口径一致：公告和报告均确认未采用净额法… | VALID | VALID | **PASS** |

## 全部通过，无失败项。