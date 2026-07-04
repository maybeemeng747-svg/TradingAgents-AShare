# TF-KB-001 本地知识分校准回放与弱候选防提升（2026-07-05）

- **状态**：PASS
- **local_knowledge_score 上限**：3.0
- **research_attention_effective_score 排序参考上限**：8.0

## 核心不变量

> 本地知识分只能作为解释和排序辅助，不得单独触发候选入池，不得改变 action_tier / action / 强动作门禁。

`action_tier_scorer.run_action_tier_scorer` 的 7 个评分因子均不读取知识分字段；过热/反证只会*降低*研究优先级，不会因知识命中多而*提升* tier。

## 对抗性回归

- scorer 签名不含 knowledge/attention 参数：是
- 知识分拉到极值 tier/score 不变：全部通过
- explain 不含买卖建议词：全部通过

## 三套 fixture 回放

| 场景 | symbol | scorer tier | 期望 tier | 弱候选保持 | 提升阻断 | 结果 |
|---|---|---|---|---|---|---|
| S1_strong_knowledge_weak_technical | 603296 | scan | scan | True | True | PASS |
| S2_strong_knowledge_technical_confirmed | 300888 | watch | watch | False | True | PASS |
| S3_no_knowledge_strong_technical | 600519 | actionable | actionable | False | False | PASS |

## 候选 explain（为什么没提升 / 为什么只是加解释）

### S1_strong_knowledge_weak_technical（603296）
- scorer tier：scan（trade_priority_score=0.2146）
- local_knowledge_score=3.00（上限3.0）仅作研究优先级/解释，不进入 action_tier_scorer
- research_attention_effective_score=5.00（过热惩罚后）只降低研究优先级，不改变强动作门禁
- 弱/降级候选保持 scan：数据完整度低(10%<30%)；综合信号弱(composite=8.0)；信号类别不足(1<2)；过热(短期过热)；知识分未提升 tier

### S2_strong_knowledge_technical_confirmed（300888）
- scorer tier：watch（trade_priority_score=0.4147）
- local_knowledge_score=3.00（上限3.0）仅作研究优先级/解释，不进入 action_tier_scorer
- research_attention_effective_score=4.00（过热惩罚后）只降低研究优先级，不改变强动作门禁

### S3_no_knowledge_strong_technical（600519）
- scorer tier：actionable（trade_priority_score=0.8906）
- 本地知识无 fresh 命中（NORMAL_NO_DATA）：不参与命中分计算，候选排序完全由技术/数据门禁决定

## 验收对照

- 技术弱 + 知识强不会进入主候选：S1 保持 scan，知识分 3.00 未提升 tier。
- 技术确认 + 知识强可提高研究优先级但不输出强买卖：S2 保持 watch，explain 明确知识只加解释、未触发 actionable。
- 数据不足时仍走 observation/filtered：S1 完整度 10% 走 scan，知识命中不改变 NEED_DEEP_TA/OBSERVE 路径。
- 无知识命中但技术强：S3 仅凭技术/数据门禁进入 actionable，知识不是必要条件。
