import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getBatches, createBatch, deleteBatch, triggerBatch } from '../api'

const fmtINR = v => v == null ? '—' :
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(v)

const STATUS_STYLES = {
  completed:               { bg: 'rgba(34,197,94,0.12)', border: 'rgba(34,197,94,0.35)',  text: '#22c55e' },
  completed_with_warnings: { bg: 'rgba(251,191,36,0.12)', border: 'rgba(251,191,36,0.35)', text: '#fbbf24' },
  running:                 { bg: 'rgba(59,130,246,0.12)',  border: 'rgba(59,130,246,0.35)', text: '#3b82f6' },
  queued:                  { bg: 'rgba(148,163,184,0.12)', border: 'rgba(148,163,184,0.3)', text: '#94a3b8' },
  failed:                  { bg: 'rgba(239,68,68,0.12)',   border: 'rgba(239,68,68,0.35)', text: '#ef4444' },
  draft:                   { bg: 'rgba(100,116,139,0.1)',  border: 'rgba(100,116,139,0.3)', text: '#64748b' },
}

function StatusBadge({ status }) {
  const s = STATUS_STYLES[status] || STATUS_STYLES.draft
  return (
    <span className="px-2 py-0.5 rounded text-xs font-semibold"
      style={{ background: s.bg, border: `1px solid ${s.border}`, color: s.text }}>
      {status}
    </span>
  )
}

const today = new Date().toISOString().split('T')[0]
const twentyDaysAgo = new Date(Date.now() - 20 * 86400000).toISOString().split('T')[0]

const DEFAULT_FORM = {
  name: '',
  instrument: 'NIFTY',
  capital: 2500000,
  start_date: twentyDaysAgo,
  end_date: today,
  execution_order: 'latest_first',
  autorun: true,
}

