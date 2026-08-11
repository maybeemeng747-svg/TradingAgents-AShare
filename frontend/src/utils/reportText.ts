export function detectDecisionLabel(text?: string | null): string | null {
    if (!text) return null
    const normalized = text.toLowerCase()
    if (normalized.includes('增持')) return '增持'
    if (normalized.includes('减持')) return '减持'
    if (normalized.includes('buy') || normalized.includes('买入')) return '买入'
    if (normalized.includes('sell') || normalized.includes('卖出')) return '卖出'
    if (normalized.includes('watch') || normalized.includes('观望')) return '观望'
    if (normalized.includes('hold') || normalized.includes('持有')) return '持有'
    return null
}

export function sanitizeReportMarkdown(text?: string | null): string {
    if (!text) return ''
    return text
        .replace(/<!--\s*VERDICT:\s*\{[\s\S]*?\}\s*-->/gi, '') // strip machine-readable verdict tag
        .replace(/<!--\s*DEBATE_STATE:\s*\{[\s\S]*?\}\s*-->/gi, '')
        .replace(/<!--\s*RISK_JUDGE:\s*\{[\s\S]*?\}\s*-->/gi, '')
        .replace(/FINAL TRANSACTION PROPOSAL:\s*\**\s*BUY\s*\**/gi, '最终交易建议：买入')
        .replace(/FINAL TRANSACTION PROPOSAL:\s*\**\s*SELL\s*\**/gi, '最终交易建议：卖出')
        .replace(/FINAL TRANSACTION PROPOSAL:\s*\**\s*HOLD\s*\**/gi, '最终交易建议：观望')
        .replace(/FINAL VERDICT:\s*/gi, '最终裁决：')
        .replace(/HOLD with Conditional Trigger/gi, '观望（条件触发）')
        .replace(/BUY with Conditional Trigger/gi, '买入（条件触发）')
        .replace(/SELL with Conditional Trigger/gi, '卖出（条件触发）')
}

const SYSTEM_DIAGNOSTICS_START = '<!-- TA_SYSTEM_DIAGNOSTICS_START -->'

export interface ReportTextParts {
    main: string
    diagnostics: string
}

const GENERATED_EXECUTION_SUMMARY = /(?:^|\n)#{1,6}\s*系统执行结论\s*\n(?:\s*-\s*[^\n]*(?:\n|$)){1,8}/g

export function selectReportTextForDiagnostics(
    streamedText?: string | null,
    completedText?: string | null,
    trustedBoundary?: number | null,
): string {
    if (Number.isInteger(trustedBoundary) && completedText) return completedText
    return streamedText || completedText || ''
}

/**
 * Keep the human decision readable while preserving the full audit trail.
 * The backend appends machine/system diagnostics after a trusted delimiter and
 * records the raw-text offset in metadata. Never infer this boundary from a
 * model-written heading: headings are ordinary, untrusted report text.
 */
export function splitReportSystemDiagnostics(
    text?: string | null,
    trustedBoundary?: number | null,
): ReportTextParts {
    const raw = text || ''
    const codePoints = Array.from(raw)
    const hasTrustedOffset = Number.isInteger(trustedBoundary)
        && (trustedBoundary as number) >= 0
        && (trustedBoundary as number) <= codePoints.length
    const trustedCodeUnitBoundary = hasTrustedOffset
        ? codePoints.slice(0, trustedBoundary as number).join('').length
        : null
    if (!hasTrustedOffset) {
        return { main: sanitizeReportMarkdown(raw).trim(), diagnostics: '' }
    }
    const boundary = trustedCodeUnitBoundary as number
    const trustedContentStart = boundary + (raw.slice(boundary).match(/^\s*/)?.[0].length || 0)
    const diagnosticsStart = raw.startsWith(SYSTEM_DIAGNOSTICS_START, trustedContentStart)
        ? trustedContentStart + SYSTEM_DIAGNOSTICS_START.length
        : boundary
    const rawDiagnostics = raw.slice(diagnosticsStart)
    const summaryMatches = Array.from(rawDiagnostics.matchAll(GENERATED_EXECUTION_SUMMARY))
    const summaryMatch = summaryMatches[summaryMatches.length - 1]
    const visibleSummary = summaryMatch ? sanitizeReportMarkdown(summaryMatch[0]).trim() : ''
    const remainingDiagnostics = summaryMatch
        ? rawDiagnostics.slice(0, summaryMatch.index)
            + rawDiagnostics.slice((summaryMatch.index || 0) + summaryMatch[0].length)
        : rawDiagnostics
    const main = sanitizeReportMarkdown(raw.slice(0, boundary)).trim()
    return {
        main: [main, visibleSummary].filter(Boolean).join('\n\n'),
        diagnostics: sanitizeReportMarkdown(remainingDiagnostics).trim(),
    }
}

export function buildAgentSummary(text?: string | null): string {
    const cleaned = sanitizeReportMarkdown(text)
        .replace(/^#+\s*/gm, '')
        .replace(/\*\*/g, '')
        .replace(/\|/g, ' ')
        .replace(/\s+/g, ' ')
        .trim()
    const decision = detectDecisionLabel(cleaned)
    if (decision) return decision
    if (/偏多|看多|上涨|突破/.test(cleaned)) return '偏多'
    if (/偏空|看空|下跌|回撤/.test(cleaned)) return '偏空'
    if (/中性|震荡/.test(cleaned)) return '中性'
    if (cleaned.includes('风险')) return '风控结论'
    if (cleaned.includes('计划')) return '计划已生成'
    return cleaned.slice(0, 18) || '报告已生成'
}

export interface Verdict {
    direction: string
    reason: string
}

// Map English direction values (en.py prompts) to Chinese display labels
const DIRECTION_ALIAS: Record<string, string> = {
    BULLISH:       '看多',
    LEAN_BULLISH:  '偏多',
    BEARISH:       '看空',
    LEAN_BEARISH:  '偏空',
    NEUTRAL:       '中性',
    CAUTIOUS:      '谨慎',  // 向后兼容旧报告
}

/**
 * Extract the structured verdict embedded by the agent as an HTML comment.
 * Format: <!-- VERDICT: {"direction": "...", "reason": "..."} -->
 */
export function extractVerdict(text?: string | null): Verdict | null {
    if (!text) return null
    const m = text.match(/<!--\s*VERDICT:\s*(\{[^>]+\})\s*-->/)
    if (!m) return null
    try {
        const parsed = JSON.parse(m[1]) as { direction?: string; reason?: string }
        if (!parsed.direction || !parsed.reason) return null
        const direction = DIRECTION_ALIAS[parsed.direction.toUpperCase()] ?? parsed.direction
        return { direction, reason: parsed.reason.trim().slice(0, 42) }
    } catch {
        return null
    }
}
