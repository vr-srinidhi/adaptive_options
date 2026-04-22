import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getWorkbenchRuns } from '../api'
import { fmtDateTime, fmtINR, fmtShortDate, runStatusTone } from '../utils/workbench'

const PALETTE = {
  bg: 'var(--surface)',
  card: 'var(--surface-secondary)',
  border: 'var(--border)',
  text: 'var(--text-primary)',
  muted: 'var(--text-secondary)',
  blue: '#3b82f6',
  blueSoft: 'rgba(59,130,246,0.12)',
  green: '#22c55e',
  greenSoft: 'rgba(34,197,94,0.12)',
  amber: '#f59e0b',
  amberSoft: 'rgba(245,158,11,0.12)',
}

const WORKFLOW = [
  { step: '01', title: 'Select Session', subtitle: 'Choose a replayable paper run', color: PALETTE.blue },
  { step: '02', title: 'Inspect Outcome', subtitle: 'Review P&L, decisions, and status', color: PALETTE.amber },
  { step: '03', title: 'Open Analyzer', subtitle: 'Move into full minute-by-minute replay', color: PALETTE.green },
]

function StatusBadge({ status }) {
  const tone = runStatusTone(status)
  return (
    <span
      className="inline-flex items-center rounded-md px-2.5 py-1 font-semibold"
      style={{
        background: tone.background,
        border: `1px solid ${tone.border}`,
        color: tone.color,
        fontSize: 10,
      }}
    >
      {status}
    </span>
  )
}

function StatRow({ label, value, color }) {
  return (
    <div
      className="flex items-center justify-between py-2"
      style={{ borderBottom: '0.5px solid rgba(26,37,64,0.9)' }}
    >
      <span style={{ color: PALETTE.muted, fontSize: 10 }}>{label}</span>
      <span className="font-semibold" style={{ color: color || PALETTE.text, fontSize: 11 }}>
        {value}
      </span>
    </div>
  )
}

