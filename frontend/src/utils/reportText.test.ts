import { describe, expect, it } from 'vitest'

import { selectReportTextForDiagnostics, splitReportSystemDiagnostics } from './reportText'

const BOUNDARY = '<!-- TA_SYSTEM_DIAGNOSTICS_START -->'

describe('splitReportSystemDiagnostics', () => {
    it('keeps the decision body visible and moves system blocks to diagnostics', () => {
        const main = '最终结论：等待突破。'
        const result = splitReportSystemDiagnostics(
            `${main}\n\n${BOUNDARY}\n⚠️ [C-001] 未持仓，持仓动作不适用。\n\n📋 [C-008] 执行就绪度评分\n- 置信度：中`,
            Array.from(main).length,
        )

        expect(result.main).toBe('最终结论：等待突破。')
        expect(result.diagnostics).toContain('⚠️ [C-001]')
        expect(result.diagnostics).toContain('📋 [C-008]')
    })

    it('recognizes the fundamental semantic gate as a system boundary', () => {
        const main = '研究结论：偏多但暂不入场。'
        const result = splitReportSystemDiagnostics(
            `${main}\n\n${BOUNDARY}\n【基本面语义门禁：NEEDS_REVIEW】\n- DERIVATION_CONFLICT`,
            Array.from(main).length,
        )

        expect(result.main).toBe('研究结论：偏多但暂不入场。')
        expect(result.diagnostics).toContain('DERIVATION_CONFLICT')
    })

    it('moves conclusion-flip audit history out of the visible decision body', () => {
        const main = '最终结论：观望。'
        const result = splitReportSystemDiagnostics(
            `${main}\n\n${BOUNDARY}\n⚠️ [C-005] 同股票结论翻转警告\n上一版结论：建议买入。`,
            Array.from(main).length,
        )

        expect(result.main).toBe('最终结论：观望。')
        expect(result.diagnostics).toContain('上一版结论：建议买入。')
    })

    it('keeps the generated execution summary visible while folding other diagnostics', () => {
        const main = '最终结论：等待突破。'
        const result = splitReportSystemDiagnostics(
            `${main}\n\n${BOUNDARY}\n### 执行质检\n- 数据完整度：80%\n\n### 系统执行结论\n- 系统动作：WAIT/观察\n- 强动作门禁：未通过\n- Buy Level：0\n- Risk Level：1`,
            Array.from(main).length,
        )

        expect(result.main).toContain('最终结论：等待突破。')
        expect(result.main).toContain('### 系统执行结论')
        expect(result.main).toContain('系统动作：WAIT/观察')
        expect(result.diagnostics).toContain('### 执行质检')
        expect(result.diagnostics).not.toContain('### 系统执行结论')
    })

    it('promotes the last generated summary when diagnostics contain stale history', () => {
        const main = '最终结论：观望。'
        const result = splitReportSystemDiagnostics(
            `${main}\n\n${BOUNDARY}\n### 系统执行结论\n- 系统动作：买入\n\n⚠️ [C-005] 历史结论\n\n### 系统执行结论\n- 系统动作：WAIT/观察\n- 强动作门禁：未通过\n- Buy Level：0\n- Risk Level：1`,
            Array.from(main).length,
        )

        expect(result.main).toContain('系统动作：WAIT/观察')
        expect(result.main).not.toContain('系统动作：买入')
        expect(result.diagnostics).toContain('系统动作：买入')
    })

    it('collapses the generated execution-quality section', () => {
        const main = '最终结论：等待突破。'
        const result = splitReportSystemDiagnostics(
            `${main}\n\n${BOUNDARY}\n### 执行质检\n- 系统动作：等待触发\n\n⚠️ [C-001] 未持仓。`,
            Array.from(main).length,
        )

        expect(result.main).toBe('最终结论：等待突破。')
        expect(result.diagnostics).toContain('### 执行质检')
        expect(result.diagnostics).toContain('系统动作：等待触发')
    })

    it('returns legacy reports unchanged when no diagnostic marker exists', () => {
        const result = splitReportSystemDiagnostics('最终结论：观望。')

        expect(result).toEqual({ main: '最终结论：观望。', diagnostics: '' })
    })

    it('does not trust a model-written diagnostic heading', () => {
        const text = '模型引用【基本面语义门禁：NEEDS_REVIEW】后继续给出最终结论：观望。'

        expect(splitReportSystemDiagnostics(text)).toEqual({ main: text, diagnostics: '' })
    })

    it('uses the trusted metadata boundary when delimiter text is copied', () => {
        const main = `模型引用 ${BOUNDARY} 后继续给出最终结论：观望。\n\n`
        const result = splitReportSystemDiagnostics(
            `${main}${BOUNDARY}\n### 系统版本`,
            main.length,
        )

        expect(result.main).toContain('最终结论：观望。')
        expect(result.diagnostics).toBe('### 系统版本')
    })

    it('accepts a trusted boundary before delimiter whitespace', () => {
        const main = '最终结论：观望。'
        const result = splitReportSystemDiagnostics(
            `${main}\n\n${BOUNDARY}\n### 系统版本`,
            Array.from(main).length,
        )

        expect(result).toEqual({ main, diagnostics: '### 系统版本' })
    })

    it('does not trust any delimiter without a trusted offset', () => {
        const text = `最终结论：观望。\n\n${BOUNDARY}\n### 系统版本`

        expect(splitReportSystemDiagnostics(text)).toEqual({
            main: text,
            diagnostics: '',
        })
    })

    it('prefers the trusted offset over a delimiter copied into audit history', () => {
        const main = '最终结论：观望。\n\n'
        const diagnostics = `${BOUNDARY}\n### 执行质检\n上一版包含 ${BOUNDARY}\n### 执行质检`

        const result = splitReportSystemDiagnostics(
            main + diagnostics,
            main.length,
        )

        expect(result.main).toBe('最终结论：观望。')
        expect(result.diagnostics).toContain('上一版包含')
        expect(result.diagnostics).toContain('### 执行质检')
    })

    it('accepts a trusted metadata offset when rendering a delimiter-free report', () => {
        const main = '最终结论：等待突破。'
        const diagnostics = '\n\n⚠️ [C-001] 未持仓。'

        expect(splitReportSystemDiagnostics(main + diagnostics, main.length)).toEqual({
            main,
            diagnostics: '⚠️ [C-001] 未持仓。',
        })
    })

    it('converts a Python code-point offset before slicing emoji text', () => {
        const main = '📊 标题\n📋 证据\n🚀 最终结论：观望。'
        const text = `${main}${BOUNDARY}\n### 系统版本`
        const pythonOffset = Array.from(main).length

        expect(splitReportSystemDiagnostics(text, pythonOffset)).toEqual({
            main,
            diagnostics: '### 系统版本',
        })
    })
})

describe('selectReportTextForDiagnostics', () => {
    it('prefers completed text when its metadata provides a trusted boundary', () => {
        expect(selectReportTextForDiagnostics(
            '短线流式文本\n中线流式文本',
            '最终主周期文本',
            6,
        )).toBe('最终主周期文本')
    })

    it('keeps streamed text before a trusted completed boundary exists', () => {
        expect(selectReportTextForDiagnostics('正在生成', '旧结果')).toBe('正在生成')
    })
})
