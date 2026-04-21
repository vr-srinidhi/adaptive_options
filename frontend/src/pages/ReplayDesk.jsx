import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getWorkbenchRuns } from '../api'
import { fmtDateTime, fmtINR, runStatusTone } from '../utils/workbench'

export default function ReplayDesk() {
  const navigate = useNavigate()
  const [runs, setRuns] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    getWorkbenchRuns({ kind: 'paper_session', limit: 12 })
      .then(res => setRuns(res.data.runs || []))
      .catch(err => setError(err.response?.data?.detail || err.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-72 gap-2 wb-muted">
        <span className="spinner" /> Loading replay desk…
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

  return (
    <div className="wb-page">
      <section className="wb-hero">
        <div>
          <div className="wb-kicker">Replay Desk</div>
          <h1 className="wb-hero-title">Pick up where the last session left off.</h1>
          <p className="wb-hero-copy">
            Session-level replay is available for paper runs immediately. Historical batch sessions can be opened from the history view.
          </p>
        </div>
        <div className="flex gap-3 flex-wrap">
          <button className="wb-primary-button" onClick={() => navigate('/workbench/run')}>New replay</button>
          <button className="wb-secondary-button" onClick={() => navigate('/workbench/history')}>Open history</button>
        </div>
      </section>

      {runs.length === 0 ? (
        <div className="wb-card p-10 mt-6 text-center">
          <div className="text-4xl">⌁</div>
          <div className="mt-3 text-lg font-semibold text-[var(--text-primary)]">No replayable sessions yet</div>
          <div className="mt-2 text-sm wb-muted">Launch the first ORB paper replay from the builder.</div>
        </div>
      ) : (
        <section className="wb-grid wb-grid-3 mt-6">
          {runs.map(run => {
            const tone = runStatusTone(run.status)
            return (
              <button
                key={run.id}
                className="wb-card p-5 text-left transition-all hover:-translate-y-0.5"
                onClick={() => navigate(run.route)}
              >
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-lg font-semibold text-[var(--text-primary)]">{run.title}</div>
                    <div className="text-sm mt-1 wb-muted">{run.subtitle}</div>
                  </div>
                  <span className="wb-chip" style={{ background: tone.background, borderColor: tone.border, color: tone.color }}>
                    {run.status}
                  </span>
                </div>
                <div className="mt-5 space-y-3 text-sm">
                  <div className="wb-stat-row">
                    <span>Created</span>
                    <strong>{fmtDateTime(run.created_at)}</strong>
                  </div>
                  <div className="wb-stat-row">
                    <span>P/L</span>
                    <strong style={{ color: run.pnl == null ? 'var(--text-secondary)' : run.pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                      {fmtINR(run.pnl)}
                    </strong>
                  </div>
                  <div className="wb-stat-row">
                    <span>Decisions</span>
                    <strong>{run.metrics?.decision_count ?? '—'}</strong>
                  </div>
                </div>
              </button>
            )
          })}
        </section>
      )}
    </div>
  )
}
