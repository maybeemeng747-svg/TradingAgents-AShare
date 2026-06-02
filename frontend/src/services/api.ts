import type { AnalysisRequest, AnalysisResponse, Announcement, AuthUser, AuthVerifyResponse, JobStatus, AnalysisReport, KlineResponse, LatestAnnouncementResponse, PortfolioImportState, PortfolioOverviewResponse, PortfolioPositionInput, Report, ReportDetail, ReportListResponse, RuntimeConfig, RuntimeConfigUpdate, RuntimeConfigUpdateResponse, RuntimeWarmupRequest, RuntimeWarmupResponse, WatchlistItem, WatchlistBatchResponse, WatchlistTableParseResponse, ScheduledAnalysis, ScheduledBatchTriggerResponse, StockSearchResult, TrackingBoardResponse, UserToken, UserTokenCreateRequest, WecomWarmupRequest, WecomWarmupResponse, BarkWarmupRequest, BarkWarmupResponse, FeedbackItem, FeedbackListResponse, FeedbackUnreadResponse, TradeFlowDailyPlanResponse, TradeFlowCandidatesResponse, TradeFlowCandidateDetailResponse, TradeFlowObserveResponse, TradeFlowTAQueueResponse, TradeFlowReviewResponse, TradeFlowDataHealthResponse, TradeFlowDiscoveryRequest, TradeFlowDiscoveryResponse, TradeFlowFilteredResponse, TradeFlowObserveRunResponse } from '@/types'

export function getBaseUrl(): string {
    const envUrl = (import.meta.env.VITE_API_URL as string) || ''
    if (envUrl) return envUrl.replace(/\/$/, '')
    if (typeof window !== 'undefined' && window.location?.origin) {
        return window.location.origin.replace(/\/$/, '')
    }
    return 'http://localhost:8000'
}


function getAuthToken(): string | null {
    try {
        return localStorage.getItem('ta-access-token')
    } catch {
        return null
    }
}

class ApiService {
    private async request<T>(endpoint: string, options?: RequestInit): Promise<T> {
        const url = `${getBaseUrl()}${endpoint}`
        const token = getAuthToken()
        const response = await fetch(url, {
            ...options,
            headers: {
                'Content-Type': 'application/json',
                ...(token ? { Authorization: `Bearer ${token}` } : {}),
                ...options?.headers,
            },
        })

        if (!response.ok) {
            const contentType = response.headers.get('content-type') || ''
            if (contentType.includes('application/json')) {
                const data = await response.json().catch(() => null)
                const detail = data?.detail || data?.message
                throw new Error(detail || `HTTP error! status: ${response.status}`)
            }
            const error = await response.text()
            throw new Error(error || `HTTP error! status: ${response.status}`)
        }

        if (response.status === 204 || response.status === 205) {
            return undefined as T
        }

        const contentType = response.headers.get('content-type') || ''
        if (!contentType.includes('application/json')) {
            const text = await response.text()
            return (text ? (text as T) : undefined) as T
        }

        const raw = await response.text()
        if (!raw) {
            return undefined as T
        }

        return JSON.parse(raw) as T
    }

    async startAnalysis(request: AnalysisRequest): Promise<AnalysisResponse> {
        return this.request<AnalysisResponse>('/v1/analyze', {
            method: 'POST',
            body: JSON.stringify(request),
        })
    }

    async getJobStatus(jobId: string): Promise<JobStatus> {
        return this.request<JobStatus>(`/v1/jobs/${jobId}`)
    }

    async getJobResult(jobId: string): Promise<{ job_id: string; status: string; decision: string; result: AnalysisReport }> {
        return this.request(`/v1/jobs/${jobId}/result`)
    }

    async getKline(symbol: string, startDate?: string, endDate?: string): Promise<KlineResponse> {
        const params = new URLSearchParams({ symbol })
        if (startDate) params.append('start_date', startDate)
        if (endDate) params.append('end_date', endDate)
        return this.request<KlineResponse>(`/v1/market/kline?${params}`)
    }

