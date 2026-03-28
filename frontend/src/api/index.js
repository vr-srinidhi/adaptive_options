import axios from 'axios'

// In Railway the frontend calls the backend's public URL directly (VITE_API_URL).
// Locally, nginx proxies /api/ to the backend container (no env var needed).
const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || '/api',
  timeout: 120000,
})

export const runBacktest = (payload) => api.post('/backtest/run', payload)
export const getResults = (params) => api.get('/backtest/results', { params })
export const getSession = (id) => api.get(`/backtest/results/${id}`)
export const getSummary = (params) => api.get('/backtest/summary', { params })
export const clearResults = () => api.delete('/backtest/results')
