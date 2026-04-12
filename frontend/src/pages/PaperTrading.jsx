import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { runPaperSession } from '../api'

export default function PaperTrading() {
  const navigate = useNavigate()
  const [form, setForm] = useState({
    instrument: 'NIFTY',
    capital: 2500000,
    date: '2026-04-07',
  })
  const [running, setRunning] = useState(false)
  const [error, setError] = useState(null)

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }))

  const handleRun = async () => {
    setError(null)
    setRunning(true)
    try {
      const res = await runPaperSession({
        instrument: form.instrument,
        capital: parseFloat(form.capital),
        date: form.date,
      })
      navigate(`/paper/session/${res.data.session_id}`)
    } catch (err) {
      const detail = err.response?.data?.detail
      if (typeof detail === 'string' && detail.includes('No Zerodha access token')) {
        setError(
          'No Zerodha token found. Connect your account first.',
        )
      } else {
        setError(detail || err.message || 'Engine error.')
      }
    } finally {
      setRunning(false)
    }
  }

  const inputCls = 'w-full px-3 py-2 rounded text-sm outline-none focus:ring-1 focus:ring-blue-500 transition'
  const inputStyle = { background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text-primary)' }
  const labelCls = 'block text-xs uppercase tracking-widest mb-1.5'
  const labelStyle = { color: 'var(--text-secondary)' }

  return (
    <div className="max-w-2xl mx-auto p-6">
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center gap-3 mb-1">
          <h1 className="text-lg font-bold text-slate-100">Paper Trade Replay</h1>
          <span className="text-xs px-2 py-0.5 rounded font-medium"
            style={{ background: 'rgba(245,158,11,0.15)', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.3)' }}>
            ORB STRATEGY
          </span>
        </div>
        <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>
          Opening Range Breakout · G1–G7 gate stack · Bull/Bear Call/Put Spread · Full minute audit log
        </p>
      </div>

      {/* Strategy summary */}
      <div className="rounded-xl p-4 mb-5"
        style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>
        <div className="text-xs uppercase tracking-widest mb-3" style={{ color: 'var(--text-secondary)' }}>
          How It Works
        </div>
        <div className="grid grid-cols-3 gap-3">
          {[
            { label: '09:15–09:29', text: 'Build Opening Range (high/low of first 15 candles)', color: '#64748b' },
            { label: '09:30 onward', text: 'Evaluate G1–G7 gates every minute for breakout entry', color: '#3b82f6' },
            { label: 'While open', text: 'Track MTM per minute. Exit on target, stop, or 15:20', color: '#f59e0b' },
          ].map(s => (
            <div key={s.label} className="rounded-lg p-3"
              style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
              <div className="text-xs font-bold mb-1" style={{ color: s.color }}>{s.label}</div>
              <div className="text-xs leading-relaxed" style={{ color: 'var(--text-secondary)' }}>{s.text}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Config form */}
      <div className="rounded-xl p-5"
        style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>
        <div className="grid grid-cols-2 gap-4 mb-4">
          <div>
            <label className={labelCls} style={labelStyle}>Instrument</label>
            <select className={inputCls} style={inputStyle}
              value={form.instrument} onChange={e => set('instrument', e.target.value)} disabled={running}>
              <option value="NIFTY">Nifty 50</option>
              <option value="BANKNIFTY">Bank Nifty</option>
            </select>
          </div>

          <div>
            <label className={labelCls} style={labelStyle}>Capital (₹)</label>
            <input type="number" className={inputCls} style={inputStyle}
              value={form.capital} min={50000} max={10000000} step={50000}
              onChange={e => set('capital', e.target.value)} disabled={running} />
            <div className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>2% max risk = ₹{(form.capital * 0.02).toLocaleString('en-IN')}</div>
          </div>

          <div>
            <label className={labelCls} style={labelStyle}>Date</label>
            <input type="date" className={inputCls} style={inputStyle}
              value={form.date} onChange={e => set('date', e.target.value)} disabled={running} />
            <div className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>Must be a trading day with valid Zerodha data</div>
          </div>

          <div className="flex flex-col justify-end">
            <div className="text-xs px-3 py-2 rounded"
              style={{ background: 'rgba(59,130,246,0.08)', border: '1px solid rgba(59,130,246,0.2)', color: '#64748b' }}>
              Zerodha token loaded from server.{' '}
              <button
                onClick={() => window.open('/zerodha-connect', '_self')}
                className="underline"
                style={{ color: '#3b82f6', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>
                Reconnect
              </button>
            </div>
          </div>
        </div>

        {error && (
          <div className="mb-4 px-3 py-2 rounded text-xs"
            style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#ef4444' }}>
            {error}
            {error.includes('Connect your account') && (
              <> <button onClick={() => window.open('/zerodha-connect', '_self')}
                className="underline ml-1" style={{ color: '#ef4444', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>
                Connect now
              </button></>
            )}
          </div>
        )}

        {running && (
          <div className="mb-4 px-3 py-2 rounded text-xs flex items-center gap-2"
            style={{ background: 'rgba(59,130,246,0.08)', border: '1px solid rgba(59,130,246,0.3)', color: '#3b82f6' }}>
            <span className="spinner" />
            Fetching candles from Zerodha and replaying engine… this takes ~20–40 seconds.
          </div>
        )}

        <div className="flex gap-3 items-center">
          <button
            onClick={handleRun}
            disabled={running}
            className="flex items-center gap-2 px-5 py-2 rounded font-semibold text-sm transition-all"
            style={{
              background: running ? '#334155' : '#f59e0b',
              color: running ? '#94a3b8' : '#0f172a',
              cursor: running ? 'not-allowed' : 'pointer',
            }}
          >
            {running && <span className="spinner" />}
            {running ? 'Replaying…' : 'Replay Day'}
          </button>

          <button
            onClick={() => navigate('/paper/sessions')}
            className="px-4 py-2 rounded text-sm transition-all"
            style={{ background: 'var(--surface)', color: 'var(--text-secondary)', cursor: 'pointer' }}
          >
            View Past Sessions
          </button>
        </div>
      </div>
    </div>
  )
}
