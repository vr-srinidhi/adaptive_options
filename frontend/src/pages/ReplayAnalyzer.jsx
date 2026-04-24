import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { getWorkbenchReplay } from '../api'
import { fmtDateTime, fmtINR, fmtNumber, runKindLabel, runStatusTone } from '../utils/workbench'

const timeLabel = value => value ? value.slice(11, 16) : '—'

function AnalyzerChart({ title, data, lineKey, color, valueFormatter }) {
  if (!data?.length) {
    return (
      <div className="wb-card p-5">
        <div className="wb-kicker">{title}</div>
        <div className="mt-4 text-sm wb-muted">No data available for this chart.</div>
      </div>
    )
  }

  return (
    <div className="wb-card p-5">
      <div className="wb-kicker">{title}</div>
      <div className="mt-4 h-64">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#213047" vertical={false} />
            <XAxis dataKey="label" tick={{ fill: '#8090aa', fontSize: 11 }} tickLine={false} axisLine={{ stroke: '#27364b' }} />
            <YAxis
              tick={{ fill: '#8090aa', fontSize: 11 }}
              tickLine={false}
              axisLine={false}
              width={82}
              tickFormatter={valueFormatter}
            />
            <Tooltip
              formatter={value => [valueFormatter(value), title]}
              contentStyle={{ background: '#0f1726', border: '1px solid #27364b', borderRadius: 12, fontSize: 11 }}
              labelStyle={{ color: '#b8c7de' }}
            />
            <Line type="monotone" dataKey={lineKey} stroke={color} strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

function StrategyRunAnalyzer({ payload, kind, id, navigate }) {
  const run = payload?.run || {}
  const legs = payload?.legs || []
  const spotSeries = payload?.spot_series || []
  const mtmSeries = payload?.mtm_series || []
  const events = payload?.events || []

  const [eventsOnly, setEventsOnly] = useState(false)

  const visibleEvents = useMemo(() => {
    if (!eventsOnly) return events
    return events.filter(e => e.event_type !== 'HOLD')
  }, [events, eventsOnly])

  const chartSpot = useMemo(
    () => spotSeries.map(r => ({ label: timeLabel(r.timestamp), spot: Number(r.close) })),
    [spotSeries]
  )
  const chartMtm = useMemo(
    () => mtmSeries.map(r => ({ label: timeLabel(r.timestamp), net_mtm: Number(r.net_mtm ?? 0) })),
    [mtmSeries]
  )

  const tone = runStatusTone(run.status)
  const pnl = run.realized_net_pnl

  return (
    <div className="wb-page">
      <section className="wb-card p-6">
        <div className="flex items-start justify-between gap-5 flex-wrap">
          <div>
            <div className="flex items-center gap-3 flex-wrap">
              <button className="wb-link" onClick={() => navigate('/workbench/history')}>← Back</button>
              <span className="wb-chip">Session Backtest</span>
              <span className="wb-chip" style={{ background: tone.background, borderColor: tone.border, color: tone.color }}>
                {run.status}
              </span>
            </div>
            <h1 className="mt-4 text-3xl font-semibold text-[var(--text-primary)]">
              {run.instrument} · {run.trade_date}
            </h1>
            <p className="mt-2 text-sm wb-muted">
              {run.strategy_id} · exit: {run.exit_reason || '—'} · {events.length} events
            </p>
          </div>
          <div className="text-right">
            <div className="wb-kicker">Net P/L</div>
            <div className="mt-2 text-4xl font-semibold" style={{ color: pnl == null ? 'var(--text-secondary)' : pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
              {fmtINR(pnl)}
            </div>
          </div>
        </div>
      </section>

      <section className="wb-grid wb-grid-3 mt-6">
        <div className="wb-card p-4">
          <div className="wb-kicker">Execution</div>
          <div className="mt-3 space-y-3 text-sm">
            <div className="wb-stat-row"><span>Capital</span><strong>{fmtINR(run.capital)}</strong></div>
            <div className="wb-stat-row"><span>Lots</span><strong>{run.lots}</strong></div>
            <div className="wb-stat-row"><span>Lot size</span><strong>{run.lot_size}</strong></div>
            <div className="wb-stat-row"><span>Entry time</span><strong>{run.entry_time || '—'}</strong></div>
            <div className="wb-stat-row"><span>Exit time</span><strong>{run.exit_time || '—'}</strong></div>
          </div>
        </div>
        <div className="wb-card p-4">
          <div className="wb-kicker">P&amp;L Breakdown</div>
          <div className="mt-3 space-y-3 text-sm">
            <div className="wb-stat-row"><span>Entry credit</span><strong>{fmtINR(run.entry_credit_total)}</strong></div>
            <div className="wb-stat-row"><span>Gross P/L</span><strong>{fmtINR(run.gross_pnl)}</strong></div>
            <div className="wb-stat-row"><span>Total charges</span><strong>{fmtINR(run.total_charges)}</strong></div>
            <div className="wb-stat-row"><span>Net P/L</span><strong>{fmtINR(run.realized_net_pnl)}</strong></div>
          </div>
        </div>
        <div className="wb-card p-4">
          <div className="wb-kicker">Legs</div>
          <div className="mt-3 space-y-2">
            {legs.map((leg, i) => (
              <div key={i} className="rounded-xl p-3" style={{ background: 'rgba(8,13,23,0.45)', border: '1px solid rgba(148,163,184,0.12)' }}>
                <div className="flex items-center justify-between text-sm">
                  <span className="font-medium text-[var(--text-primary)]">{leg.side} {leg.option_type} {leg.strike}</span>
                  <span className="wb-muted">{leg.expiry_date}</span>
                </div>
                <div className="mt-1 text-sm wb-muted">
                  Entry {fmtINR(leg.entry_price)} · Exit {fmtINR(leg.exit_price)} · P/L {fmtINR(leg.gross_leg_pnl)}
                </div>
              </div>
            ))}
            {legs.length === 0 && <div className="text-sm wb-muted">No trade entered.</div>}
          </div>
        </div>
      </section>

      <section className="wb-grid wb-grid-2 mt-6">
        <AnalyzerChart title="NIFTY spot (1-min)" data={chartSpot} lineKey="spot" color="#38bdf8" valueFormatter={v => fmtNumber(v, 0)} />
        <AnalyzerChart title="Net MTM progression" data={chartMtm} lineKey="net_mtm" color="#36b37e" valueFormatter={v => fmtINR(v)} />
      </section>

      <section className="wb-card p-5 mt-6">
        <div className="flex items-center justify-between gap-4 mb-4">
          <div>
            <div className="wb-kicker">Event log</div>
            <h2 className="text-lg font-semibold text-[var(--text-primary)] mt-1">{events.length} strategy events</h2>
          </div>
          <button className="wb-secondary-button" onClick={() => setEventsOnly(prev => !prev)}>
            {eventsOnly ? 'Show all events' : 'Events only'}
          </button>
        </div>
        <div className="overflow-auto">
          <table className="w-full text-sm">
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                {['Time', 'Type', 'Reason', 'Detail'].map(h => (
                  <th key={h} className="text-left py-2 pr-4 wb-muted text-[11px] uppercase tracking-[0.18em]">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visibleEvents.map((ev, i) => (
                <tr key={i} style={{ borderBottom: '1px solid rgba(39,54,75,0.45)' }}>
                  <td className="py-2 pr-4 text-[var(--text-primary)]">{timeLabel(ev.timestamp)}</td>
                  <td className="py-2 pr-4 font-semibold" style={{ color: ev.event_type === 'ENTRY' ? 'var(--green)' : ev.event_type?.includes('EXIT') ? 'var(--red)' : 'var(--text-secondary)' }}>{ev.event_type}</td>
                  <td className="py-2 pr-4 wb-muted">{ev.reason_code || '—'}</td>
                  <td className="py-2 pr-4 wb-muted">{ev.reason_text || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  )
}

export default function ReplayAnalyzer() {
  const navigate = useNavigate()
  const { kind, id } = useParams()
  const [payload, setPayload] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [eventsOnly, setEventsOnly] = useState(true)

  useEffect(() => {
    getWorkbenchReplay(kind, id)
      .then(res => setPayload(res.data))
      .catch(err => setError(err.response?.data?.detail || err.message))
      .finally(() => setLoading(false))
  }, [kind, id])

  const session = payload?.session
  const trade = payload?.trade
  const decisions = payload?.decisions || []
  const marks = payload?.marks || []
  const explainability = payload?.explainability || {}

  const visibleDecisions = useMemo(() => {
    if (!eventsOnly) return decisions
    return decisions.filter(item => item.action && item.action !== 'HOLD')
  }, [decisions, eventsOnly])

  const spotSeries = useMemo(
    () => decisions
      .filter(item => item.timestamp && item.spot_close != null)
      .map(item => ({
        label: timeLabel(item.timestamp),
        spot: Number(item.spot_close),
      })),
    [decisions]
  )

  const pnlSeries = useMemo(
    () => marks
      .filter(item => item.timestamp && (item.estimated_net_mtm != null || item.total_mtm != null))
      .map(item => ({
        label: timeLabel(item.timestamp),
        pnl: Number(item.estimated_net_mtm ?? item.total_mtm),
      })),
    [marks]
  )

  if (loading) {
    return (
      <div className="flex items-center justify-center h-72 gap-2 wb-muted">
        <span className="spinner" /> Loading analyzer…
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

  if (!payload) return null

  // Generic strategy run — different payload shape
  if (kind === 'strategy_run') {
    return <StrategyRunAnalyzer payload={payload} kind={kind} id={id} navigate={navigate} />
  }

  if (!session) return null

  const tone = runStatusTone(session.status)
  const legacyRoute = kind === 'paper_session' ? `/paper/session/${id}` : `/backtests/sessions/${id}`
  const backRoute = kind === 'paper_session' ? '/workbench/replay' : session.batch_id ? `/workbench/history/historical_batch/${session.batch_id}` : '/workbench/history'

  return (
    <div className="wb-page">
      <section className="wb-card p-6">
        <div className="flex items-start justify-between gap-5 flex-wrap">
          <div>
            <div className="flex items-center gap-3 flex-wrap">
              <button className="wb-link" onClick={() => navigate(backRoute)}>← Back</button>
              <span className="wb-chip">{runKindLabel(kind)}</span>
              <span className="wb-chip" style={{ background: tone.background, borderColor: tone.border, color: tone.color }}>
                {session.status}
              </span>
            </div>
            <h1 className="mt-4 text-3xl font-semibold text-[var(--text-primary)]">
              {session.instrument} · {session.session_date}
            </h1>
            <p className="mt-2 text-sm wb-muted">
              {session.final_session_state || session.status} · {decisions.length} decisions · {marks.length} marks
            </p>
          </div>

          <div className="text-right">
            <div className="wb-kicker">Net P/L</div>
            <div className="mt-2 text-4xl font-semibold" style={{ color: session.summary_pnl == null ? 'var(--text-secondary)' : session.summary_pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
              {fmtINR(session.summary_pnl)}
            </div>
            <div className="mt-4 flex gap-2 justify-end flex-wrap">
              <Link to={legacyRoute} className="wb-secondary-button">Open detailed report</Link>
            </div>
          </div>
        </div>
      </section>

      <section className="wb-grid wb-grid-3 mt-6">
        <div className="wb-card p-4">
          <div className="wb-kicker">Execution</div>
          <div className="mt-3 space-y-3 text-sm">
            <div className="wb-stat-row"><span>Capital</span><strong>{fmtINR(session.capital)}</strong></div>
            <div className="wb-stat-row"><span>Session type</span><strong>{session.session_type}</strong></div>
            <div className="wb-stat-row"><span>Source mode</span><strong>{session.source_mode}</strong></div>
            <div className="wb-stat-row"><span>Created</span><strong>{fmtDateTime(session.created_at)}</strong></div>
          </div>
        </div>
        <div className="wb-card p-4">
          <div className="wb-kicker">Trade</div>
          <div className="mt-3 space-y-3 text-sm">
            <div className="wb-stat-row"><span>Status</span><strong>{trade?.status || 'No trade'}</strong></div>
            <div className="wb-stat-row"><span>Bias</span><strong>{trade?.bias || '—'}</strong></div>
            <div className="wb-stat-row"><span>Entry debit</span><strong>{fmtINR(trade?.entry_debit)}</strong></div>
            <div className="wb-stat-row"><span>Exit reason</span><strong>{trade?.exit_reason || explainability.no_trade_reason || '—'}</strong></div>
          </div>
        </div>
        <div className="wb-card p-4">
          <div className="wb-kicker">Explainability</div>
          <div className="mt-3 space-y-3 text-sm">
            <div className="wb-stat-row"><span>Entry reason</span><strong>{trade?.entry_reason_code || '—'}</strong></div>
            <div className="wb-stat-row"><span>Exit reason</span><strong>{explainability.exit_reason || '—'}</strong></div>
            <div className="wb-stat-row"><span>No-trade reason</span><strong>{explainability.no_trade_reason || '—'}</strong></div>
            <div className="wb-stat-row"><span>Signals logged</span><strong>{Object.keys(explainability.action_counts || {}).length}</strong></div>
          </div>
        </div>
      </section>

      <section className="wb-grid wb-grid-2 mt-6">
        <AnalyzerChart title="Spot progression" data={spotSeries} lineKey="spot" color="#38bdf8" valueFormatter={value => fmtNumber(value, 0)} />
        <AnalyzerChart title="Net MTM progression" data={pnlSeries} lineKey="pnl" color="#36b37e" valueFormatter={value => fmtINR(value)} />
      </section>

      <section className="wb-grid wb-grid-2 mt-6">
        <div className="wb-card p-5">
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="wb-kicker">Decision stream</div>
              <h2 className="text-lg font-semibold text-[var(--text-primary)] mt-1">Minute audit ledger</h2>
            </div>
            <button className="wb-secondary-button" onClick={() => setEventsOnly(prev => !prev)}>
              {eventsOnly ? 'Show full minute log' : 'Show event-only log'}
            </button>
          </div>

          <div className="mt-4 overflow-auto">
            <table className="w-full text-sm">
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  {['Time', 'Action', 'Spot', 'Gate', 'State', 'Reason'].map(header => (
                    <th key={header} className="text-left py-2 pr-4 wb-muted text-[11px] uppercase tracking-[0.18em]">{header}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visibleDecisions.map(row => (
                  <tr key={row.id} style={{ borderBottom: '1px solid rgba(39, 54, 75, 0.45)' }}>
                    <td className="py-2 pr-4 text-[var(--text-primary)]">{timeLabel(row.timestamp)}</td>
                    <td className="py-2 pr-4 text-[var(--text-primary)]">{row.action || '—'}</td>
                    <td className="py-2 pr-4 text-[var(--text-primary)]">{fmtNumber(row.spot_close, 0)}</td>
                    <td className="py-2 pr-4 wb-muted">{row.rejection_gate || '—'}</td>
                    <td className="py-2 pr-4 wb-muted">{row.session_state || '—'}</td>
                    <td className="py-2 pr-4 wb-muted">{row.reason_code || row.reason_text || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="wb-card p-5">
          <div className="wb-kicker">Frozen assumptions</div>
          <h2 className="text-lg font-semibold text-[var(--text-primary)] mt-1">Strategy snapshot</h2>
          <pre className="mt-4 rounded-2xl p-4 overflow-auto text-xs" style={{ background: '#09111f', border: '1px solid var(--border)', color: '#b8c7de' }}>
            {JSON.stringify(session.strategy_config_snapshot || trade?.strategy_params_json || {}, null, 2)}
          </pre>

          {trade?.legs?.length > 0 && (
            <div className="mt-5">
              <div className="wb-kicker">Legs</div>
              <div className="mt-3 space-y-2">
                {trade.legs.map(leg => (
                  <div key={`${leg.leg_side}-${leg.option_type}-${leg.strike}`} className="rounded-2xl p-3 border" style={{ borderColor: 'rgba(148,163,184,0.12)', background: 'rgba(8, 13, 23, 0.45)' }}>
                    <div className="flex items-center justify-between gap-3">
                      <div className="text-sm font-medium text-[var(--text-primary)]">
                        {leg.leg_side} {leg.option_type} {leg.strike}
                      </div>
                      <div className="text-sm wb-muted">{leg.expiry || '—'}</div>
                    </div>
                    <div className="mt-2 text-sm wb-muted">
                      Entry {fmtINR(leg.entry_price)} · Exit {fmtINR(leg.exit_price)}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </section>
    </div>
  )
}
