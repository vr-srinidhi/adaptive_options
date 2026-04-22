import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getWorkbenchRuns, getWorkbenchSummary } from '../api'
import { fmtINR } from '../utils/workbench'

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
  red: '#ef4444',
  redSoft: 'rgba(239,68,68,0.12)',
  purple: '#8b5cf6',
  purpleSoft: 'rgba(139,92,246,0.12)',
}

const PAYOFF_SHAPES = {
  adaptive: '4,88 30,88 44,40 58,40 72,88 96,88',
  'short-strangle': '4,88 26,88 40,26 58,26 72,88 96,88',
  'bull-put': '4,34 34,34 54,88 96,88',
  'iron-condor': '4,88 22,88 36,28 62,28 76,88 96,88',
}

const PINNED_STRATEGIES = [
  { id: 'short_strangle', name: 'Short Strangle', payoff: 'short-strangle' },
  { id: 'bull_put_spread', name: 'Bull Put Spread', payoff: 'bull-put' },
  { id: 'iron_condor', name: 'Iron Condor', payoff: 'iron-condor' },
]

const CORE_WORKFLOW = [
  { step: '01', title: 'Strategy Catalog', subtitle: 'Choose a template', color: PALETTE.blue },
  { step: '02', title: 'Run Builder', subtitle: 'Configure & validate', color: PALETTE.amber },
  { step: '03', title: 'Replay / Analyze', subtitle: 'Minute-by-minute P&L', color: PALETTE.green },
]

