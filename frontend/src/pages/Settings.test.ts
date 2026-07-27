import { describe, expect, it } from 'vitest'

import { inferPreset } from './Settings'


describe('[B-001-R1] inferPreset MiMo migration', () => {
    it.each([
        ['https://api.xiaomimimo.com/v1', 'xiaomi-mimo'],
        ['https://api.xiaomimimo.com/v1/', 'xiaomi-mimo'],
        ['https://token-plan-cn.xiaomimimo.com/v1', 'xiaomi-token-plan'],
        ['https://token-plan-cn.xiaomimimo.com/v1/', 'xiaomi-token-plan'],
    ])('maps legacy openai config %s to %s', (backendUrl, expected) => {
        expect(inferPreset('openai', backendUrl)).toBe(expected)
    })

    it('keeps a non-MiMo OpenAI-compatible URL custom', () => {
        expect(inferPreset('openai', 'https://example.com/v1')).toBe('custom-openai')
    })

    it('keeps current MiMo configs on their preset', () => {
        expect(inferPreset('mimo', 'https://token-plan-cn.xiaomimimo.com/v1'))
            .toBe('xiaomi-token-plan')
    })
})
