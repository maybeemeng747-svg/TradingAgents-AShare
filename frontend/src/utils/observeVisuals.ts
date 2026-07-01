// [TF-OBS-006] observe_refresh_visual_semantics
// Pure helpers for the 盘中观察 (intraday observe) tab visual semantics.
//
// A-share color convention (RED = up / strong, GREEN = down / weak):
//   - Triggered / near trigger (price rising toward trigger)  → RED family
//   - Invalidated / near invalidation (price falling)          → GREEN family
//   - Neutral waiting / no quote                               → GRAY
//
// Extracted from TradeFlow.tsx so the semantics can be unit-tested without
// pulling in React/components, following the tradeflowFocus.ts pattern.

export type ObserveVisualTone =
    | 'triggered'
    | 'near_trigger'
    | 'waiting'
    | 'near_invalidation'
    | 'invalidated'
    | 'unknown'

export interface ObserveVisualResult {
    tone: ObserveVisualTone
    /** Distance / status text shown in the cell, e.g. "还差 1.20%" / "已触发 / +2.10%". */
    text: string
    /** Tailwind text-color classes (light + dark). */
    cls: string
    /** Short label used by the legend. */
    label: string
}

export interface ComputeObserveVisualInput {
    currentPrice: number | null
    triggerPrice: number | null
    invalidPrice: number | null
    /** Server-side "near trigger" flag (TF-OBS-004). When true, force near_trigger tone. */
    nearTrigger?: boolean
    /** Signed pct distance to trigger from server (optional, used only as a hint). */
    triggerDistancePct?: number | null
}

/**
 * Distance to trigger (positive pct) below which we treat the candidate as
 * "approaching trigger" (strong → red). Kept conservative so we do not over-promise.
 */
export const NEAR_TRIGGER_THRESHOLD_PCT = 2.0

/**
 * Distance to invalidation (positive pct above the invalid price) below which
 * we treat the candidate as "approaching invalidation" (weak → green).
 */
export const NEAR_INVALIDATION_THRESHOLD_PCT = 2.0

/**
 * Compute the A-share-compliant visual tone for an observe row.
 *
 * Rules (RED = strong/approaching trigger, GREEN = weak/approaching invalid):
 *   1. No trigger or current price  → unknown (gray, "无行情")
 *   2. currentPrice >= triggerPrice → triggered (red)
 *   3. currentPrice <= invalidPrice → invalidated (green)
 *   4. near trigger (server flag OR within threshold) → near_trigger (red)
 *   5. near invalidation (within threshold of invalid price) → near_invalidation (green)
 *   6. otherwise → waiting (gray)
 */