    async chatCompletion(
        messages: Array<{ role: string; content: string }>,
        stream = true,
        selectedAnalysts?: string[],
        signal?: AbortSignal,
        userContext?: {
            objective?: string
            investment_horizon?: string
            current_position?: number
            current_position_pct?: number
            average_cost?: number
        },
    ) {
        const body: Record<string, unknown> = {
            messages,
            stream,
            selected_analysts: selectedAnalysts,
        }
        // [TA-UI-001] analysis_console_horizon_intent
        if (userContext) {
            if (userContext.objective) body.objective = userContext.objective
            if (userContext.investment_horizon) body.investment_horizon = userContext.investment_horizon
            if (userContext.current_position != null) body.current_position = userContext.current_position
            if (userContext.current_position_pct != null) body.current_position_pct = userContext.current_position_pct
            if (userContext.average_cost != null) body.average_cost = userContext.average_cost
        }
        const response = await fetch(`${getBaseUrl()}/v1/chat/completions`, {
            method: 'POST',
            signal,
            headers: {
                'Content-Type': 'application/json',
                ...(getAuthToken() ? { Authorization: `Bearer ${getAuthToken()}` } : {}),
            },
            body: JSON.stringify(body),
        })

        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`)
        }

        return response
    }

    // Report API Methods
    async getReports(symbol?: string, skip = 0, limit = 100): Promise<ReportListResponse> {
        const params = new URLSearchParams()
        if (symbol) params.append('symbol', symbol)
        params.append('skip', skip.toString())
        params.append('limit', limit.toString())
        return this.request<ReportListResponse>(`/v1/reports?${params}`)
    }

    async getLatestReportsBySymbols(symbols: string[]): Promise<{ reports: Report[] }> {
        return this.request<{ reports: Report[] }>('/v1/reports/latest-by-symbols', {
            method: 'POST',
            body: JSON.stringify({ symbols }),
        })
    }

    async getReport(reportId: string): Promise<ReportDetail> {
        return this.request<ReportDetail>(`/v1/reports/${reportId}`)
    }

    async getLatestAnnouncement(): Promise<Announcement | null> {
        const data = await this.request<LatestAnnouncementResponse>('/v1/announcements/latest')
        return data.announcement
    }

    async deleteReport(reportId: string): Promise<{ message: string }> {
        return this.request<{ message: string }>(`/v1/reports/${reportId}`, {
            method: 'DELETE',
        })
    }


    async createReport(report: {
        symbol: string
        trade_date: string
        decision?: string
        result_data?: AnalysisReport
    }): Promise<Report> {
        return this.request<Report>('/v1/reports', {
            method: 'POST',
            body: JSON.stringify(report),
        })
    }

    // Watchlist
    async getWatchlist(): Promise<{ items: WatchlistItem[] }> {
        return this.request<{ items: WatchlistItem[] }>('/v1/watchlist')
    }
    async addToWatchlist(input: string): Promise<WatchlistBatchResponse> {
        return this.request<WatchlistBatchResponse>('/v1/watchlist', {
            method: 'POST',
            body: JSON.stringify({ text: input }),
        })
    }
    async removeFromWatchlist(id: string): Promise<void> {
        await this.request('/v1/watchlist/' + id, { method: 'DELETE' })
    }
    async updateWatchlistNotes(id: string, notes: string): Promise<WatchlistItem> {
        return this.request<WatchlistItem>('/v1/watchlist/' + id, {
            method: 'PATCH',
            body: JSON.stringify({ notes }),
        })
    }
    async reorderWatchlist(items: Array<{ id: string; sort_order: number }>): Promise<{ message: string }> {
        return this.request<{ message: string }>('/v1/watchlist/reorder', {
            method: 'PUT',
            body: JSON.stringify({ items }),
        })
    }

    // Scheduled Analysis
    async getScheduled(): Promise<{ items: ScheduledAnalysis[] }> {
        return this.request<{ items: ScheduledAnalysis[] }>('/v1/scheduled')
    }
    async getPortfolioOverview(): Promise<PortfolioOverviewResponse> {
        return this.request<PortfolioOverviewResponse>('/v1/portfolio/overview')
    }
    async createScheduled(symbol: string, horizon?: string, trigger_time?: string): Promise<ScheduledAnalysis> {
        return this.request<ScheduledAnalysis>('/v1/scheduled', {
            method: 'POST',
            body: JSON.stringify({ symbol, horizon, trigger_time }),
        })
    }
    async updateScheduled(id: string, data: { is_active?: boolean; horizon?: string; trigger_time?: string }): Promise<ScheduledAnalysis> {
        return this.request<ScheduledAnalysis>('/v1/scheduled/' + id, {
            method: 'PATCH',
            body: JSON.stringify(data),
        })
    }
    async updateScheduledBatch(
        item_ids: string[],
        data: { is_active?: boolean; horizon?: string; trigger_time?: string }
    ): Promise<{ items: ScheduledAnalysis[] }> {
        return this.request<{ items: ScheduledAnalysis[] }>('/v1/scheduled/batch', {
            method: 'PATCH',
            body: JSON.stringify({ item_ids, ...data }),
        })
    }
    async deleteScheduled(id: string): Promise<void> {
        await this.request('/v1/scheduled/' + id, { method: 'DELETE' })
    }
    async deleteScheduledBatch(item_ids: string[]): Promise<{ deleted_ids: string[]; missing_ids: string[] }> {
        return this.request<{ deleted_ids: string[]; missing_ids: string[] }>('/v1/scheduled/batch/delete', {
            method: 'POST',
            body: JSON.stringify({ item_ids }),
        })
    }
    async triggerScheduledTest(id: string): Promise<AnalysisResponse> {
        return this.request<AnalysisResponse>(`/v1/scheduled/${id}/trigger`, {
            method: 'POST',
        })
    }
    async triggerScheduledBatch(item_ids: string[]): Promise<ScheduledBatchTriggerResponse> {
        return this.request<ScheduledBatchTriggerResponse>('/v1/scheduled/batch/trigger', {
            method: 'POST',
            body: JSON.stringify({ item_ids }),
        })
    }

    async getPortfolioImportState(): Promise<PortfolioImportState> {
        return this.request<PortfolioImportState>('/v1/portfolio/imports')
    }

    async syncPortfolioImport(data: {
        positions: PortfolioPositionInput[]
        source?: string
        auto_apply_scheduled: boolean
    }): Promise<PortfolioImportState> {
        return this.request<PortfolioImportState>('/v1/portfolio/imports', {
            method: 'POST',
            body: JSON.stringify(data),
        })
    }

    async clearPortfolioImport(): Promise<void> {
        await this.request('/v1/portfolio/imports', { method: 'DELETE' })
    }

    async parsePositionImage(file: File, mode: 'position' | 'watchlist' = 'position'): Promise<{ positions: PortfolioPositionInput[] } | WatchlistTableParseResponse> {
        const formData = new FormData()
        formData.append('file', file)
        formData.append('mode', mode)
        const url = `${getBaseUrl()}/v1/portfolio/parse-image`
        const token = getAuthToken()
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                ...(token ? { Authorization: `Bearer ${token}` } : {}),
            },
            body: formData,
        })
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: response.statusText }))
            throw new Error(error.detail || '图片解析失败')
        }
        return response.json()
    }

    // [VLM-001] watchlist_table_parser
    async addToWatchlistBatchNotes(entries: Array<{ symbol: string; notes?: string }>): Promise<WatchlistBatchResponse> {
        return this.request<WatchlistBatchResponse>('/v1/watchlist/batch-notes', {
            method: 'POST',
            body: JSON.stringify({ entries }),
        })
    }

    async getDashboardTrackingBoard(): Promise<TrackingBoardResponse> {
        return this.request<TrackingBoardResponse>('/v1/dashboard/tracking-board')
    }

    // Stock Search
    async searchStocks(q: string): Promise<{ results: StockSearchResult[] }> {
        return this.request<{ results: StockSearchResult[] }>(`/v1/market/stock-search?q=${encodeURIComponent(q)}`)
    }

    async getConfig(): Promise<RuntimeConfig> {
        return this.request<RuntimeConfig>('/v1/config')
    }

    async updateConfig(updates: RuntimeConfigUpdate): Promise<RuntimeConfigUpdateResponse> {
        return this.request<RuntimeConfigUpdateResponse>('/v1/config', {
            method: 'PATCH',
            body: JSON.stringify(updates),
        })
    }

    async warmupConfig(request: RuntimeWarmupRequest): Promise<RuntimeWarmupResponse> {
        return this.request<RuntimeWarmupResponse>('/v1/config/warmup', {
            method: 'POST',
            body: JSON.stringify(request),
        })
    }

    async warmupWecom(request: WecomWarmupRequest): Promise<WecomWarmupResponse> {
        return this.request<WecomWarmupResponse>('/v1/config/wecom/warmup', {
            method: 'POST',
            body: JSON.stringify(request),
        })
    }

    async warmupBark(request: BarkWarmupRequest): Promise<BarkWarmupResponse> {
        return this.request<BarkWarmupResponse>('/v1/config/bark/warmup', {
            method: 'POST',
            body: JSON.stringify(request),
        })
    }

    async requestLoginCode(email: string): Promise<{ message: string; dev_code?: string }> {
        return this.request('/v1/auth/request-code', {
            method: 'POST',
            body: JSON.stringify({ email }),
        })
    }

    async verifyLoginCode(email: string, code: string): Promise<AuthVerifyResponse> {
        return this.request('/v1/auth/verify-code', {
            method: 'POST',
            body: JSON.stringify({ email, code }),
        })
    }

    async getMe(): Promise<AuthUser> {
        return this.request('/v1/auth/me')
    }

    // Token Management
    async getTokens(): Promise<UserToken[]> {
        return this.request<UserToken[]>('/v1/tokens')
    }

    async createToken(request: UserTokenCreateRequest): Promise<UserToken> {
        return this.request<UserToken>('/v1/tokens', {
            method: 'POST',
            body: JSON.stringify(request),
        })
    }

    async deleteToken(tokenId: string): Promise<{ message: string }> {
        return this.request<{ message: string }>(`/v1/tokens/${tokenId}`, {
            method: 'DELETE',
        })
    }

    // Feedback
    async createFeedback(subject: string, content: string): Promise<FeedbackItem> {
        return this.request<FeedbackItem>('/v1/feedbacks', {
            method: 'POST',
            body: JSON.stringify({ subject, content }),
        })
    }

    async listFeedbacks(page = 1, pageSize = 20): Promise<FeedbackListResponse> {
        return this.request<FeedbackListResponse>(`/v1/feedbacks?page=${page}&page_size=${pageSize}`)
    }

    async getFeedback(id: string): Promise<FeedbackItem> {
        return this.request<FeedbackItem>(`/v1/feedbacks/${id}`)
    }

    async getFeedbackUnreadCount(): Promise<FeedbackUnreadResponse> {
        return this.request<FeedbackUnreadResponse>('/v1/feedbacks/unread-count')
    }

    async markFeedbackRead(id: string): Promise<void> {
        return this.request<void>(`/v1/feedbacks/${id}/read`, { method: 'POST' })
    }

    // TradeFlow
    async getTradeFlowDailyPlan(date: string): Promise<TradeFlowDailyPlanResponse> {
        return this.request<TradeFlowDailyPlanResponse>(`/v1/tradeflow/daily-plan?date=${encodeURIComponent(date)}`)
    }

    async getTradeFlowCandidates(date: string, tier?: string, needDeepTa?: boolean, candidateType?: string, pool?: string): Promise<TradeFlowCandidatesResponse> {  // [H-005] mandate_radar_ui [TF-P0-002] tradeflow_pool_split
        const params = new URLSearchParams({ date })
        if (tier) params.append('tier', tier)
        if (needDeepTa !== undefined) params.append('need_deep_ta', String(needDeepTa))
        if (candidateType) params.append('candidate_type', candidateType)  // [H-005]
        if (pool) params.append('pool', pool)  // [TF-P0-002]
        return this.request<TradeFlowCandidatesResponse>(`/v1/tradeflow/candidates?${params}`)
    }

    async getTradeFlowCandidateDetail(symbol: string, date: string): Promise<TradeFlowCandidateDetailResponse> {
        return this.request<TradeFlowCandidateDetailResponse>(`/v1/tradeflow/candidates/${encodeURIComponent(symbol)}?date=${encodeURIComponent(date)}`)
    }

    async getTradeFlowObserve(date: string): Promise<TradeFlowObserveResponse> {
        return this.request<TradeFlowObserveResponse>(`/v1/tradeflow/observe?date=${encodeURIComponent(date)}`)
    }

    async getTradeFlowTaQueue(date: string): Promise<TradeFlowTAQueueResponse> {
        return this.request<TradeFlowTAQueueResponse>(`/v1/tradeflow/ta-queue?date=${encodeURIComponent(date)}`)
    }

    async getTradeFlowReview(date: string): Promise<TradeFlowReviewResponse> {
        return this.request<TradeFlowReviewResponse>(`/v1/tradeflow/review?date=${encodeURIComponent(date)}`)
    }

    async getTradeFlowDataHealth(): Promise<TradeFlowDataHealthResponse> {
        return this.request<TradeFlowDataHealthResponse>('/v1/tradeflow/data-health')
    }

    // [UI-007] tradeflow_filtered_trace
    async getTradeFlowFiltered(date: string): Promise<TradeFlowFilteredResponse> {
        return this.request<TradeFlowFilteredResponse>(`/v1/tradeflow/filtered?date=${encodeURIComponent(date)}`)
    }

    async runTradeFlowDiscovery(request: TradeFlowDiscoveryRequest): Promise<TradeFlowDiscoveryResponse> {
        return this.request<TradeFlowDiscoveryResponse>('/v1/tradeflow/discovery', {
            method: 'POST',
            body: JSON.stringify(request),
        })
    }

    // [TF-OBS-001] tradeflow_observe_runner
    async runTradeFlowObserve(date: string): Promise<TradeFlowObserveRunResponse> {
        return this.request<TradeFlowObserveRunResponse>(`/v1/tradeflow/observe/run?date=${encodeURIComponent(date)}`, {
            method: 'POST',
        })
    }
}

export const api = new ApiService()
