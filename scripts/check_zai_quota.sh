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
#   1. 用实际使用的模型 (GLM-5.1) 发送最小请求
#   2. 如果返回 1308 或 429 → 额度耗尽
#   3. 如果返回 200 且响应正常 → 额度正常
#   4. 解析响应中的刷新时间信息

set -euo pipefail

JSON_MODE=false
if [[ "${1:-}" == "--json" ]]; then
    JSON_MODE=true
fi

# ── 配置 ──
ZAI_BASE_URL="${ZAI_BASE_URL:-https://open.bigmodel.cn/api/coding/paas/v4}"
ZAI_API_KEY="${ZAI_API_KEY:-${GLM_API_KEY:-}}"
# 用 GLM-5.1 测试（我们实际使用的模型，受5小时额度限制）
ZAI_TEST_MODEL="${ZAI_TEST_MODEL:-glm-5.1}"

# 如果没有 API key，尝试从 openclaw.json 读取
if [[ -z "$ZAI_API_KEY" ]]; then
    OPENCLAW_CONFIG="$HOME/.openclaw/openclaw.json"
    if [[ -f "$OPENCLAW_CONFIG" ]]; then
        ZAI_API_KEY=$(python3 -c "
import json
with open('$OPENCLAW_CONFIG') as f:
    cfg = json.load(f)
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

# ── 发送测试请求（用实际使用的模型） ──
RESPONSE_FILE=$(mktemp)
HTTP_CODE=$(curl -s -w "%{http_code}" -o "$RESPONSE_FILE" \
    -X POST "${ZAI_BASE_URL}/chat/completions" \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer ${ZAI_API_KEY}" \
    -d "{\"model\": \"${ZAI_TEST_MODEL}\", \"messages\": [{\"role\": \"user\", \"content\": \"say ok\"}], \"max_tokens\": 5}" 2>/dev/null || echo "000")

RESPONSE_BODY=$(cat "$RESPONSE_FILE" 2>/dev/null || echo "{}")
rm -f "$RESPONSE_FILE"

# ── 解析函数 ──
parse_quota_error() {
    local body="$1"
    python3 -c "
import json, re, sys
try:
    data = json.loads('''$body''')
    err = data.get('error', {})
    msg = err.get('message', '') or data.get('message', '') or str(data)
    code = str(err.get('code', ''))
    # 提取时间：2026-05-29 21:49:56 或 21:49
    time_match = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', msg)
    if not time_match:
        time_match = re.search(r'(\d{1,2}:\d{2})', msg)
    reset_time = time_match.group(1) if time_match else ''
    print(json.dumps({'message': msg, 'reset_time': reset_time, 'code': code}))
except:
    print(json.dumps({'message': '', 'reset_time': '', 'code': ''}))
" 2>/dev/null || echo '{"message":"","reset_time":"","code":""}'
}

# ── 解析结果 ──
case "$HTTP_CODE" in
    200)
        # 检查响应体是否包含额度错误（有时 HTTP 200 但 body 里有错误）
        if echo "$RESPONSE_BODY" | grep -q '"code".*1308\|已达.*上限\|exhausted\|5 小时' 2>/dev/null; then
            PARSED=$(parse_quota_error "$RESPONSE_BODY")
            ERROR_MSG=$(echo "$PARSED" | python3 -c "import json,sys; print(json.load(sys.stdin).get('message',''))" 2>/dev/null || echo "")
            RESET_TIME=$(echo "$PARSED" | python3 -c "import json,sys; print(json.load(sys.stdin).get('reset_time',''))" 2>/dev/null || echo "")
            ERROR_CODE=$(echo "$PARSED" | python3 -c "import json,sys; print(json.load(sys.stdin).get('code','1308'))" 2>/dev/null || echo "1308")

            if $JSON_MODE; then
                echo "{\"status\":\"EXHAUSTED\",\"error_code\":\"$ERROR_CODE\",\"reset_time\":\"$RESET_TIME\",\"message\":\"$ERROR_MSG\"}"
            else
                echo "[QUOTA] EXHAUSTED: 智谱 API 额度耗尽 (错误码 $ERROR_CODE)"
                [[ -n "$RESET_TIME" ]] && echo "[QUOTA] 下次刷新: $RESET_TIME"
                [[ -n "$ERROR_MSG" ]] && echo "[QUOTA] 详情: $ERROR_MSG"
            fi
            exit 2
        fi
        # 真正的 200 OK
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
    429|1308)
        # 额度耗尽
        PARSED=$(parse_quota_error "$RESPONSE_BODY")
        ERROR_MSG=$(echo "$PARSED" | python3 -c "import json,sys; print(json.load(sys.stdin).get('message',''))" 2>/dev/null || echo "")
        RESET_TIME=$(echo "$PARSED" | python3 -c "import json,sys; print(json.load(sys.stdin).get('reset_time',''))" 2>/dev/null || echo "")
        ERROR_CODE=$(echo "$PARSED" | python3 -c "import json,sys; print(json.load(sys.stdin).get('code',''))" 2>/dev/null || echo "")

        if $JSON_MODE; then
            echo "{\"status\":\"EXHAUSTED\",\"error_code\":\"${ERROR_CODE:-$HTTP_CODE}\",\"reset_time\":\"$RESET_TIME\",\"message\":\"$ERROR_MSG\"}"
        else
            echo "[QUOTA] EXHAUSTED: 智谱 API 额度耗尽 (错误码 ${ERROR_CODE:-$HTTP_CODE})"
            [[ -n "$RESET_TIME" ]] && echo "[QUOTA] 下次刷新: $RESET_TIME"
            [[ -n "$ERROR_MSG" ]] && echo "[QUOTA] 详情: $ERROR_MSG"
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
        # 检查响应体是否包含额度错误
        if echo "$RESPONSE_BODY" | grep -q '1308\|已达.*上限\|5 小时' 2>/dev/null; then
            PARSED=$(parse_quota_error "$RESPONSE_BODY")
            ERROR_MSG=$(echo "$PARSED" | python3 -c "import json,sys; print(json.load(sys.stdin).get('message',''))" 2>/dev/null || echo "")
            RESET_TIME=$(echo "$PARSED" | python3 -c "import json,sys; print(json.load(sys.stdin).get('reset_time',''))" 2>/dev/null || echo "")

            if $JSON_MODE; then
                echo "{\"status\":\"EXHAUSTED\",\"error_code\":\"1308\",\"reset_time\":\"$RESET_TIME\",\"message\":\"$ERROR_MSG\"}"
            else
                echo "[QUOTA] EXHAUSTED: 智谱 API 额度耗尽"
                [[ -n "$RESET_TIME" ]] && echo "[QUOTA] 下次刷新: $RESET_TIME"
                [[ -n "$ERROR_MSG" ]] && echo "[QUOTA] 详情: $ERROR_MSG"
            fi
            exit 2
        fi
        if $JSON_MODE; then
            echo "{\"status\":\"ERROR\",\"http_code\":$HTTP_CODE,\"message\":\"$RESPONSE_BODY\"}"
        else
            echo "[QUOTA] ERROR: 未知状态 HTTP $HTTP_CODE"
            echo "[QUOTA] 响应: $RESPONSE_BODY"
        fi
        exit 1
        ;;
esac