export default function Backtests() {
  const navigate = useNavigate()
  const [batches, setBatches] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState(DEFAULT_FORM)
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState(null)

  const load = () => {
    setLoading(true)
    getBatches()
      .then(r => setBatches(r.data))
      .catch(err => setError(err.response?.data?.detail || err.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  // Poll running batches every 5 s
  useEffect(() => {
    const hasRunning = batches.some(b => b.status === 'running' || b.status === 'queued')
    if (!hasRunning) return
    const t = setTimeout(load, 5000)
    return () => clearTimeout(t)
  }, [batches])

  const handleCreate = async e => {
    e.preventDefault()
    setFormError(null)
    setSubmitting(true)
    try {
      const res = await createBatch({
        ...form,
        capital: Number(form.capital),
      })
      setBatches(prev => [res.data, ...prev])
      setShowForm(false)
      setForm(DEFAULT_FORM)
    } catch (err) {
      setFormError(err.response?.data?.detail || err.message)
    } finally {
      setSubmitting(false)
    }
  }

  const handleDelete = async (e, id) => {
    e.stopPropagation()
    if (!confirm('Delete this batch and all its sessions?')) return
    try {
      await deleteBatch(id)
      setBatches(prev => prev.filter(b => b.id !== id))
    } catch (err) {
      alert(err.response?.data?.detail || err.message)
    }
  }

  const handleRun = async (e, id) => {
    e.stopPropagation()
    try {
      await triggerBatch(id)
      load()
    } catch (err) {
      alert(err.response?.data?.detail || err.message)
    }
  }

  return (
    <div className="max-w-5xl mx-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-lg font-bold text-slate-100">Historical Backtests</h1>
          <p className="text-xs mt-0.5" style={{ color: 'var(--text-secondary)' }}>
            ORB strategy · DB-backed historical replay · click a row to drill down
          </p>
        </div>
        <button
          onClick={() => setShowForm(v => !v)}
          className="px-4 py-1.5 rounded text-xs font-medium"
          style={{ background: '#f59e0b', color: '#0f172a', cursor: 'pointer' }}>
          {showForm ? 'Cancel' : '+ New Backtest'}
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleCreate}
          className="rounded-xl p-5 mb-6"
          style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
          <h2 className="text-sm font-semibold text-slate-200 mb-4">Configure Backtest Batch</h2>
          <div className="grid grid-cols-2 gap-4">
            <div className="col-span-2">
              <label className="block text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>Batch name</label>
              <input required value={form.name}
                onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                className="w-full px-3 py-2 rounded text-sm"
                style={{ background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)' }}
                placeholder="e.g. NIFTY Jan-Mar 2025" />
            </div>
            <div>
              <label className="block text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>Start date</label>
              <input type="date" required value={form.start_date}
                onChange={e => setForm(f => ({ ...f, start_date: e.target.value }))}
                className="w-full px-3 py-2 rounded text-sm"
                style={{ background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)' }} />
            </div>
            <div>
              <label className="block text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>End date</label>
              <input type="date" required value={form.end_date}
                onChange={e => setForm(f => ({ ...f, end_date: e.target.value }))}
                className="w-full px-3 py-2 rounded text-sm"
                style={{ background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)' }} />
            </div>
            <div>
              <label className="block text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>Capital (₹)</label>
              <input type="number" required min={100000} step={10000} value={form.capital}
                onChange={e => setForm(f => ({ ...f, capital: e.target.value }))}
                className="w-full px-3 py-2 rounded text-sm"
                style={{ background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)' }} />
            </div>
            <div>
              <label className="block text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>Execution order</label>
              <select value={form.execution_order}
                onChange={e => setForm(f => ({ ...f, execution_order: e.target.value }))}
                className="w-full px-3 py-2 rounded text-sm"
                style={{ background: 'var(--bg)', border: '1px solid var(--border)', color: 'var(--text)' }}>
                <option value="latest_first">Latest first</option>
                <option value="oldest_first">Oldest first</option>
              </select>
            </div>
          </div>
          {formError && (
            <div className="mt-3 px-3 py-2 rounded text-xs"
              style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#ef4444' }}>
              {formError}
            </div>
          )}
          <div className="flex justify-end mt-4">
            <button type="submit" disabled={submitting}
              className="px-5 py-2 rounded text-xs font-semibold"
              style={{ background: '#f59e0b', color: '#0f172a', cursor: submitting ? 'not-allowed' : 'pointer', opacity: submitting ? 0.6 : 1 }}>
              {submitting ? 'Creating…' : 'Create & Run'}
            </button>
          </div>
        </form>
      )}

      {error && (
        <div className="mb-4 px-4 py-3 rounded text-sm"
          style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#ef4444' }}>
          {error}
        </div>
      )}

      {loading && batches.length === 0 ? (
        <div className="flex items-center justify-center h-48 gap-2" style={{ color: 'var(--text-secondary)' }}>
          <span className="spinner" /> Loading…
        </div>
      ) : batches.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-24 gap-3">
          <div className="text-4xl">📈</div>
          <div className="text-sm" style={{ color: 'var(--text-secondary)' }}>No backtest batches yet.</div>
          <button onClick={() => setShowForm(true)}
            className="px-4 py-2 rounded text-sm font-medium"
            style={{ background: '#f59e0b', color: '#0f172a', cursor: 'pointer' }}>
            Run your first backtest →
          </button>
        </div>
      ) : (
        <div className="rounded-xl overflow-hidden" style={{ border: '1px solid var(--border)' }}>
          <table className="w-full text-sm">
            <thead>
              <tr style={{ background: 'var(--surface)', borderBottom: '1px solid var(--border)' }}>
                {['Name', 'Status', 'Date range', 'Sessions', 'P&L', 'Actions'].map(h => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-semibold"
                    style={{ color: 'var(--text-secondary)' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {batches.map((b, i) => (
                <tr key={b.id}
                  onClick={() => navigate(`/backtests/${b.id}`)}
                  className="cursor-pointer transition-colors"
                  style={{
                    borderBottom: i < batches.length - 1 ? '1px solid var(--border)' : undefined,
                    background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.02)',
                  }}
                  onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.05)'}
                  onMouseLeave={e => e.currentTarget.style.background = i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.02)'}>
                  <td className="px-4 py-3 font-medium text-slate-200">{b.name}</td>
                  <td className="px-4 py-3"><StatusBadge status={b.status} /></td>
                  <td className="px-4 py-3 text-xs" style={{ color: 'var(--text-secondary)' }}>
                    {b.start_date} → {b.end_date}
                  </td>
                  <td className="px-4 py-3 text-xs" style={{ color: 'var(--text-secondary)' }}>
                    {b.completed_sessions + b.failed_sessions + b.skipped_sessions}/{b.total_sessions}
                    {b.failed_sessions > 0 && <span className="ml-1 text-red-400">({b.failed_sessions} failed)</span>}
                  </td>
                  <td className="px-4 py-3 font-mono text-sm"
                    style={{ color: b.total_pnl == null ? 'var(--text-secondary)' : b.total_pnl >= 0 ? '#22c55e' : '#ef4444' }}>
                    {fmtINR(b.total_pnl)}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex gap-2" onClick={e => e.stopPropagation()}>
                      {(b.status === 'draft' || b.status === 'failed' || b.status === 'completed_with_warnings') && (
                        <button onClick={e => handleRun(e, b.id)}
                          className="px-2 py-1 rounded text-xs font-medium"
                          style={{ background: 'rgba(59,130,246,0.15)', color: '#3b82f6', cursor: 'pointer', border: '1px solid rgba(59,130,246,0.3)' }}>
                          Re-run
                        </button>
                      )}
                      {b.status !== 'running' && b.status !== 'queued' && (
                        <button onClick={e => handleDelete(e, b.id)}
                          className="px-2 py-1 rounded text-xs font-medium"
                          style={{ background: 'rgba(239,68,68,0.1)', color: '#ef4444', cursor: 'pointer', border: '1px solid rgba(239,68,68,0.3)' }}>
                          Delete
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
