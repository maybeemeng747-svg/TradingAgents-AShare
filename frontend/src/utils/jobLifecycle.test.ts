import { describe, expect, it } from 'vitest'
import {
    classifyRecoveredJobStatus,
    DEFAULT_OVERTIME_NOTICE,
    getJobLifecycleUpdate,
    hasRecoveryPollingReachedLimit,
} from './jobLifecycle'

describe('classifyRecoveredJobStatus', () => {
    it('maps completed to completed', () => {
        expect(classifyRecoveredJobStatus('completed')).toBe('completed')
    })

    it('maps pending/running to running', () => {
        expect(classifyRecoveredJobStatus('running')).toBe('running')
        expect(classifyRecoveredJobStatus('pending')).toBe('running')
    })

    it('keeps real failures as failed', () => {
        expect(classifyRecoveredJobStatus('failed', '模型 API Key 无效')).toBe('failed')
        expect(classifyRecoveredJobStatus('failed', null)).toBe('failed')
        expect(classifyRecoveredJobStatus('failed', '')).toBe('failed')
    })

    it('keeps hard-timeout failures as failed', () => {
        expect(
            classifyRecoveredJobStatus('failed', '任务达到硬性运行上限（7200 秒），已终止。请检查模型或数据源的请求超时配置后重试。'),
        ).toBe('failed')
    })

    it('treats legacy soft-timeout failures as still running', () => {
        expect(
            classifyRecoveredJobStatus('failed', '任务超时（超过 1800 秒），已自动终止'),
        ).toBe('running')
        expect(
            classifyRecoveredJobStatus('failed', '任务超时（超过 2700 秒），已自动终止'),
        ).toBe('running')
    })

    it('never rewrites arbitrary timeout-ish errors to running', () => {
        expect(classifyRecoveredJobStatus('failed', 'Request timed out after 1800 ms')).toBe('failed')
        expect(classifyRecoveredJobStatus('failed', '任务超时')).toBe('failed')
    })
})

describe('getJobLifecycleUpdate', () => {
    it('job.running keeps analyzing and clears overtime notice', () => {
        expect(getJobLifecycleUpdate('job.running')).toEqual({
            isAnalyzing: true,
            runState: 'running',
            overtimeNotice: null,
        })
    })

    it('job.overtime stays running with backend message', () => {
        const update = getJobLifecycleUpdate('job.overtime', { message: '分析耗时较长' })
        expect(update).toEqual({
            isAnalyzing: true,
            runState: 'running',
            overtimeNotice: '分析耗时较长',
        })
    })

    it('job.overtime falls back to default notice', () => {
        const update = getJobLifecycleUpdate('job.overtime', {})
        expect(update?.overtimeNotice).toBe(DEFAULT_OVERTIME_NOTICE)
        expect(update?.runState).toBe('running')
    })

    it('job.completed settles the run', () => {
        expect(getJobLifecycleUpdate('job.completed')).toEqual({
            isAnalyzing: false,
            runState: 'completed',
            overtimeNotice: null,
        })
    })

    it('real job.failed settles as failed', () => {
        expect(getJobLifecycleUpdate('job.failed', { error: '数据源不可用' })).toEqual({
            isAnalyzing: false,
            runState: 'failed',
            overtimeNotice: null,
        })
    })

    it('legacy soft-timeout job.failed keeps waiting in background', () => {
        const update = getJobLifecycleUpdate('job.failed', { error: '任务超时（超过 1800 秒），已自动终止' })
        expect(update).toEqual({
            isAnalyzing: true,
            runState: 'running',
            overtimeNotice: DEFAULT_OVERTIME_NOTICE,
        })
    })

    it('returns null for non-lifecycle events', () => {
        expect(getJobLifecycleUpdate('agent.token', {})).toBeNull()
        expect(getJobLifecycleUpdate('ping', {})).toBeNull()
    })
})

describe('hasRecoveryPollingReachedLimit', () => {
    it('is false before the limit and true at/after it', () => {
        expect(hasRecoveryPollingReachedLimit(0, 3)).toBe(false)
        expect(hasRecoveryPollingReachedLimit(2, 3)).toBe(false)
        expect(hasRecoveryPollingReachedLimit(3, 3)).toBe(true)
        expect(hasRecoveryPollingReachedLimit(4, 3)).toBe(true)
    })
})
