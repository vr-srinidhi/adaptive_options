import axios from 'axios'

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '/api',
  timeout: 120000,
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
