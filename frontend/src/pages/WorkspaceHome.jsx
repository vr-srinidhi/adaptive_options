import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { getWorkbenchSummary } from '../api'
import { fmtDateTime, fmtINR, runKindLabel, runStatusTone, strategyStatusTone } from '../utils/workbench'

function MetricTile({ label, value, hint }) {
  return (
    <div className="wb-card p-4">
      <div className="text-[11px] uppercase tracking-[0.24em] wb-muted">{label}</div>
      <div className="mt-2 text-2xl font-semibold text-[var(--text-primary)]">{value}</div>
      {hint && <div className="mt-1 text-xs wb-muted">{hint}</div>}
    </div>
  )
}

export default function WorkspaceHome() {
  const navigate = useNavigate()
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    getWorkbenchSummary()
      .then(res => setSummary(res.data))
      .catch(err => setError(err.response?.data?.detail || err.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-72 gap-2 wb-muted">
        <span className="spinner" /> Loading workspace…
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

  const metrics = summary?.metrics || {}
  const readiness = summary?.data_readiness || {}

  return (
    <div className="wb-page">
      <section className="wb-hero">
        <div>
          <div className="wb-kicker">Adaptive Options Workbench</div>
          <h1 className="wb-hero-title">Strategy research, replay, and historical runs in one shell.</h1>
          <p className="wb-hero-copy">
            The current release preserves the live ORB engine, wraps it in a new v2 workbench flow,
            and lays out the catalog for the broader strategy roadmap from the PRD.
          </p>
        </div>
        <div className="flex gap-3 flex-wrap">
          <button className="wb-primary-button" onClick={() => navigate('/workbench/run')}>
            Start a run
          </button>
          <button className="wb-secondary-button" onClick={() => navigate('/workbench/strategies')}>
            Browse catalog
          </button>
        </div>
      </section>

      <section className="wb-grid wb-grid-4 mt-6">
        <MetricTile label="Live Strategies" value={metrics.available_strategies ?? 0} hint="Executable end to end today" />
        <MetricTile label="Catalogued Strategies" value={(metrics.available_strategies ?? 0) + (metrics.planned_strategies ?? 0)} hint="Includes planned and research tracks" />
        <MetricTile label="Paper Replays" value={metrics.paper_sessions ?? 0} hint="User-owned interactive sessions" />
        <MetricTile label="Historical Batches" value={metrics.historical_batches ?? 0} hint={`${metrics.historical_sessions ?? 0} child sessions tracked`} />
      </section>

      <section className="wb-grid wb-grid-2 mt-6">
        <div className="wb-card p-5">
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="wb-kicker">Featured</div>
              <h2 className="text-lg font-semibold text-[var(--text-primary)]">What is live in this build</h2>
            </div>
            <Link className="wb-link" to="/workbench/strategies">Full catalog</Link>
          </div>

          <div className="mt-4 space-y-3">
            {(summary?.featured_strategies || []).map(strategy => {
              const tone = strategyStatusTone(strategy.status)
              return (
                <div key={strategy.id} className="rounded-2xl p-4 border" style={{ borderColor: 'var(--border)', background: 'rgba(10, 17, 30, 0.55)' }}>
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <div className="text-base font-semibold text-[var(--text-primary)]">{strategy.name}</div>
                      <div className="text-sm mt-1 wb-muted">{strategy.playbook}</div>
                    </div>
                    <span className="wb-chip" style={{ background: tone.background, borderColor: tone.border, color: tone.color }}>
                      {tone.label}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-2 mt-3">
                    {(strategy.chips || []).map(chip => (
                      <span key={chip} className="wb-chip">{chip}</span>
                    ))}
                  </div>
                  <div className="mt-4">
                    <button className="wb-secondary-button" onClick={() => navigate(`/workbench/run?strategy=${strategy.id}`)}>
                      Build run
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        </div>

        <div className="wb-card p-5">
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="wb-kicker">Data Readiness</div>
              <h2 className="text-lg font-semibold text-[var(--text-primary)]">Historical warehouse status</h2>
            </div>
            <span className="wb-chip">{readiness.ready_days ?? 0} ready days</span>
          </div>

          <div className="mt-5 space-y-4">
            <div className="wb-stat-row">
              <span>Total catalogue days</span>
              <strong>{readiness.total_days ?? 0}</strong>
            </div>
            <div className="wb-stat-row">
              <span>Backtest-ready days</span>
              <strong>{readiness.ready_days ?? 0}</strong>
            </div>
            <div className="wb-stat-row">
              <span>Latest ready day</span>
              <strong>{readiness.latest_ready_day || '—'}</strong>
            </div>
            <div className="pt-2">
              <div className="text-[11px] uppercase tracking-[0.24em] wb-muted">Ingestion breakdown</div>
              <div className="flex flex-wrap gap-2 mt-3">
                {Object.entries(readiness.ingestion_status_breakdown || {}).map(([key, value]) => (
                  <span key={key} className="wb-chip">
                    {key}: {value}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="wb-card p-5 mt-6">
        <div className="flex items-center justify-between gap-4">
          <div>
            <div className="wb-kicker">Activity</div>
            <h2 className="text-lg font-semibold text-[var(--text-primary)]">Recent runs</h2>
          </div>
          <button className="wb-secondary-button" onClick={() => navigate('/workbench/history')}>
            Open history
          </button>
        </div>

        <div className="mt-4 grid gap-3">
          {(summary?.recent_runs || []).map(run => {
            const tone = runStatusTone(run.status)
            return (
              <button
                key={`${run.kind}:${run.id}`}
                className="text-left rounded-2xl p-4 border transition-all hover:-translate-y-0.5"
                style={{ borderColor: 'var(--border)', background: 'rgba(10, 17, 30, 0.55)' }}
                onClick={() => navigate(run.route)}
              >
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="text-base font-semibold text-[var(--text-primary)]">{run.title}</div>
                    <div className="text-sm mt-1 wb-muted">
                      {runKindLabel(run.kind)} · {run.subtitle}
                    </div>
                  </div>
                  <span className="wb-chip" style={{ background: tone.background, borderColor: tone.border, color: tone.color }}>
                    {run.status}
                  </span>
                </div>
                <div className="mt-4 flex flex-wrap gap-6 text-sm">
                  <div>
                    <div className="wb-muted text-[11px] uppercase tracking-[0.18em]">P/L</div>
                    <div style={{ color: run.pnl == null ? 'var(--text-secondary)' : run.pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                      {fmtINR(run.pnl)}
                    </div>
                  </div>
                  <div>
                    <div className="wb-muted text-[11px] uppercase tracking-[0.18em]">Summary</div>
                    <div className="text-[var(--text-primary)]">{run.summary}</div>
                  </div>
                  <div>
                    <div className="wb-muted text-[11px] uppercase tracking-[0.18em]">Created</div>
                    <div className="text-[var(--text-primary)]">{fmtDateTime(run.created_at)}</div>
                  </div>
                </div>
              </button>
            )
          })}
        </div>
      </section>
    </div>
  )
}
