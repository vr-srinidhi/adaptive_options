import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { getWorkbenchRun } from '../api'
import { fmtINR, runStatusTone } from '../utils/workbench'

export default function WorkbenchHistoryDetail() {
  const navigate = useNavigate()
  const { kind, id } = useParams()
  const [payload, setPayload] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    getWorkbenchRun(kind, id)
      .then(res => setPayload(res.data))
      .catch(err => setError(err.response?.data?.detail || err.message))
      .finally(() => setLoading(false))
  }, [kind, id])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-72 gap-2 wb-muted">
        <span className="spinner" /> Loading history detail…
      </div>
    )
  }

  if (error) {
    return (
      <div className="max-w-6xl mx-auto p-6">
        <div className="wb-alert-error">{error}</div>
      </div>
    )
  }

  const run = payload?.run
  const sessions = payload?.sessions || []
  if (!run) return null

  const tone = runStatusTone(run.status)

  return (
    <div className="wb-page">
      <section className="wb-card p-6">
        <div className="flex items-start justify-between gap-5 flex-wrap">
          <div>
            <button className="wb-link" onClick={() => navigate('/workbench/history')}>← Back to history</button>
            <h1 className="mt-4 text-3xl font-semibold text-[var(--text-primary)]">{run.title}</h1>
            <p className="mt-2 text-sm wb-muted">{run.subtitle}</p>
          </div>
          <div className="text-right">
            <span className="wb-chip" style={{ background: tone.background, borderColor: tone.border, color: tone.color }}>
              {run.status}
            </span>
            <div className="mt-4 text-4xl font-semibold" style={{ color: run.pnl == null ? 'var(--text-secondary)' : run.pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
              {fmtINR(run.pnl)}
            </div>
          </div>
        </div>
      </section>

      <section className="wb-grid wb-grid-4 mt-6">
        <div className="wb-card p-4">
          <div className="wb-kicker">Sessions</div>
          <div className="mt-2 text-2xl font-semibold text-[var(--text-primary)]">{run.metrics?.total_sessions ?? sessions.length}</div>
        </div>
        <div className="wb-card p-4">
          <div className="wb-kicker">Completed</div>
          <div className="mt-2 text-2xl font-semibold text-[var(--text-primary)]">{run.metrics?.completed_sessions ?? '—'}</div>
        </div>
        <div className="wb-card p-4">
          <div className="wb-kicker">Failed</div>
          <div className="mt-2 text-2xl font-semibold text-[var(--text-primary)]">{run.metrics?.failed_sessions ?? '—'}</div>
        </div>
        <div className="wb-card p-4">
          <div className="wb-kicker">Win Rate</div>
          <div className="mt-2 text-2xl font-semibold text-[var(--text-primary)]">{run.metrics?.win_rate != null ? `${run.metrics.win_rate}%` : '—'}</div>
        </div>
      </section>

      <section className="wb-card p-5 mt-6">
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="wb-kicker">Child sessions</div>
            <h2 className="text-lg font-semibold text-[var(--text-primary)] mt-1">Session-level replay entry points</h2>
          </div>
          {run.legacy_route && (
            <button className="wb-secondary-button" onClick={() => navigate(run.legacy_route)}>
              Open legacy batch view
            </button>
          )}
        </div>

        <div className="overflow-auto mt-4">
          <table className="w-full text-sm">
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                {['Date', 'Status', 'Final state', 'Decisions', 'P/L', 'Actions'].map(header => (
                  <th key={header} className="text-left py-2 pr-4 wb-muted text-[11px] uppercase tracking-[0.18em]">{header}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sessions.map(session => (
                <tr key={session.id} style={{ borderBottom: '1px solid rgba(39, 54, 75, 0.45)' }}>
                  <td className="py-3 pr-4 text-[var(--text-primary)]">{session.session_date}</td>
                  <td className="py-3 pr-4 text-[var(--text-primary)]">{session.status}</td>
                  <td className="py-3 pr-4 wb-muted">{session.final_session_state || '—'}</td>
                  <td className="py-3 pr-4 text-[var(--text-primary)]">{session.decision_count ?? '—'}</td>
                  <td className="py-3 pr-4" style={{ color: session.summary_pnl == null ? 'var(--text-secondary)' : session.summary_pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                    {fmtINR(session.summary_pnl)}
                  </td>
                  <td className="py-3 pr-4">
                    <div className="flex gap-2">
                      <button className="wb-primary-button" onClick={() => navigate(session.route)}>
                        Replay
                      </button>
                      <button className="wb-secondary-button" onClick={() => navigate(session.legacy_route)}>
                        Legacy
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
