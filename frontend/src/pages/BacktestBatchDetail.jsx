import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { getBatch, getBatchSessions } from '../api'

const fmtINR = v => v == null ? '—' :
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(v)

const STATUS_STYLES = {
  completed:               { bg: 'rgba(34,197,94,0.12)',  border: 'rgba(34,197,94,0.35)',  text: '#22c55e' },
  completed_with_warnings: { bg: 'rgba(251,191,36,0.12)', border: 'rgba(251,191,36,0.35)', text: '#fbbf24' },
  running:                 { bg: 'rgba(59,130,246,0.12)', border: 'rgba(59,130,246,0.35)', text: '#3b82f6' },
  queued:                  { bg: 'rgba(148,163,184,0.12)',border: 'rgba(148,163,184,0.3)', text: '#94a3b8' },
  failed:                  { bg: 'rgba(239,68,68,0.12)',  border: 'rgba(239,68,68,0.35)', text: '#ef4444' },
  COMPLETED:               { bg: 'rgba(34,197,94,0.12)',  border: 'rgba(34,197,94,0.35)',  text: '#22c55e' },
  ERROR:                   { bg: 'rgba(239,68,68,0.12)',  border: 'rgba(239,68,68,0.35)', text: '#ef4444' },
  RUNNING:                 { bg: 'rgba(59,130,246,0.12)', border: 'rgba(59,130,246,0.35)', text: '#3b82f6' },
}

function StatusBadge({ status }) {
  const s = STATUS_STYLES[status] || STATUS_STYLES.queued
  return (
    <span className="px-2 py-0.5 rounded text-xs font-semibold"
      style={{ background: s.bg, border: `1px solid ${s.border}`, color: s.text }}>
      {status}
    </span>
  )
}

function ProgressBar({ done, total }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  return (
    <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'var(--border)' }}>
      <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: '#f59e0b' }} />
    </div>
  )
}

export default function BacktestBatchDetail() {
  const { batchId } = useParams()
  const navigate = useNavigate()
  const [batch, setBatch] = useState(null)
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const load = async () => {
    try {
      const [bRes, sRes] = await Promise.all([
        getBatch(batchId),
        getBatchSessions(batchId, { limit: 500 }),
      ])
      setBatch(bRes.data)
      setSessions(sRes.data)
    } catch (err) {
      setError(err.response?.data?.detail || err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [batchId])

  // Poll while running
  useEffect(() => {
    if (!batch) return
    if (batch.status !== 'running' && batch.status !== 'queued') return
    const t = setTimeout(load, 5000)
    return () => clearTimeout(t)
  }, [batch])

  if (loading) return (
    <div className="flex items-center justify-center h-64 gap-2" style={{ color: 'var(--text-secondary)' }}>
      <span className="spinner" /> Loading…
    </div>
  )

  if (error) return (
    <div className="max-w-4xl mx-auto p-6">
      <div className="px-4 py-3 rounded text-sm"
        style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#ef4444' }}>
        {error}
      </div>
    </div>
  )

  if (!batch) return null

  const done = batch.completed_sessions + batch.failed_sessions + batch.skipped_sessions

  // Aggregate stats
  const tradedSessions = sessions.filter(s => s.final_session_state !== 'OBSERVING' && s.summary_pnl != null)
  const pnlValues = tradedSessions.map(s => s.summary_pnl)
  const winners = pnlValues.filter(v => v > 0).length
  const losers = pnlValues.filter(v => v < 0).length
  const winRate = pnlValues.length > 0 ? Math.round((winners / pnlValues.length) * 100) : null

  return (
    <div className="max-w-5xl mx-auto p-6">
      {/* Header */}
      <div className="flex items-center gap-3 mb-1">
        <button onClick={() => navigate('/backtests')}
          className="text-xs px-2 py-1 rounded"
          style={{ color: 'var(--text-secondary)', background: 'var(--surface)', border: '1px solid var(--border)', cursor: 'pointer' }}>
          ← Backtests
        </button>
        <StatusBadge status={batch.status} />
      </div>
      <h1 className="text-lg font-bold text-slate-100 mt-2 mb-1">{batch.name}</h1>
      <p className="text-xs mb-6" style={{ color: 'var(--text-secondary)' }}>
        {batch.start_date} → {batch.end_date} · {batch.strategy_id}
      </p>

      {/* Progress */}
      {(batch.status === 'running' || batch.status === 'queued') && (
        <div className="rounded-xl p-4 mb-6" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
          <div className="flex justify-between text-xs mb-2" style={{ color: 'var(--text-secondary)' }}>
            <span>Running… {done}/{batch.total_sessions} sessions</span>
            <span className="animate-pulse text-blue-400">live</span>
          </div>
          <ProgressBar done={done} total={batch.total_sessions} />
        </div>
      )}

      {/* Summary stats */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        {[
          { label: 'Total P&L', value: fmtINR(batch.total_pnl), color: batch.total_pnl == null ? undefined : batch.total_pnl >= 0 ? '#22c55e' : '#ef4444' },
          { label: 'Sessions done', value: `${done} / ${batch.total_sessions}` },
          { label: 'Win rate', value: winRate != null ? `${winRate}%` : '—' },
          { label: 'Winners / Losers', value: pnlValues.length > 0 ? `${winners} / ${losers}` : '—' },
        ].map(({ label, value, color }) => (
          <div key={label} className="rounded-xl p-4" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
            <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>{label}</div>
            <div className="text-lg font-bold font-mono" style={{ color: color || 'var(--text)' }}>{value}</div>
          </div>
        ))}
      </div>

      {/* Sessions table */}
      {sessions.length > 0 && (
        <div className="rounded-xl overflow-hidden" style={{ border: '1px solid var(--border)' }}>
          <div className="px-4 py-3" style={{ background: 'var(--surface)', borderBottom: '1px solid var(--border)' }}>
            <span className="text-xs font-semibold" style={{ color: 'var(--text-secondary)' }}>
              SESSIONS ({sessions.length})
            </span>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr style={{ background: 'var(--surface)', borderBottom: '1px solid var(--border)' }}>
                {['Date', 'Status', 'Final state', 'P&L', 'Decisions'].map(h => (
                  <th key={h} className="px-4 py-2 text-left text-xs font-semibold"
                    style={{ color: 'var(--text-secondary)' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sessions.map((s, i) => (
                <tr key={s.id}
                  onClick={() => navigate(`/backtests/sessions/${s.id}`)}
                  className="cursor-pointer transition-colors"
                  style={{ borderBottom: i < sessions.length - 1 ? '1px solid var(--border)' : undefined }}
                  onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.05)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                  <td className="px-4 py-2.5 font-mono text-xs text-slate-300">{s.session_date}</td>
                  <td className="px-4 py-2.5"><StatusBadge status={s.status} /></td>
                  <td className="px-4 py-2.5 text-xs" style={{ color: 'var(--text-secondary)' }}>
                    {s.final_session_state || '—'}
                  </td>
                  <td className="px-4 py-2.5 font-mono text-sm"
                    style={{ color: s.summary_pnl == null ? 'var(--text-secondary)' : s.summary_pnl >= 0 ? '#22c55e' : '#ef4444' }}>
                    {fmtINR(s.summary_pnl)}
                  </td>
                  <td className="px-4 py-2.5 text-xs" style={{ color: 'var(--text-secondary)' }}>
                    {s.decision_count}
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
