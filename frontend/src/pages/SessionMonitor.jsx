import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getPaperSessions } from '../api'

const fmtINR = v => v == null ? '—' :
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(v)

const STATUS_STYLES = {
  COMPLETED: { bg: 'rgba(34,197,94,0.12)', border: 'rgba(34,197,94,0.35)', text: '#22c55e' },
  RUNNING:   { bg: 'rgba(59,130,246,0.12)', border: 'rgba(59,130,246,0.35)', text: '#3b82f6' },
  ERROR:     { bg: 'rgba(239,68,68,0.12)', border: 'rgba(239,68,68,0.35)', text: '#ef4444' },
}

function StatusBadge({ status }) {
  const s = STATUS_STYLES[status] || STATUS_STYLES.RUNNING
  return (
    <span className="px-2 py-0.5 rounded text-xs font-semibold"
      style={{ background: s.bg, border: `1px solid ${s.border}`, color: s.text }}>
      {status}
    </span>
  )
}

export default function SessionMonitor() {
  const navigate = useNavigate()
  const [sessions, setSessions] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    getPaperSessions()
      .then(r => setSessions(r.data))
      .catch(err => setError(err.response?.data?.detail || err.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return (
    <div className="flex items-center justify-center h-64 gap-2" style={{ color: 'var(--text-secondary)' }}>
      <span className="spinner" /> Loading sessions…
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

  return (
    <div className="max-w-5xl mx-auto p-6">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-lg font-bold text-slate-100">Paper Trading Sessions</h1>
          <p className="text-xs mt-0.5" style={{ color: 'var(--text-secondary)' }}>
            ORB replay sessions · click a row to view full minute audit log
          </p>
        </div>
        <button
          onClick={() => navigate('/paper')}
          className="px-4 py-1.5 rounded text-xs font-medium"
          style={{ background: '#f59e0b', color: '#0f172a', cursor: 'pointer' }}>
          + New Replay
        </button>
      </div>

      {sessions.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-24 gap-4">
          <div className="text-4xl">📋</div>
          <div className="text-sm" style={{ color: 'var(--text-secondary)' }}>No paper trading sessions yet.</div>
          <button onClick={() => navigate('/paper')}
            className="px-4 py-2 rounded text-sm font-medium"
            style={{ background: '#f59e0b', color: '#0f172a', cursor: 'pointer' }}>
            Replay your first day →
          </button>
        </div>
      ) : (
        <div className="rounded-xl overflow-hidden"
          style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>
          <table className="w-full text-xs">
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface-tertiary)' }}>
                {['Date', 'Instrument', 'Capital', 'Status', 'Decisions', 'Created At'].map(h => (
                  <th key={h} className="text-left px-3 py-2.5 font-medium uppercase tracking-wider"
                    style={{ color: 'var(--text-secondary)' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sessions.map(s => (
                <tr
                  key={s.id}
                  className="table-row-hover"
                  style={{ borderBottom: '0.5px solid var(--border)', cursor: 'pointer' }}
                  onClick={() => navigate(`/paper/session/${s.id}`)}
                >
                  <td className="px-3 py-2.5 font-medium" style={{ color: 'var(--text-primary)' }}>
                    {s.session_date}
                  </td>
                  <td className="px-3 py-2.5" style={{ color: 'var(--text-secondary)' }}>
                    {s.instrument}
                  </td>
                  <td className="px-3 py-2.5" style={{ color: 'var(--text-secondary)' }}>
                    {fmtINR(s.capital)}
                  </td>
                  <td className="px-3 py-2.5">
                    <StatusBadge status={s.status} />
                  </td>
                  <td className="px-3 py-2.5" style={{ color: 'var(--text-secondary)' }}>
                    {s.decision_count ?? '—'}
                  </td>
                  <td className="px-3 py-2.5" style={{ color: 'var(--text-secondary)' }}>
                    {s.created_at ? s.created_at.slice(0, 16).replace('T', ' ') : '—'}
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
