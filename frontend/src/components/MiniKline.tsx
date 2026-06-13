// [TF-UI-011] candidate_research_entry
import { useEffect, useRef, useState } from 'react'
import {
    CandlestickData,
    CandlestickSeries,
    ColorType,
    IChartApi,
    createChart,
} from 'lightweight-charts'
import { Loader2 } from 'lucide-react'
import { api } from '@/services/api'
import type { KlineCandle } from '@/types'

interface MiniKlineProps {
    symbol: string
    height?: number
}

interface CandleWithTime {
    time: { year: number; month: number; day: number }
    open: number
    high: number
    low: number
    close: number
}

function toBusinessDay(value: string): { year: number; month: number; day: number } | null {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value)
    if (!m) return null
    const year = Number(m[1])
    const month = Number(m[2])
    const day = Number(m[3])
    if (!Number.isFinite(year) || !Number.isFinite(month) || !Number.isFinite(day)) return null
    return { year, month, day }
}

export default function MiniKline({ symbol, height = 200 }: MiniKlineProps) {
    const containerRef = useRef<HTMLDivElement>(null)
    const chartRef = useRef<IChartApi | null>(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')
    const [lastClose, setLastClose] = useState<number | null>(null)
    const [changePct, setChangePct] = useState<number | null>(null)

    useEffect(() => {
        let cancelled = false
        setLoading(true)
        setError('')

        api.getKline(symbol)
            .then(res => {
                if (cancelled) return
                const candles: KlineCandle[] = res.candles || []
                if (candles.length === 0) {
                    setError('暂无K线数据')
                    setLoading(false)
                    return
                }

                const candleData: CandleWithTime[] = []
                for (const c of candles) {
                    const bd = toBusinessDay(c.date)
                    if (!bd) continue
                    candleData.push({
                        time: bd,
                        open: c.open,
                        high: c.high,
                        low: c.low,
                        close: c.close,
                    })
                }

                if (candleData.length === 0) {
                    setError('K线数据解析失败')
                    setLoading(false)
                    return
                }

                const last = candles[candles.length - 1]
                const prev = candles.length > 1 ? candles[candles.length - 2] : null
                setLastClose(last.close)
                if (prev && prev.close) {
                    setChangePct(((last.close - prev.close) / prev.close) * 100)
                }

                if (!cancelled && containerRef.current) {
                    setLoading(false)
                    const chart = createChart(containerRef.current, {
                        width: containerRef.current.clientWidth,
                        height,
                        layout: {
                            background: { type: ColorType.Solid, color: 'transparent' },
                            textColor: '#94a3b8',
                            fontSize: 10,
                        },
                        grid: {
                            vertLines: { visible: false },
                            horzLines: { color: 'rgba(148, 163, 184, 0.1)' },
                        },
                        rightPriceScale: {
                            borderVisible: false,
                            scaleMargins: { top: 0.1, bottom: 0.1 },
                        },
                        timeScale: {
                            borderVisible: false,
                            visible: true,
                        },
                        crosshair: { mode: 0 },
                        handleScale: false,
                        handleScroll: false,
                    })
                    chartRef.current = chart

                    const series = chart.addSeries(CandlestickSeries, {
                        upColor: '#ef4444',
                        downColor: '#22c55e',
                        borderUpColor: '#ef4444',
                        borderDownColor: '#22c55e',
                        wickUpColor: '#ef4444',
                        wickDownColor: '#22c55e',
                    })

                    const validData = candleData as unknown as CandlestickData[]
                    series.setData(validData)
                    chart.timeScale().fitContent()
                }
            })
            .catch(() => {
                if (!cancelled) {
                    setError('K线数据获取失败')
                    setLoading(false)
                }
            })

        return () => {
            cancelled = true
            if (chartRef.current) {
                chartRef.current.remove()
                chartRef.current = null
            }
        }
    }, [symbol, height])

    return (
        <div>
            <div className="mb-1 flex items-center justify-between">
                <span className="text-[11px] text-slate-500 dark:text-slate-400">近 120 日 K 线</span>
                {lastClose != null && (
                    <div className="flex items-center gap-2 text-[11px]">
                        <span className="tabular-nums text-slate-600 dark:text-slate-300">
                            最新: {lastClose.toFixed(2)}
                        </span>
                        {changePct != null && (
                            <span className={`tabular-nums font-medium ${changePct >= 0 ? 'text-red-500' : 'text-green-500'}`}>
                                {changePct >= 0 ? '+' : ''}{changePct.toFixed(2)}%
                            </span>
                        )}
                    </div>
                )}
            </div>
            {loading && (
                <div className="flex items-center justify-center" style={{ height }}>
                    <Loader2 className="h-4 w-4 animate-spin text-slate-400" />
                </div>
            )}
            {error && (
                <div className="flex items-center justify-center text-xs text-slate-400" style={{ height }}>
                    {error}
                </div>
            )}
            {!loading && !error && (
                <div ref={containerRef} style={{ height }} />
            )}
        </div>
    )
}
