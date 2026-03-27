import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 120000,
})

export const runBacktest = (payload) => api.post('/backtest/run', payload)
export const getResults = (params) => api.get('/backtest/results', { params })
export const getSession = (id) => api.get(`/backtest/results/${id}`)
export const getSummary = (params) => api.get('/backtest/summary', { params })
export const clearResults = () => api.delete('/backtest/results')
