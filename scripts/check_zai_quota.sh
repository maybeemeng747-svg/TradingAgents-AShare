#!/usr/bin/env bash
# check_zai_quota.sh — 检查智谱 API 额度状态
# 用法: ./check_zai_quota.sh [--json]
#
# 输出:
#   - OK: 额度正常
#   - EXHAUSTED: 额度耗尽，包含下次刷新时间
#   - ERROR: 无法检测（网络错误等）
#
# 检测逻辑:
#   1. 向智谱 API 发送一个最小请求
#   2. 如果返回 429 → 额度耗尽
#   3. 如果返回 200/401 → 额度正常
#   4. 解析响应中的刷新时间信息

set -euo pipefail

JSON_MODE=false
if [[ "${1:-}" == "--json" ]]; then
    JSON_MODE=true
fi

# ── 配置 ──
ZAI_BASE_URL="${ZAI_BASE_URL:-https://open.bigmodel.cn/api/coding/paas/v4}"
ZAI_API_KEY="${ZAI_API_KEY:-${GLM_API_KEY:-}}"

# 如果没有 API key，尝试从 openclaw.json 读取
if [[ -z "$ZAI_API_KEY" ]]; then
    OPENCLAW_CONFIG="$HOME/.openclaw/openclaw.json"
    if [[ -f "$OPENCLAW_CONFIG" ]]; then
        # 尝试提取 zai 相关的 API key
        ZAI_API_KEY=$(python3 -c "
import json
with open('$OPENCLAW_CONFIG') as f:
    cfg = json.load(f)
# 搜索 models.providers 中的 ZAI 配置
providers = cfg.get('models', {}).get('providers', {})
for name, prov in providers.items():
    if name.upper() == 'ZAI':
        key = prov.get('apiKey', '')
        if key:
            print(key)
            break
" 2>/dev/null || echo "")
    fi
fi

if [[ -z "$ZAI_API_KEY" ]]; then
    if $JSON_MODE; then
        echo '{"status":"ERROR","reason":"NO_API_KEY","message":"未找到智谱 API Key"}'
    else
        echo "[QUOTA] ERROR: 未找到智谱 API Key"
    fi
    exit 1
fi

# ── 发送测试请求 ──
RESPONSE_FILE=$(mktemp)
HTTP_CODE=$(curl -s -w "%{http_code}" -o "$RESPONSE_FILE" \
    -X POST "${ZAI_BASE_URL}/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${ZAI_API_KEY}" \
    -d '{
        "model": "glm-4-flash",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1
    }' 2>/dev/null || echo "000")

RESPONSE_BODY=$(cat "$RESPONSE_FILE" 2>/dev/null || echo "{}")
rm -f "$RESPONSE_FILE"

# ── 解析结果 ──
case "$HTTP_CODE" in
    200)
        if $JSON_MODE; then
            echo '{"status":"OK","http_code":200}'
        else
            echo "[QUOTA] OK: 额度正常 (HTTP 200)"
        fi
        exit 0
        ;;
    401)
        if $JSON_MODE; then
            echo '{"status":"OK","http_code":401,"reason":"API key 有效但可能无权限，额度应该正常"}'
        else
            echo "[QUOTA] OK: API key 有效 (HTTP 401，额度应该正常)"
        fi
        exit 0
        ;;
    429)
        # 额度耗尽，尝试解析刷新时间
        RESET_TIME=""
        ERROR_MSG=""
        
        # 尝试从 response body 解析
        if command -v python3 &>/dev/null; then
            PARSED=$(python3 -c "
import json, re, sys
try:
    data = json.loads('''$RESPONSE_BODY''')
    msg = data.get('error', {}).get('message', '') or data.get('message', '') or str(data)
    # 尝试提取时间信息
    time_match = re.search(r'(\d{1,2}:\d{2})', msg)
    reset_time = time_match.group(1) if time_match else ''
    print(json.dumps({'message': msg, 'reset_time': reset_time}))
except:
    print(json.dumps({'message': '$RESPONSE_BODY', 'reset_time': ''}))
" 2>/dev/null || echo '{"message":"解析失败","reset_time":""}')
            ERROR_MSG=$(echo "$PARSED" | python3 -c "import json,sys; print(json.load(sys.stdin).get('message',''))" 2>/dev/null || echo "")
            RESET_TIME=$(echo "$PARSED" | python3 -c "import json,sys; print(json.load(sys.stdin).get('reset_time',''))" 2>/dev/null || echo "")
        fi
        
        if $JSON_MODE; then
            echo "{\"status\":\"EXHAUSTED\",\"http_code\":429,\"reset_time\":\"$RESET_TIME\",\"message\":\"$ERROR_MSG\"}"
        else
            echo "[QUOTA] EXHAUSTED: 额度耗尽 (HTTP 429)"
            if [[ -n "$RESET_TIME" ]]; then
                echo "[QUOTA] 下次刷新: $RESET_TIME"
            fi
            if [[ -n "$ERROR_MSG" ]]; then
                echo "[QUOTA] 详情: $ERROR_MSG"
            fi
        fi
        exit 2
        ;;
    000)
        if $JSON_MODE; then
            echo '{"status":"ERROR","reason":"NETWORK_ERROR","message":"无法连接到智谱 API"}'
        else
            echo "[QUOTA] ERROR: 无法连接到智谱 API"
        fi
        exit 1
        ;;
    *)
        if $JSON_MODE; then
            echo "{\"status\":\"ERROR\",\"http_code\":$HTTP_CODE,\"message\":\"$RESPONSE_BODY\"}"
        else
            echo "[QUOTA] ERROR: 未知状态 HTTP $HTTP_CODE"
            echo "[QUOTA] 响应: $RESPONSE_BODY"
        fi
        exit 1
        ;;
esac