export function computeObserveVisual(input: ComputeObserveVisualInput): ObserveVisualResult {
    const { currentPrice, triggerPrice, invalidPrice, nearTrigger, triggerDistancePct } = input

    if (!triggerPrice || !currentPrice) {
        return { tone: 'unknown', text: '-', cls: 'text-slate-400', label: '无行情' }
    }

    const distPct = ((currentPrice - triggerPrice) / triggerPrice) * 100

    // 2. Triggered — price already at/above trigger (upside break, strong)
    if (currentPrice >= triggerPrice) {
        return {
            tone: 'triggered',
            text: `已触发 / +${distPct.toFixed(2)}%`,
            cls: 'text-red-600 dark:text-red-400 font-medium',
            label: '已触发',
        }
    }

    // 3. Invalidated — price at/below invalid (downside break, weak)
    if (invalidPrice && currentPrice <= invalidPrice) {
        const belowPct = ((invalidPrice - currentPrice) / invalidPrice) * 100
        return {
            tone: 'invalidated',
            text: `已失效 / -${belowPct.toFixed(2)}%`,
            cls: 'text-emerald-600 dark:text-emerald-400',
            label: '已失效',
        }
    }

    // In-between: differentiate "approaching trigger" (strong/red) vs
    // "approaching invalidation" (weak/green) vs neutral waiting (gray).
    const toTrigger = ((triggerPrice - currentPrice) / triggerPrice) * 100

    // 4. Near trigger — server flag OR within threshold (and server hint agrees)
    const serverHintsNear = triggerDistancePct != null
        ? Math.abs(triggerDistancePct) <= NEAR_TRIGGER_THRESHOLD_PCT
        : false
    const isNearTrigger = Boolean(nearTrigger) || toTrigger <= NEAR_TRIGGER_THRESHOLD_PCT || serverHintsNear

    // 5. Near invalidation — close to invalid price (price falling)
    let toInvalidationPct: number | null = null
    if (invalidPrice && invalidPrice > 0) {
        toInvalidationPct = ((currentPrice - invalidPrice) / invalidPrice) * 100
    }
    const isNearInvalidation = toInvalidationPct != null && toInvalidationPct <= NEAR_INVALIDATION_THRESHOLD_PCT

    if (isNearTrigger) {
        return {
            tone: 'near_trigger',
            text: `还差 ${toTrigger.toFixed(2)}%`,
            cls: 'text-red-600 dark:text-red-400 font-medium',
            label: '接近触发',
        }
    }
    if (isNearInvalidation) {
        return {
            tone: 'near_invalidation',
            text: `距失效 ${toInvalidationPct!.toFixed(2)}%`,
            cls: 'text-emerald-600 dark:text-emerald-400',
            label: '接近失效',
        }
    }

    // 6. Neutral waiting
    return {
        tone: 'waiting',
        text: `还差 ${toTrigger.toFixed(2)}%`,
        cls: 'text-slate-500 dark:text-slate-400',
        label: '等待中',
    }
}

export interface ObserveLegendEntry {
    tone: ObserveVisualTone
    label: string
    /** Dot swatch class (bg-*) to render the legend marker. */
    dotCls: string
    desc: string
}

/**
 * Legend entries explaining the A-share red/green semantics used in the observe tab.
 * Order matters: reds first (strong), then neutral, then greens (weak), then unknown.
 */
export const OBSERVE_LEGEND: ObserveLegendEntry[] = [
    { tone: 'triggered', label: '已触发', dotCls: 'bg-red-500', desc: '价格已达/过触发价（强势）' },
    { tone: 'near_trigger', label: '接近触发', dotCls: 'bg-red-400', desc: '价格接近触发价，重点关注' },
    { tone: 'waiting', label: '等待中', dotCls: 'bg-slate-400', desc: '价格在区间内，未接近任一端' },
    { tone: 'near_invalidation', label: '接近失效', dotCls: 'bg-emerald-500', desc: '价格接近失效价，走弱' },
    { tone: 'invalidated', label: '已失效', dotCls: 'bg-emerald-600', desc: '价格已跌破失效价' },
]

/**
 * Format an ISO timestamp / arbitrary string into a short "最近刷新 HH:MM:SS" label.
 * Returns '' when the input is empty so callers can render without extra guards.
 *
 * Accepts either an ISO string (from server `last_observed_at`) or an already-formatted
 * string; when parsing fails the original value is returned unchanged.
 */
export function formatLastRefreshTime(raw: string | null | undefined): string {
    if (!raw) return ''
    const d = new Date(raw)
    if (isNaN(d.getTime())) return raw
    const pad = (n: number) => n.toString().padStart(2, '0')
    return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

/**
 * Whether the non-market-hours banner should be shown.
 * Centralized so the banner and the empty-state wording stay consistent.
 *
 * The decision depends only on the trading-session flags: the banner is shown
 * on non-trading days and outside market hours. The caller chooses the wording
 * (review-last-snapshot vs wait-for-session) based on whether items exist.
 */
export function shouldShowNonMarketBanner(
    isTradingDay: boolean,
    isMarketHours: boolean,
): boolean {
    return !isTradingDay || !isMarketHours
}
