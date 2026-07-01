// [TF-OBS-006] observe_refresh_visual_semantics
import { describe, expect, it } from 'vitest'

import {
    computeObserveVisual,
    formatLastRefreshTime,
    shouldShowNonMarketBanner,
    OBSERVE_LEGEND,
    NEAR_TRIGGER_THRESHOLD_PCT,
    NEAR_INVALIDATION_THRESHOLD_PCT,
} from '@/utils/observeVisuals'

describe('[TF-OBS-006] computeObserveVisual — A-share red/green semantics', () => {
    it('returns unknown tone when prices are missing', () => {
        expect(computeObserveVisual({ currentPrice: null, triggerPrice: 10, invalidPrice: 9 }).tone).toBe('unknown')
        expect(computeObserveVisual({ currentPrice: 10, triggerPrice: null, invalidPrice: 9 }).tone).toBe('unknown')
        expect(computeObserveVisual({ currentPrice: null, triggerPrice: null, invalidPrice: null }).label).toBe('无行情')
    })

    it('colors "triggered" (price >= trigger) red — strong/upside', () => {
        const r = computeObserveVisual({ currentPrice: 10.5, triggerPrice: 10, invalidPrice: 9 })
        expect(r.tone).toBe('triggered')
        expect(r.cls).toMatch(/text-red/)
        expect(r.label).toBe('已触发')
        expect(r.text).toContain('+')
    })

    it('colors "invalidated" (price <= invalid) green — weak/downside', () => {
        const r = computeObserveVisual({ currentPrice: 8.8, triggerPrice: 10, invalidPrice: 9 })
        expect(r.tone).toBe('invalidated')
        expect(r.cls).toMatch(/text-emerald/)
        expect(r.label).toBe('已失效')
    })

    it('colors "near trigger" red when within threshold (strong, approaching)', () => {
        // trigger=10, current=9.9 -> 1% away, within 2% threshold
        const r = computeObserveVisual({ currentPrice: 9.9, triggerPrice: 10, invalidPrice: 8 })
        expect(r.tone).toBe('near_trigger')
        expect(r.cls).toMatch(/text-red/)
        expect(r.label).toBe('接近触发')
    })

    it('respects server near_trigger=true flag even when distance is moderate', () => {
        // trigger=10, current=9.7 -> 3% away, beyond threshold, but server says near
        const r = computeObserveVisual({
            currentPrice: 9.7,
            triggerPrice: 10,
            invalidPrice: 8,
            nearTrigger: true,
        })
        expect(r.tone).toBe('near_trigger')
        expect(r.cls).toMatch(/text-red/)
    })

    it('colors "near invalidation" green when close to invalid price (weak, falling)', () => {
        // invalid=9, current=9.05 -> ~0.5% above invalid, within threshold
        const r = computeObserveVisual({ currentPrice: 9.05, triggerPrice: 10, invalidPrice: 9 })
        expect(r.tone).toBe('near_invalidation')
        expect(r.cls).toMatch(/text-emerald/)
        expect(r.label).toBe('接近失效')
    })

    it('colors neutral "waiting" gray when far from both ends', () => {
        // trigger=10, invalid=8, current=9 -> 10% from trigger, 12.5% from invalid
        const r = computeObserveVisual({ currentPrice: 9, triggerPrice: 10, invalidPrice: 8 })
        expect(r.tone).toBe('waiting')
        expect(r.cls).toMatch(/text-slate/)
        expect(r.label).toBe('等待中')
    })

    it('prefers near_trigger over near_invalidation when both are true (trigger is the actionable side)', () => {
        // Construct a narrow band where both thresholds overlap: trigger very close to invalid.
        // trigger=10, invalid=9.9, current=9.95 -> within 2% of trigger AND within 2% of invalid.
        const r = computeObserveVisual({ currentPrice: 9.95, triggerPrice: 10, invalidPrice: 9.9 })
        expect(r.tone).toBe('near_trigger')
    })

    it('near_trigger threshold boundary is inclusive at exactly the threshold', () => {
        // trigger=10, threshold=2%, current=9.8 -> exactly 2% away
        const r = computeObserveVisual({
            currentPrice: 10 - 10 * (NEAR_TRIGGER_THRESHOLD_PCT / 100),
            triggerPrice: 10,
            invalidPrice: 5,
        })
        expect(r.tone).toBe('near_trigger')
    })

    it('distance just beyond threshold falls back to neutral waiting', () => {
        // trigger=10, current=9.79 -> ~2.1% away, beyond threshold
        const r = computeObserveVisual({ currentPrice: 9.79, triggerPrice: 10, invalidPrice: 5 })
        expect(r.tone).toBe('waiting')
    })

    it('never produces red for downside / invalidated scenarios (no anti-intuitive green-for-up)', () => {
        // Strong cases (triggered + near trigger) must always be red.
        const strong = [
            computeObserveVisual({ currentPrice: 11, triggerPrice: 10, invalidPrice: 9 }),
            computeObserveVisual({ currentPrice: 9.95, triggerPrice: 10, invalidPrice: 8 }),
        ]
        for (const r of strong) {
            expect(r.cls).toMatch(/text-red/)
            expect(r.cls).not.toMatch(/text-emerald/)
        }
        // Weak cases (invalidated + near invalidation) must always be green.
        const weak = [
            computeObserveVisual({ currentPrice: 8.5, triggerPrice: 10, invalidPrice: 9 }),
            computeObserveVisual({ currentPrice: 9.02, triggerPrice: 10, invalidPrice: 9 }),
        ]
        for (const r of weak) {
            expect(r.cls).toMatch(/text-emerald/)
            expect(r.cls).not.toMatch(/text-red/)
        }
    })

    it('triggerDistancePct server hint can promote to near_trigger', () => {
        // current=9.7 (3% away, beyond local threshold) but server hint within 2%
        const r = computeObserveVisual({
            currentPrice: 9.7,
            triggerPrice: 10,
            invalidPrice: 5,
            triggerDistancePct: -1.5,
        })
        expect(r.tone).toBe('near_trigger')
    })
})