function WorkflowStep({ step, index }) {
  return (
    <div>
      {index > 0 ? <div style={{ width: 1, height: 10, background: PALETTE.border, marginLeft: 11 }} /> : null}
      <div className="flex items-center gap-3 py-1.5">
        <div
          className="inline-flex h-[22px] w-[22px] items-center justify-center rounded-full font-bold"
          style={{
            color: step.color,
            background: `${step.color}18`,
            border: `1px solid ${step.color}40`,
            fontSize: 9,
          }}
        >
          {step.step}
        </div>
        <div>
          <div className="font-semibold" style={{ color: PALETTE.text, fontSize: 11 }}>{step.title}</div>
          <div style={{ color: '#64748b', fontSize: 9 }}>{step.subtitle}</div>
        </div>
      </div>
    </div>
  )
}

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

  const latest = runs[0] || null

  const replayStats = useMemo(() => {
    const completed = runs.filter(run => typeof run.pnl === 'number')
    const avgPnl = completed.length
      ? completed.reduce((sum, run) => sum + run.pnl, 0) / completed.length
      : null
    return {
      total: runs.length,
      completed: completed.length,
      avgPnl,
      latestCreated: latest?.created_at ? fmtDateTime(latest.created_at) : '—',
    }
  }, [latest?.created_at, runs])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-72 gap-2" style={{ color: PALETTE.muted }}>
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
    <div className="mx-auto max-w-[1360px]" style={{ padding: '18px 20px 0', fontSize: 12 }}>
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_240px]">
        <div className="space-y-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div style={{ fontSize: 15, fontWeight: 700, color: PALETTE.text, marginBottom: 3 }}>
                Replay Desk
              </div>
              <div style={{ fontSize: 10, color: PALETTE.muted }}>
                Paper sessions ready for replay and analyzer hand-off.
              </div>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => navigate('/workbench/run')}
                className="rounded-md px-3 py-1.5"
                style={{
                  background: 'rgba(59,130,246,0.15)',
                  border: '1px solid rgba(59,130,246,0.35)',
                  color: PALETTE.blue,
                  fontSize: 10,
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
              >
                New Replay
              </button>
              <button
                type="button"
                onClick={() => navigate('/workbench/history')}
                className="rounded-md px-3 py-1.5"
                style={{
                  background: PALETTE.card,
                  border: `1px solid ${PALETTE.border}`,
                  color: PALETTE.muted,
                  fontSize: 10,
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
              >
                Open History
              </button>
            </div>
          </div>

          {latest ? (
            <section
              className="rounded-[10px]"
              style={{ background: PALETTE.card, border: `1px solid ${PALETTE.border}`, padding: 14 }}
            >
              <div className="flex items-center gap-3" style={{ borderBottom: `0.5px solid ${PALETTE.border}`, paddingBottom: 10 }}>
                <div style={{ fontSize: 13, fontWeight: 700, color: PALETTE.text }}>
                  {latest.strategy_name || latest.title}
                </div>
                <span
                  className="inline-flex items-center rounded-md px-2 py-1"
                  style={{
                    background: PALETTE.bg,
                    border: `1px solid ${PALETTE.border}`,
                    color: PALETTE.muted,
                    fontSize: 9,
                  }}
                >
                  {latest.instrument || '—'} · {fmtShortDate(latest.date_label || latest.subtitle)}
                </span>
                <div style={{ marginLeft: 'auto', fontSize: 10, fontWeight: 600, color: PALETTE.blue }}>
                  {latest.created_at ? fmtDateTime(latest.created_at) : '—'}
                </div>
              </div>

              <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_180px]" style={{ paddingTop: 12 }}>
                <div
                  className="rounded-[8px]"
                  style={{ background: PALETTE.bg, border: `1px solid ${PALETTE.border}`, padding: '12px 14px' }}
                >
                  <div
                    className="mb-3 text-[10px] uppercase tracking-[0.08em]"
                    style={{ color: PALETTE.muted, fontWeight: 500 }}
                  >
                    Latest Session
                  </div>
                  <div className="grid gap-3 sm:grid-cols-3">
                    <div>
                      <div style={{ fontSize: 9, color: '#64748b' }}>Net P&amp;L</div>
                      <div style={{ fontSize: 12, fontWeight: 700, color: latest.pnl == null ? PALETTE.muted : latest.pnl >= 0 ? PALETTE.green : '#ef4444' }}>
                        {fmtINR(latest.pnl)}
                      </div>
                    </div>
                    <div>
                      <div style={{ fontSize: 9, color: '#64748b' }}>Decisions</div>
                      <div style={{ fontSize: 12, fontWeight: 700, color: PALETTE.text }}>{latest.metrics?.decision_count ?? '—'}</div>
                    </div>
                    <div>
                      <div style={{ fontSize: 9, color: '#64748b' }}>Status</div>
                      <div className="mt-1">
                        <StatusBadge status={latest.status} />
                      </div>
                    </div>
                  </div>
                </div>

                <div
                  className="rounded-[8px]"
                  style={{
                    background: PALETTE.bg,
                    border: `1px solid ${PALETTE.border}`,
                    padding: 12,
                    display: 'flex',
                    flexDirection: 'column',
                    gap: 8,
                  }}
                >
                  <div style={{ fontSize: 9, color: '#64748b' }}>Next step</div>
                  <button
                    type="button"
                    onClick={() => navigate(latest.route)}
                    className="rounded-md px-3 py-2"
                    style={{
                      background: '#2563eb',
                      color: '#fff',
                      fontSize: 11,
                      fontWeight: 700,
                      border: 'none',
                      cursor: 'pointer',
                    }}
                  >
                    Open Analyzer →
                  </button>
                  {latest.legacy_route ? (
                    <button
                      type="button"
                      onClick={() => navigate(latest.legacy_route)}
                      className="rounded-md px-3 py-2"
                      style={{
                        background: PALETTE.card,
                        border: `1px solid ${PALETTE.border}`,
                        color: PALETTE.muted,
                        fontSize: 10,
                        fontWeight: 600,
                        cursor: 'pointer',
                      }}
                    >
                      Legacy View
                    </button>
                  ) : null}
                </div>
              </div>
            </section>
          ) : null}

          <section
            className="rounded-[10px] overflow-hidden"
            style={{ background: PALETTE.card, border: `1px solid ${PALETTE.border}` }}
          >
            <div
              className="flex items-center justify-between"
              style={{ borderBottom: `0.5px solid ${PALETTE.border}`, padding: '11px 14px' }}
            >
              <div
                className="text-[10px] uppercase tracking-[0.08em]"
                style={{ color: PALETTE.muted, fontWeight: 500 }}
              >
                Replayable Sessions
              </div>
              <div style={{ fontSize: 10, color: PALETTE.muted }}>{runs.length} available</div>
            </div>

            {runs.length === 0 ? (
              <div style={{ padding: '28px 18px', textAlign: 'center' }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: PALETTE.text }}>No replayable sessions yet</div>
                <div style={{ marginTop: 6, fontSize: 10, color: PALETTE.muted }}>
                  Launch the first ORB paper replay from the builder.
                </div>
              </div>
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                <thead>
                  <tr style={{ background: PALETTE.bg }}>
                    {['Strategy', 'Date', 'Instrument', 'Net P&L', 'Status', ''].map(header => (
                      <th
                        key={header}
                        style={{
                          textAlign: 'left',
                          padding: '7px 14px',
                          fontSize: 9,
                          fontWeight: 500,
                          textTransform: 'uppercase',
                          letterSpacing: '0.06em',
                          color: '#64748b',
                        }}
                      >
                        {header}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {runs.map(run => (
                    <tr key={run.id} style={{ borderTop: `0.5px solid ${PALETTE.border}` }}>
                      <td style={{ padding: '8px 14px', fontWeight: 600, color: PALETTE.text }}>
                        {run.strategy_name || run.title}
                      </td>
                      <td style={{ padding: '8px 14px', color: PALETTE.muted }}>
                        {fmtShortDate(run.date_label || run.subtitle)}
                      </td>
                      <td style={{ padding: '8px 14px', color: PALETTE.muted }}>{run.instrument || '—'}</td>
                      <td
                        style={{
                          padding: '8px 14px',
                          fontWeight: 600,
                          color: run.pnl == null ? PALETTE.muted : run.pnl >= 0 ? PALETTE.green : '#ef4444',
                        }}
                      >
                        {fmtINR(run.pnl)}
                      </td>
                      <td style={{ padding: '8px 14px' }}>
                        <StatusBadge status={run.status} />
                      </td>
                      <td style={{ padding: '8px 14px' }}>
                        <button
                          type="button"
                          onClick={() => navigate(run.route)}
                          style={{ color: PALETTE.blue, background: 'transparent', cursor: 'pointer', fontSize: 10, fontWeight: 600 }}
                        >
                          Open →
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </div>

        <div className="space-y-4">
          <section
            className="rounded-[10px]"
            style={{ background: PALETTE.card, border: `1px solid ${PALETTE.border}`, padding: 14 }}
          >
            <div className="mb-2 text-[10px] uppercase tracking-[0.08em]" style={{ color: PALETTE.muted, fontWeight: 500 }}>
              Replay Stats
            </div>
            <StatRow label="Total Sessions" value={String(replayStats.total)} />
            <StatRow label="Completed" value={String(replayStats.completed)} color={PALETTE.green} />
            <StatRow label="Avg Net P&L" value={fmtINR(replayStats.avgPnl)} color={replayStats.avgPnl >= 0 ? PALETTE.green : '#ef4444'} />
            <StatRow label="Latest Created" value={replayStats.latestCreated} />
          </section>

          <section
            className="rounded-[10px]"
            style={{ background: PALETTE.card, border: `1px solid ${PALETTE.border}`, padding: 14 }}
          >
            <div className="mb-4 text-[10px] uppercase tracking-[0.08em]" style={{ color: PALETTE.muted, fontWeight: 500 }}>
              Replay Workflow
            </div>
            {WORKFLOW.map((step, index) => (
              <WorkflowStep key={step.step} step={step} index={index} />
            ))}
          </section>
        </div>
      </div>

      <div
        className="text-center"
        style={{ color: '#475569', fontSize: 10, padding: '7px 0', marginTop: 18, borderTop: '0.5px solid #1a2540' }}
      >
        For educational and backtesting purposes only · Not financial advice
      </div>
    </div>
  )
}
