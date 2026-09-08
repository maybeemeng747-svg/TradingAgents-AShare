// [UPSTREAM-081-002] 长时分析软/硬超时与断线恢复的纯函数工具。
// 软超时（job.overtime）是非终态事件：任务仍在后台运行，最终只会产生一次
// completed/failed。这里集中维护“事件 → 运行状态”的映射与历史
// failed(timeout) 记录的归类，禁止把任意 failed 无条件当作 running。

export type AnalysisRunState = 'idle' | 'running' | 'completed' | 'failed'

export interface JobLifecycleUpdate {
    isAnalyzing: boolean
    runState: AnalysisRunState
    overtimeNotice: string | null
}

export type RecoveredJobDisposition = 'running' | 'completed' | 'failed'

export const DEFAULT_OVERTIME_NOTICE = '分析耗时较长，后台仍在继续，正在等待最终结果，请勿重复提交。'
export const RECOVERY_POLL_INTERVAL_MS = 3000
// 断线恢复最多轮询 2 小时（与后端硬超时上限同量级），超限后停止等待。
export const RECOVERY_POLL_MAX_ATTEMPTS = 2 * 60 * 60 / (RECOVERY_POLL_INTERVAL_MS / 1000)
export const RECOVERY_POLL_TIMEOUT_MESSAGE = '已停止等待任务状态。后端任务可能仍在处理，请稍后到历史报告查看最终结果。'
// 历史失败文案：旧版外层看门狗把软超时标成 failed（“任务超时（超过 N 秒），
// 已自动终止”），但内层分析可能仍在运行并最终落盘报告。
export const LEGACY_SOFT_TIMEOUT_PATTERN = /任务超时（超过\s*\d+\s*秒）/

export function hasRecoveryPollingReachedLimit(
    attempts: number,
    maxAttempts: number = RECOVERY_POLL_MAX_ATTEMPTS,
): boolean {
    return attempts >= maxAttempts
}

export function getJobLifecycleUpdate(
    eventName: string,
    data: Record<string, unknown> = {},
): JobLifecycleUpdate | null {
    switch (eventName) {
        case 'job.running':
            return { isAnalyzing: true, runState: 'running', overtimeNotice: null }
        case 'job.overtime': {
            const suppliedMessage = data.message ?? data.msg
            return {
                isAnalyzing: true,
                runState: 'running',
                overtimeNotice: typeof suppliedMessage === 'string' && suppliedMessage.trim()
                    ? suppliedMessage
                    : DEFAULT_OVERTIME_NOTICE,
            }
        }
        case 'job.completed':
            return { isAnalyzing: false, runState: 'completed', overtimeNotice: null }
        case 'job.failed':
            if (classifyRecoveredJobStatus('failed', typeof data.error === 'string' ? data.error : null) === 'running') {
                // 历史软超时文案不代表终态：保持“后台继续”，等待真正的完成事件。
                return {
                    isAnalyzing: true,
                    runState: 'running',
                    overtimeNotice: DEFAULT_OVERTIME_NOTICE,
                }
            }
            return { isAnalyzing: false, runState: 'failed', overtimeNotice: null }
        default:
            return null
    }
}

export function classifyRecoveredJobStatus(status: string, error?: string | null): RecoveredJobDisposition {
    if (status === 'completed') return 'completed'
    if (status !== 'failed') return 'running'

    // 旧策略把软超时写成 failed(timeout)，但内层工作流可能仍在运行并能落盘
    // completed 报告。只有匹配该历史文案时才当作 running 继续等待；
    // 硬超时（“硬性运行上限”）与真实失败必须保持 failed，不做无条件改写。
    if (LEGACY_SOFT_TIMEOUT_PATTERN.test(error || '')) return 'running'
    return 'failed'
}
