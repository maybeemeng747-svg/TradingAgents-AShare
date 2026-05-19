# LLM API 使用规则

> 这份文档给 OpenClaw、OpenCode、Codex 和后续子 agent 使用。目标是让大家知道当前项目有哪些模型配置，同时避免擅自消耗用户 token 或泄露密钥。

## 先读和先查

任何 agent 如果要改模型、调用模型、排查模型问题，先读：

1. `docs/MODEL_API_CATALOG.md` — 项目支持的模型端点目录
2. 本文件 — 模型 API 使用边界
3. 运行只读审计脚本：

```bash
source .venv/bin/activate
python scripts/audit_llm_config.py
```

脚本只输出 provider、base URL、模型名和 `HAS_KEY/NO_KEY`，不会解密或打印 API Key。

## 当前本机主配置快照

最后审计时间：2026-05-15

| 字段 | 当前值 |
|------|--------|
| provider | `openai` |
| base_url | `https://open.bigmodel.cn/api/coding/paas/v4` |
| quick_model | `glm-4.5-air` |
| deep_model | `glm-5-turbo` |
| max_debate_rounds | `2` |
| max_risk_discuss_rounds | `1` |

当前主用户保存的 key scope：

| key_scope | 状态 | 备注 |
|-----------|------|------|
| `openai:https://open.bigmodel.cn/api/coding/paas/v4` | `HAS_KEY` | 默认推荐，智谱 Coding Plan |
| `openai:https://token-plan-cn.xiaomimimo.com/v1` | `HAS_KEY` | 小米 MiMo Token Plan |
| `openai:https://api.deepseek.com` | `HAS_KEY` | DeepSeek，余额紧张时不要默认使用 |
| `openai:https://api.deepseek.com/v1` | `HAS_KEY` | DeepSeek v1，余额紧张时不要默认使用 |

## 使用边界

- 定时任务：用户主动创建并启用的定时分析任务可以正常消耗 token。
- 手动分析：用户明确要求“跑一次分析/测试模型/验证报告”时，可以使用当前配置。
- 代码工作：agent 改代码、写测试、做 review 时，不应自行调用付费 LLM API 来完成普通 coding 工作。
- 需要 live LLM 的测试：必须先说明 provider、base_url、模型名、预计调用次数，并向用户确认。
- 模型切换：不要擅自把默认模型切到 DeepSeek、OpenAI、Moonshot 等付费端点；如需切换先问用户。
- 密钥处理：禁止读取、解密、打印、复制、提交任何 API Key 明文。只能使用 `HAS_KEY/NO_KEY` 级别的信息。

## 推荐默认

当前建议默认用智谱 Coding Plan：

```text
provider=openai
base_url=https://open.bigmodel.cn/api/coding/paas/v4
quick_model=glm-4.5-air
deep_model=glm-5-turbo
```

如果这个端点不可用，先问用户要不要临时切到小米 MiMo Token Plan；不要自动切 DeepSeek。
