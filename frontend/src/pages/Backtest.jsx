import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { runBacktest } from '../api'

const STRATEGY_MATRIX = [
  {
    regime: 'BULLISH',
    color: '#22c55e',
    bg: 'rgba(34,197,94,0.08)',
    border: 'rgba(34,197,94,0.3)',
    rows: ['EMA 5 > EMA 20 (≥0.15%)', 'RSI 40–70 + IV ≥ 30 → Iron Condor', 'RSI 40–70 + IV < 30 → Bull Put Spread'],
  },
  {
    regime: 'BEARISH',
    color: '#ef4444',
    bg: 'rgba(239,68,68,0.08)',
    border: 'rgba(239,68,68,0.3)',
    rows: ['EMA 5 < EMA 20 (≥0.15%)', 'RSI 30–60 + IV ≥ 30 → Iron Condor', 'RSI 30–60 + IV < 30 → Bear Call Spread'],
  },
  {
    regime: 'NEUTRAL',
    color: '#f59e0b',
    bg: 'rgba(245,158,11,0.08)',
    border: 'rgba(245,158,11,0.3)',
    rows: ['EMAs intertwined (< 0.15%)', 'RSI 40–60 + IV ≥ 30 → Iron Condor', 'RSI 40–60 + IV < 30 → No Trade'],
  },
]

export default function Backtest() {
  const navigate = useNavigate()
  const [form, setForm] = useState({
    instrument: 'NIFTY',
    capital: 500000,
    startDate: '2025-01-06',
    endDate: '2025-01-31',
  })
  const [running, setRunning] = useState(false)
  const [progress, setProgress] = useState(null)
  const [error, setError] = useState(null)
  const [hasResults, setHasResults] = useState(false)

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const handleRun = async () => {
    setError(null)
    setRunning(true)
    setProgress({ current: 0, total: 0, label: 'Starting…' })
    try {
      const res = await runBacktest({
        instrument: form.instrument,
        capital: parseFloat(form.capital),
        startDate: form.startDate,
        endDate: form.endDate,
      })
      setProgress({ current: res.data.length, total: res.data.length, label: 'Done!' })
      setHasResults(true)
      setTimeout(() => navigate('/dashboard'), 800)
    } catch (err) {
      setError(err.response?.data?.detail || err.message || 'An error occurred.')
      setProgress(null)
    } finally {
      setRunning(false)
    }
  }

  const inputCls = `w-full px-3 py-2 rounded text-sm outline-none focus:ring-1 focus:ring-blue-500 transition`
  const inputStyle = { background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text-primary)' }
  const labelCls = 'block text-xs uppercase tracking-widest mb-1.5'
  const labelStyle = { color: 'var(--text-secondary)' }

  return (
    <div className="max-w-4xl mx-auto p-6">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-lg font-bold text-slate-100">Run Backtest</h1>
        <p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
          Strategy auto-selected per 1-min regime detection · EMA(5,20) · RSI(14) · IV Rank
        </p>
      </div>

      {/* Config card */}
      <div className="rounded-xl p-5 mb-5"
        style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>

        <div className="grid grid-cols-2 gap-4 mb-4">
          {/* Instrument */}
          <div>
            <label className={labelCls} style={labelStyle}>Instrument</label>
            <select
              className={inputCls}
              style={inputStyle}
              value={form.instrument}
              onChange={e => set('instrument', e.target.value)}
              disabled={running}
            >
              <option value="NIFTY">Nifty 50</option>
              <option value="BANKNIFTY">Bank Nifty</option>
            </select>
          </div>

          {/* Capital */}
          <div>
            <label className={labelCls} style={labelStyle}>Capital (₹)</label>
            <input
              type="number"
              className={inputCls}
              style={inputStyle}
              value={form.capital}
              min={50000}
              max={10000000}
              step={50000}
              onChange={e => set('capital', e.target.value)}
              disabled={running}
            />
            <div className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
              ₹50,000 – ₹1,00,00,000
            </div>
          </div>

          {/* Start date */}
          <div>
            <label className={labelCls} style={labelStyle}>Start Date</label>
            <input
              type="date"
              className={inputCls}
              style={inputStyle}
              value={form.startDate}
              onChange={e => set('startDate', e.target.value)}
              disabled={running}
            />
          </div>

          {/* End date */}
          <div>
            <label className={labelCls} style={labelStyle}>End Date</label>
            <input
              type="date"
              className={inputCls}
              style={inputStyle}
              value={form.endDate}
              onChange={e => set('endDate', e.target.value)}
              disabled={running}
            />
            <div className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
              Max 60 trading days
            </div>
          </div>
        </div>

        {/* Error */}
        {error && (
          <div className="mb-4 px-3 py-2 rounded text-xs"
            style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#ef4444' }}>
            {error}
          </div>
        )}

        {/* Progress */}
        {running && progress && (
          <div className="mb-4">
            <div className="flex justify-between text-xs mb-1.5" style={{ color: 'var(--text-secondary)' }}>
              <span>{progress.label}</span>
            </div>
            <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--surface-tertiary)' }}>
              <div className="h-full rounded-full progress-animate"
                style={{ background: '#3b82f6', width: progress.total ? `${progress.current / progress.total * 100}%` : '60%' }} />
            </div>
          </div>
        )}

        {/* Actions */}
        <div className="flex gap-3">
          <button
            onClick={handleRun}
            disabled={running}
            className="flex items-center gap-2 px-5 py-2 rounded font-semibold text-sm transition-all"
            style={{
              background: running ? '#334155' : '#2563eb',
              color: 'white',
              cursor: running ? 'not-allowed' : 'pointer',
            }}
          >
            {running && <span className="spinner" />}
            {running ? 'Running…' : 'Run Backtest'}
          </button>

          {hasResults && !running && (
            <button
              onClick={() => navigate('/dashboard')}
              className="px-5 py-2 rounded font-medium text-sm transition-all"
              style={{ background: 'var(--surface-tertiary)', color: 'var(--text-primary)', cursor: 'pointer' }}
            >
              View Dashboard →
            </button>
          )}
        </div>
      </div>

      {/* Strategy info panel */}
      <div className="rounded-xl p-5"
        style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>
        <div className="text-xs uppercase tracking-widest mb-4" style={{ color: 'var(--text-secondary)' }}>
          Strategy Auto-Selection Logic
        </div>
        <div className="grid grid-cols-3 gap-3">
          {STRATEGY_MATRIX.map(col => (
            <div key={col.regime} className="rounded-lg p-3"
              style={{ background: col.bg, border: `1px solid ${col.border}` }}>
              <div className="font-bold mb-2 text-xs" style={{ color: col.color }}>
                {col.regime}
              </div>
              {col.rows.map((row, i) => (
                <div key={i} className="text-xs mb-1 leading-relaxed" style={{ color: 'var(--text-secondary)' }}>
                  {row}
                </div>
              ))}
            </div>
          ))}
        </div>
        <div className="mt-3 text-xs" style={{ color: 'var(--text-secondary)' }}>
          RSI &gt; 70 or &lt; 30 (any regime) → <span style={{ color: '#64748b' }}>No Trade</span>
          &nbsp;·&nbsp; Entry at 09:30 · Exit: Profit target, Hard stop (75%), or End of day (15:15)
          &nbsp;·&nbsp; Position size: 2% max capital risk
        </div>
      </div>
    </div>
  )
}
