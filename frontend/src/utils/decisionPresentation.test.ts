import { describe, expect, it } from 'vitest'
import { shouldSuppressTargetPrice } from './decisionPresentation'

describe('shouldSuppressTargetPrice', () => {
    it('suppresses bearish WAIT targets', () => {
        expect(shouldSuppressTargetPrice({
            execution_action: 'WAIT',
            research_direction: '偏空',
            action_label: '回避',
        })).toBe(true)
    })

    it('suppresses explicit no-entry labels', () => {
        expect(shouldSuppressTargetPrice({
            execution_action: 'WAIT',
            research_direction: '中性',
            action_label: '禁止买入',
        })).toBe(true)
    })

    it('suppresses legacy avoid targets without an execution action', () => {
        expect(shouldSuppressTargetPrice({
            execution_action: undefined,
            research_direction: '偏空',
            action_label: '回避',
        })).toBe(true)
    })

    it('suppresses legacy bearish direction targets', () => {
        expect(shouldSuppressTargetPrice({
            direction: '偏空',
        })).toBe(true)
    })

    it('keeps legacy reduce targets without an execution action', () => {
        expect(shouldSuppressTargetPrice({
            decision: 'SELL',
            execution_action: undefined,
            research_direction: '偏空',
            action_label: '条件减仓',
        })).toBe(false)
    })

    it('keeps explicit exit targets even when a stale label says avoid', () => {
        expect(shouldSuppressTargetPrice({
            decision: 'SELL',
            execution_action: 'EXIT',
            research_direction: '偏空',
            action_label: '回避',
        })).toBe(false)
    })

    it('keeps targets for conditional entry and bullish waiting', () => {
        expect(shouldSuppressTargetPrice({
            execution_action: 'ENTER',
            research_direction: '偏多',
            action_label: '条件入场',
        })).toBe(false)
        expect(shouldSuppressTargetPrice({
            execution_action: 'WAIT',
            research_direction: '偏多',
            action_label: '等待触发',
        })).toBe(false)
    })
})
