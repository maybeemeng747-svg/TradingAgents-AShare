# 模型 API 调用目录

> 统一记录项目知道如何调用的模型端点。实际 API Key 存在用户加密配置或环境变量中，本文和接口都不包含密钥。

代码目录：`tradingagents/llm_clients/model_catalog.py`

API 目录：`GET /v1/config/model-catalog`

使用规则：`docs/LLM_API_USAGE.md`

脱敏审计脚本：

```bash
python scripts/audit_llm_config.py
```

## 推荐默认

当前本地默认建议优先用 **智谱 Coding Plan**：

| 字段 | 值 |
|------|----|
| provider | `openai` |
| base_url | `https://open.bigmodel.cn/api/coding/paas/v4` |
| quick_model | `glm-4.5-air` |
| deep_model | `glm-5-turbo` |
| key_scope | `openai:https://open.bigmodel.cn/api/coding/paas/v4` |

## 已整理端点

| id | 名称 | provider | base_url | quick | deep | 备注 |
|----|------|----------|----------|-------|------|------|
| `openai` | OpenAI | `openai` | `https://api.openai.com/v1` | `gpt-4o-mini` | `gpt-4o` | 官方 OpenAI |
| `zhipu` | Zhipu AI | `openai` | `https://open.bigmodel.cn/api/paas/v4` | `glm-4.5-air` | `glm-4.5` | 标准智谱 |
| `zhipu-coding` | Zhipu Coding Plan | `openai` | `https://open.bigmodel.cn/api/coding/paas/v4` | `glm-4.5-air` | `glm-5-turbo` | 建议本地默认 |
| `deepseek` | DeepSeek | `openai` | `https://api.deepseek.com/v1` | `deepseek-chat` | `deepseek-reasoner` | 余额低时避免默认使用 |
| `moonshot` | Moonshot AI (Kimi) | `openai` | `https://api.moonshot.cn/v1` | `moonshot-v1-8k` | `moonshot-v1-32k` | Kimi |
| `xiaomi-mimo` | Xiaomi MiMo | `openai` | `https://api.xiaomimimo.com/v1` | `xiaomi/mimo-v2-flash` | `xiaomi/mimo-v2-pro` | MiMo 标准端点 |
| `xiaomi-token-plan` | Xiaomi MiMo Token Plan | `openai` | `https://token-plan-cn.xiaomimimo.com/v1` | `mimo-v2.5` | `mimo-v2.5-pro` | Token Plan |
| `dashscope` | Alibaba DashScope | `openai` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` | `qwen-max` | OpenAI 兼容 |
| `siliconflow` | SiliconFlow | `openai` | `https://api.siliconflow.cn/v1` | 自填 | 自填 | 依 route 决定模型名 |
| `anthropic` | Anthropic Claude | `anthropic` | 空 | `claude-haiku-4-5` | `claude-sonnet-4-5` | Anthropic SDK |
| `google` | Google Gemini | `google` | 空 | `gemini-2.5-flash` | `gemini-2.5-pro` | Google SDK |
| `openrouter` | OpenRouter | `openrouter` | `https://openrouter.ai/api/v1` | 自填 | 自填 | 费用随 route |
| `ollama` | Ollama | `ollama` | `http://localhost:11434/v1` | 自填 | 自填 | 本地模型，无托管 API token 成本 |

## 调用路径

- 主分析：`TradingAgentsGraph` 根据 runtime config 创建 quick/deep/mid/ultra LLM。
- 意图解析：`api/main.py` 使用 `quick_think_llm`。
- 报告结构化提取：`api/services/report_service.py` 使用 `quick_think_llm`。
- 设置页 warmup：`/v1/config` 变更模型或手动 `/v1/config/warmup` 时会调用模型做探测。
- VLM 截图识别：`api/services/vlm_service.py` 读取 `TA_VLM_*`，默认智谱视觉模型。

## 避免误耗 token

- 改模型配置时，如果不想自动 warmup，前端或 API 请求传 `warmup=false`。
- 定时任务使用 `docs/SCHEDULER_TOKEN_GUARD.md` 里的开关。
- DeepSeek 余额紧张时，不要把 `backend_url` 设置为 `https://api.deepseek.com/v1` 或 `https://api.deepseek.com`。
