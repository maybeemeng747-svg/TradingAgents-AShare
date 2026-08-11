import type { AnalysisReport } from '@/types'

export function shouldSuppressTargetPrice(
    report?: Pick<AnalysisReport, 'decision' | 'direction' | 'execution_action' | 'research_direction' | 'action_label'> | null,
): boolean {
    const action = (report?.execution_action || '').toUpperCase()
    const decision = (report?.decision || '').toUpperCase()
    const direction = report?.research_direction || report?.direction || ''
    const actionLabel = report?.action_label || ''
    if (action && action !== 'WAIT') return false
    if (
        !action
        && (
            actionLabel.includes('减仓')
            || actionLabel.includes('清仓')
            || actionLabel.includes('退出')
            || ['SELL', 'REDUCE', 'EXIT'].includes(decision)
        )
    ) return false
    if (actionLabel.includes('回避') || actionLabel.includes('禁止买入')) return true
    return direction.includes('偏空') || direction.includes('看空')
}