describe('[TF-OBS-006] OBSERVE_LEGEND', () => {
    it('exposes all five user-facing tones with reds first and greens last', () => {
        const tones = OBSERVE_LEGEND.map(e => e.tone)
        expect(tones).toEqual(['triggered', 'near_trigger', 'waiting', 'near_invalidation', 'invalidated'])
    })

    it('red tones use red dot, green tones use emerald dot, waiting is slate', () => {
        const map = Object.fromEntries(OBSERVE_LEGEND.map(e => [e.tone, e.dotCls]))
        expect(map.triggered).toMatch(/bg-red/)
        expect(map.near_trigger).toMatch(/bg-red/)
        expect(map.waiting).toMatch(/bg-slate/)
        expect(map.near_invalidation).toMatch(/bg-emerald/)
        expect(map.invalidated).toMatch(/bg-emerald/)
    })

    it('every entry has a non-empty label and description (legend must be self-explanatory)', () => {
        for (const e of OBSERVE_LEGEND) {
            expect(e.label.length).toBeGreaterThan(0)
            expect(e.desc.length).toBeGreaterThan(0)
        }
    })
})

describe('[TF-OBS-006] formatLastRefreshTime', () => {
    it('returns empty string for null/undefined/empty', () => {
        expect(formatLastRefreshTime(null)).toBe('')
        expect(formatLastRefreshTime(undefined)).toBe('')
        expect(formatLastRefreshTime('')).toBe('')
    })

    it('formats an ISO timestamp to HH:MM:SS', () => {
        const out = formatLastRefreshTime('2026-07-01T10:30:45.123Z')
        // exact hour depends on TZ, but format must be HH:MM:SS
        expect(out).toMatch(/^\d{2}:\d{2}:\d{2}$/)
    })

    it('passes through unparseable strings unchanged (fallback)', () => {
        expect(formatLastRefreshTime('not a date')).toBe('not a date')
    })
})

describe('[TF-OBS-006] shouldShowNonMarketBanner', () => {
    it('shows banner on non-trading days', () => {
        expect(shouldShowNonMarketBanner(false, false)).toBe(true)
    })

    it('shows banner during off-market hours on a trading day', () => {
        expect(shouldShowNonMarketBanner(true, false)).toBe(true)
    })

    it('hides banner during live market hours', () => {
        expect(shouldShowNonMarketBanner(true, true)).toBe(false)
    })
})

describe('[TF-OBS-006] threshold constants', () => {
    it('keeps near-trigger threshold conservative (<= 3%)', () => {
        expect(NEAR_TRIGGER_THRESHOLD_PCT).toBeLessThanOrEqual(3)
        expect(NEAR_TRIGGER_THRESHOLD_PCT).toBeGreaterThan(0)
    })

    it('keeps near-invalidation threshold conservative (<= 3%)', () => {
        expect(NEAR_INVALIDATION_THRESHOLD_PCT).toBeLessThanOrEqual(3)
        expect(NEAR_INVALIDATION_THRESHOLD_PCT).toBeGreaterThan(0)
    })
})
