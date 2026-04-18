import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '/api',
  timeout: 120000,
  withCredentials: true,  // needed for HttpOnly refresh cookie
})

// ── Auth interceptor: attach access token from in-memory store ────────────────
// The token is injected by AuthContext via the `setToken` helper below.
let _accessToken = null

export function setToken(token) {
  _accessToken = token
}

api.interceptors.request.use(config => {
  if (_accessToken && !config.headers['Authorization']) {
    config.headers['Authorization'] = `Bearer ${_accessToken}`
  }
  return config
})

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

export default api