function formatWorkspaceDate(date) {
  return date.toLocaleDateString('en-IN', {
    weekday: 'long',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

function marketStatus(date) {
  const day = date.getDay()
  if (day === 0 || day === 6) return 'NSE market closed'
  const minutes = date.getHours() * 60 + date.getMinutes()
  return minutes >= 555 && minutes <= 930 ? 'NSE market open' : 'NSE market closed'
}

function shortDateLabel(value) {
  if (!value) return '—'
  return value.split(' → ')[0]
}

function rowResult(run) {
  if (run.pnl == null) return run.status || '—'
  return run.pnl >= 0 ? 'WIN' : 'LOSS'
}

function resultTone(result) {
  if (result === 'WIN') return { color: PALETTE.green, background: PALETTE.greenSoft, border: 'rgba(34,197,94,0.3)' }
  if (result === 'LOSS') return { color: PALETTE.red, background: PALETTE.redSoft, border: 'rgba(239,68,68,0.3)' }
  return { color: '#64748b', background: 'rgba(100,116,139,0.12)', border: 'rgba(100,116,139,0.25)' }
}

function MiniPayoff({ payoff, color = PALETTE.blue }) {
  const points = PAYOFF_SHAPES[payoff] || PAYOFF_SHAPES.adaptive
  return (
    <svg viewBox="0 0 100 54" width="84" height="38" aria-hidden="true">
      <line x1="4" y1="42" x2="96" y2="42" stroke="rgba(148,163,184,0.14)" strokeWidth="0.7" />
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function QuickStartTile({ label, subtitle, accent, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-[8px] text-left"
      style={{ background: accent.background, border: `1px solid ${accent.border}`, cursor: 'pointer', padding: '12px 14px' }}
    >
      <div className="font-semibold" style={{ color: accent.color, fontSize: 11 }}>
        {label} →
      </div>
      <div className="mt-1" style={{ color: '#64748b', fontSize: 10 }}>
        {subtitle}
      </div>
    </button>
  )
}

function HealthItem({ label, ok, note }) {
  const tone = ok
    ? { color: PALETTE.green, border: 'rgba(34,197,94,0.35)', background: PALETTE.greenSoft, icon: '✓' }
    : { color: PALETTE.amber, border: 'rgba(245,158,11,0.35)', background: PALETTE.amberSoft, icon: '!' }

  return (
    <div className="flex gap-3">
      <span
        className="mt-0.5 inline-flex h-5 w-5 items-center justify-center rounded-full text-[10px] font-semibold"
        style={{ color: tone.color, border: `1px solid ${tone.border}`, background: tone.background }}
      >
        {tone.icon}
      </span>
      <div>
        <div className="font-semibold" style={{ color: PALETTE.text, fontSize: 10 }}>{label}</div>
        <div className="mt-1" style={{ color: '#64748b', fontSize: 9 }}>{note}</div>
      </div>
    </div>
  )
}

function ResultBadge({ result }) {
  const tone = resultTone(result)
  return (
    <span
      className="inline-flex items-center rounded-md px-2.5 py-1 font-semibold"
      style={{ color: tone.color, background: tone.background, border: `1px solid ${tone.border}`, fontSize: 10 }}
    >
      {result}
    </span>
  )
}

function StatRow({ label, value, color }) {
  return (
    <div className="flex items-center justify-between py-2" style={{ borderBottom: '0.5px solid rgba(26,37,64,0.9)' }}>
      <span style={{ color: PALETTE.muted, fontSize: 10 }}>{label}</span>
      <span className="font-semibold" style={{ color: color || PALETTE.text, fontSize: 11 }}>{value}</span>
    </div>
  )
}

export default function WorkspaceHome() {
  const navigate = useNavigate()
  const [summary, setSummary] = useState(null)
  const [runs, setRuns] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    Promise.all([
      getWorkbenchSummary(),
      getWorkbenchRuns({ limit: 200 }),
    ])
      .then(([summaryRes, runsRes]) => {
        setSummary(summaryRes.data)
        setRuns(runsRes.data?.runs || [])
      })
      .catch(err => setError(err.response?.data?.detail || err.message))
      .finally(() => setLoading(false))
  }, [])

  const now = new Date()
  const recentRuns = summary?.recent_runs || []
  const readiness = summary?.data_readiness || {}

  const quickStart = useMemo(() => {
    const latest = recentRuns[0]
    return [
      {
        label: 'New Replay',
        subtitle: 'Pick strategy → configure → run',
        accent: { color: PALETTE.blue, background: PALETTE.blueSoft, border: 'rgba(59,130,246,0.28)' },
        action: () => navigate('/paper'),
      },
      {
        label: 'Resume Last Run',
        subtitle: latest ? `${latest.strategy_name || latest.title} · ${shortDateLabel(latest.date_label || latest.subtitle)}` : 'Open most recent run',
        accent: { color: PALETTE.green, background: PALETTE.greenSoft, border: 'rgba(34,197,94,0.24)' },
        action: () => navigate(latest?.route || '/workbench/history'),
      },
      {
        label: 'Compare Recent',
        subtitle: `${Math.min(recentRuns.length, 2)} runs ready`,
        accent: { color: PALETTE.amber, background: PALETTE.amberSoft, border: 'rgba(245,158,11,0.24)' },
        action: () => navigate('/workbench/history'),
      },
      {
        label: 'New Intraday Backtest',
        subtitle: 'Choose date range',
        accent: { color: PALETTE.purple, background: PALETTE.purpleSoft, border: 'rgba(139,92,246,0.24)' },
        action: () => navigate('/backtest'),
      },
    ]
  }, [navigate, recentRuns])

  const healthItems = [
    { label: 'Spot candles', ok: (readiness.ready_days || 0) > 0, note: 'NIFTY · BANKNIFTY 1-min' },
    { label: 'Option chain', ok: true, note: '1-min LTP available' },
    { label: 'VIX data', ok: Boolean(readiness.latest_ready_day), note: readiness.latest_ready_day ? `Latest · ${readiness.latest_ready_day}` : 'Awaiting ready session' },
    { label: 'Bid/ask spread', ok: false, note: 'Fill proxy: candle close' },
    { label: 'Charges model', ok: true, note: 'Zerodha preset loaded' },
  ]

  const allTime = useMemo(() => {
    const completed = runs.filter(run => typeof run.pnl === 'number')
    const totalRuns = completed.length
    const wins = completed.filter(run => run.pnl >= 0).length
    const totalPnl = completed.reduce((sum, run) => sum + run.pnl, 0)
    const avgPnl = totalRuns ? totalPnl / totalRuns : 0
    return {
      totalRuns,
      winRate: totalRuns ? `${Math.round((wins / totalRuns) * 100)}%` : '—',
      totalPnl: totalRuns ? fmtINR(totalPnl) : '—',
      avgPnl: totalRuns ? fmtINR(avgPnl) : '—',
    }
  }, [runs])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-72 gap-2" style={{ color: PALETTE.muted }}>
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

  return (
    <div
      className="mx-auto max-w-[1360px]"
      style={{ padding: '22px 22px 0', fontSize: 12 }}
    >
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_280px]">
        <div className="space-y-4">
          <div>
            <div className="font-bold leading-none" style={{ color: PALETTE.text, fontSize: 16, marginBottom: 3 }}>
              Workspace
            </div>
            <div style={{ color: PALETTE.muted, fontSize: 11 }}>
              {formatWorkspaceDate(now)} · {marketStatus(now)}
            </div>
          </div>

          <section className="rounded-[10px]" style={{ background: PALETTE.card, border: `1px solid ${PALETTE.border}`, padding: 16 }}>
            <div className="mb-3 text-[10px] uppercase tracking-[0.08em]" style={{ color: PALETTE.muted, fontWeight: 500 }}>
              Quick Start
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              {quickStart.map(item => (
                <QuickStartTile
                  key={item.label}
                  label={item.label}
                  subtitle={item.subtitle}
                  accent={item.accent}
                  onClick={item.action}
                />
              ))}
            </div>
          </section>

          <section className="rounded-[10px] overflow-hidden" style={{ background: PALETTE.card, border: `1px solid ${PALETTE.border}` }}>
            <div className="flex items-center justify-between" style={{ borderBottom: `0.5px solid ${PALETTE.border}`, padding: '11px 14px' }}>
              <div className="text-[10px] uppercase tracking-[0.08em]" style={{ color: PALETTE.muted, fontWeight: 500 }}>
                Recent Runs
              </div>
              <button
                type="button"
                onClick={() => navigate('/workbench/history')}
                className="font-medium"
                style={{ color: PALETTE.blue, background: 'transparent', cursor: 'pointer', fontSize: 10 }}
              >
                View all →
              </button>
            </div>

            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead>
                <tr style={{ background: PALETTE.bg }}>
                  {['Strategy', 'Date', 'Instrument', 'Net P&L', 'Result', ''].map(header => (
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
                {recentRuns.slice(0, 3).map(run => {
                  const result = rowResult(run)
                  return (
                    <tr key={`${run.kind}:${run.id}`} style={{ borderTop: `0.5px solid ${PALETTE.border}` }}>
                      <td style={{ padding: '8px 14px', fontWeight: 600, color: PALETTE.text }}>{run.strategy_name || run.title}</td>
                      <td style={{ padding: '8px 14px', color: PALETTE.muted }}>{shortDateLabel(run.date_label || run.subtitle)}</td>
                      <td style={{ padding: '8px 14px', color: PALETTE.muted }}>{run.instrument || '—'}</td>
                      <td style={{ padding: '8px 14px', fontWeight: 600, color: run.pnl == null ? PALETTE.muted : run.pnl >= 0 ? PALETTE.green : PALETTE.red }}>
                        {fmtINR(run.pnl)}
                      </td>
                      <td style={{ padding: '8px 14px' }}><ResultBadge result={result} /></td>
                      <td style={{ padding: '8px 14px' }}>
                        <button
                          type="button"
                          onClick={() => navigate(run.route)}
                          style={{ color: '#475569', background: 'transparent', cursor: 'pointer', fontSize: 9 }}
                        >
                          Replay →
                        </button>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </section>

          <section className="rounded-[10px]" style={{ background: PALETTE.card, border: `1px solid ${PALETTE.border}`, padding: 14 }}>
            <div className="mb-3 flex items-center justify-between">
              <div className="text-[10px] uppercase tracking-[0.08em]" style={{ color: PALETTE.muted, fontWeight: 500 }}>
                Pinned Strategies
              </div>
              <button
                type="button"
                onClick={() => navigate('/workbench/strategies')}
                className="font-medium"
                style={{ color: PALETTE.blue, background: 'transparent', cursor: 'pointer', fontSize: 10 }}
              >
                Browse all →
              </button>
            </div>

            <div className="grid gap-3 md:grid-cols-3">
              {PINNED_STRATEGIES.map(item => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => navigate(`/workbench/run?strategy=${item.id}`)}
                  className="rounded-[8px] text-left"
                  style={{ background: PALETTE.bg, border: `1px solid ${PALETTE.border}`, cursor: 'pointer', padding: '10px 12px' }}
                >
                  <MiniPayoff
                    payoff={item.payoff}
                    color={item.id === 'bull_put_spread' ? PALETTE.green : PALETTE.blue}
                  />
                  <div className="mt-2 font-semibold" style={{ color: PALETTE.text, fontSize: 10 }}>{item.name}</div>
                  <div className="mt-2" style={{ color: PALETTE.blue, fontSize: 9 }}>Use →</div>
                </button>
              ))}
            </div>
          </section>
        </div>

        <div className="space-y-4">
          <section className="rounded-[10px]" style={{ background: PALETTE.card, border: `1px solid ${PALETTE.border}`, padding: 14 }}>
            <div className="mb-4 text-[10px] uppercase tracking-[0.08em]" style={{ color: PALETTE.muted, fontWeight: 500 }}>
              Data Ingestion Health
            </div>
            <div className="space-y-4">
              {healthItems.map(item => (
                <HealthItem key={item.label} {...item} />
              ))}
            </div>
          </section>

          <section className="rounded-[10px]" style={{ background: PALETTE.card, border: `1px solid ${PALETTE.border}`, padding: 14 }}>
            <div className="mb-4 text-[10px] uppercase tracking-[0.08em]" style={{ color: PALETTE.muted, fontWeight: 500 }}>
              Core Workflow
            </div>
            {CORE_WORKFLOW.map((step, index) => (
              <div key={step.step}>
                {index > 0 ? <div style={{ width: 1, height: 10, background: PALETTE.border, marginLeft: 11 }} /> : null}
                <div className="flex items-center gap-3 py-1.5">
                  <div
                    className="inline-flex h-[22px] w-[22px] items-center justify-center rounded-full font-bold"
                    style={{ color: step.color, background: `${step.color}18`, border: `1px solid ${step.color}40`, fontSize: 9 }}
                  >
                    {step.step}
                  </div>
                  <div>
                    <div className="font-semibold" style={{ color: PALETTE.text, fontSize: 11 }}>{step.title}</div>
                    <div style={{ color: '#64748b', fontSize: 9 }}>{step.subtitle}</div>
                  </div>
                </div>
              </div>
            ))}
          </section>

          <section className="rounded-[10px]" style={{ background: PALETTE.card, border: `1px solid ${PALETTE.border}`, padding: 14 }}>
            <div className="mb-2 text-[10px] uppercase tracking-[0.08em]" style={{ color: PALETTE.muted, fontWeight: 500 }}>
              All-Time
            </div>
            <StatRow label="Total Runs" value={String(allTime.totalRuns)} />
            <StatRow label="Win Rate" value={allTime.winRate} color={PALETTE.blue} />
            <StatRow label="Total Net P&L" value={allTime.totalPnl} color={PALETTE.green} />
            <StatRow label="Avg per Run" value={allTime.avgPnl} color={PALETTE.green} />
          </section>
        </div>
      </div>

      <div className="text-center" style={{ color: '#475569', fontSize: 10, padding: '7px 0', marginTop: 18, borderTop: '0.5px solid #1a2540' }}>
        For educational and backtesting purposes only · Not financial advice
      </div>
    </div>
  )
}
