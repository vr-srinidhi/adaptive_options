import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '/api',
  timeout: 120000,
  withCredentials: true,  // needed for HttpOnly refresh cookie
})

// ── Auth interceptor: attach access token from in-memory store ────────────────
// The token is injected by AuthContext via the `setToken` helper below.
let _accessToken = null
let _refreshAccessToken = null
let _clearAuthState = null
let _refreshPromise = null

export function setToken(token) {
  _accessToken = token
}

export function setAuthHandlers({ refreshAccessToken, clearAuthState } = {}) {
  _refreshAccessToken = refreshAccessToken || null
  _clearAuthState = clearAuthState || null
}

async function refreshAccessTokenOnce() {
  if (!_refreshAccessToken) return null
  if (!_refreshPromise) {
    _refreshPromise = Promise.resolve(_refreshAccessToken()).finally(() => {
      _refreshPromise = null
    })
  }
  return _refreshPromise
}

api.interceptors.request.use(config => {
  if (_accessToken && !config.headers['Authorization']) {
    config.headers['Authorization'] = `Bearer ${_accessToken}`
  }
  return config
})

api.interceptors.response.use(
  response => response,
  async error => {
    const status = error?.response?.status
    const original = error?.config
    const url = original?.url || ''
    const isAuthEndpoint = url.includes('/users/login') || url.includes('/users/refresh') || url.includes('/users/logout')

    if (status === 401 && original && !original._retry && !isAuthEndpoint) {
      original._retry = true
      const nextToken = await refreshAccessTokenOnce()
      if (nextToken) {
        original.headers = original.headers || {}
        original.headers.Authorization = `Bearer ${nextToken}`
        return api(original)
      }
    }

    if (status === 401 && !url.includes('/users/logout') && _clearAuthState) {
      _clearAuthState()
    }

    return Promise.reject(error)
  }
)

// ── Backtest ──────────────────────────────────────────────────────────────────
export const runBacktest = (payload) => api.post('/backtest/run', payload)
export const getResults = (params) => api.get('/backtest/results', { params })
export const getSession = (id) => api.get(`/backtest/results/${id}`)
export const getSummary = (params) => api.get('/backtest/summary', { params })
export const clearResults = () => api.delete('/backtest/results')

// ── Paper Trading ─────────────────────────────────────────────────────────────
export const runPaperSession = (payload) => api.post('/paper/session/run', payload)
export const getPaperSessions = (params) => api.get('/paper/sessions', { params })
export const getPaperSession = (id) => api.get(`/paper/session/${id}`)
export const getPaperDecisions = (id, params) => api.get(`/paper/session/${id}/decisions`, { params })
export const getPaperTrade = (id) => api.get(`/paper/session/${id}/trade`)
export const getPaperMarks = (id) => api.get(`/paper/session/${id}/trade/marks`)
export const getPaperCandles = (id) => api.get(`/paper/session/${id}/candles`)
export const exportPaperSessionsBundle = (sessionIds) => api.post(
  '/paper/sessions/export-bundle',
  { session_ids: sessionIds },
  { timeout: 300000 }
)

// ── App Auth ──────────────────────────────────────────────────────────────────
export const authRegister = (payload) => api.post('/users/register', payload)
export const authLogin = (payload) => api.post('/users/login', payload)
export const authRefresh = () => api.post('/users/refresh')
export const authLogout = () => api.post('/users/logout')
export const authMe = () => api.get('/users/me')

// ── Historical data ───────────────────────────────────────────────────────────
export const getTradingDays = (params) => api.get('/historical/trading-days', { params })
export const ingestDay = (date, payload) => api.post(`/historical/ingest/day/${date}`, payload)
export const ingestBulk = (payload) => api.post('/historical/ingest/bulk', payload)
export const syncCatalogue = () => api.post('/historical/catalogue/sync')

// ── Backtests ─────────────────────────────────────────────────────────────────
export const createBatch = (payload) => api.post('/backtests/batches', payload)
export const getBatches = (params) => api.get('/backtests/batches', { params })
export const getBatch = (id) => api.get(`/backtests/batches/${id}`)
export const triggerBatch = (id) => api.post(`/backtests/batches/${id}/run`)
export const deleteBatch = (id) => api.delete(`/backtests/batches/${id}`)
export const getBatchSessions = (id, params) => api.get(`/backtests/batches/${id}/sessions`, { params })
export const getHistSession = (id) => api.get(`/backtests/sessions/${id}`)
export const getHistDecisions = (id, params) => api.get(`/backtests/sessions/${id}/decisions`, { params })
export const getHistTrade = (id) => api.get(`/backtests/sessions/${id}/trade`)
export const getHistMarks = (id) => api.get(`/backtests/sessions/${id}/trade/marks`)

// ── Zerodha ───────────────────────────────────────────────────────────────────
export const zerodhaLoginUrl = () => api.get('/auth/zerodha/login-url')
export const zerodhaSession = (payload) => api.post('/auth/zerodha/session', payload)
export const zerodhaStatus = () => api.get('/auth/zerodha/status')
export const zerodhaSetTokenDirect = (access_token) => api.post('/auth/zerodha/token', { access_token })

// ── Workbench v2 ─────────────────────────────────────────────────────────────
export const getWorkbenchSummary = () => api.get('/v2/workspace/summary')
export const getStrategyDashboard = () => api.get('/v2/workspace/strategy-dashboard')
export const getDayCompare = (date, strategyIds) => api.get('/v2/workspace/day-compare', {
  params: { date, ...(strategyIds?.length ? { strategy_ids: strategyIds.join(',') } : {}) },
})
export const getWorkbenchStrategies = () => api.get('/v2/strategies')
export const getWorkbenchStrategy = (strategyId) => api.get(`/v2/strategies/${strategyId}`)
export const getWorkbenchRuns = (params) => api.get('/v2/runs', { params })
export const createWorkbenchRun = (payload) => api.post('/v2/runs', payload)
export const validateWorkbenchRun = (payload) => api.post('/v2/runs/validate', payload)
export const getWorkbenchRun = (kind, id) => api.get(`/v2/runs/${kind}/${id}`)
export const getWorkbenchReplay = (kind, id) => api.get(`/v2/runs/${kind}/${id}/replay`)
export const compareWorkbenchRuns = (refs) => api.get('/v2/runs/compare', { params: { refs } })
export const getStrategyRunReplayCsv = (id) => api.get(`/v2/runs/strategy_run/${id}/replay/csv`, { responseType: 'blob' })

// ── Live Paper Trading ────────────────────────────────────────────────────────
export const getLivePaperConfig    = ()           => api.get('/v2/live-paper/config')
export const updateLivePaperConfig = (payload)    => api.put('/v2/live-paper/config', payload)
export const getLivePaperToday     = ()           => api.get('/v2/live-paper/today')
export const getLivePaperHistory   = (params)     => api.get('/v2/live-paper/history', { params })
export const startLivePaper        = ()           => api.post('/v2/live-paper/start')
export const stopLivePaper         = ()           => api.post('/v2/live-paper/stop')
export const exportStrategyRunsBundle = (runIds) => api.post(
  '/v2/runs/strategy_run/export-bundle',
  { run_ids: runIds },
  { responseType: 'blob', timeout: 300000 },
)

export default api
