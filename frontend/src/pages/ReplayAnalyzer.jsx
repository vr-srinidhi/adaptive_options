import { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  CartesianGrid,
  Label,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { getStrategyRunReplayCsvUrl, getWorkbenchReplay } from '../api'
import { fmtDateTime, fmtINR, fmtNumber, runKindLabel, runStatusTone } from '../utils/workbench'

const timeLabel = value => value ? value.slice(11, 16) : '—'

// ── Utility ──────────────────────────────────────────────────────────────────

function fmtExpiry(isoDate) {
  if (!isoDate) return '—'
  const [y, m, d] = isoDate.split('-')
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
  return `${d}-${months[parseInt(m, 10) - 1]}-${y}`
}

// ── Data Quality Banner ───────────────────────────────────────────────────────

function DataQualityBanner({ warnings }) {
  if (!warnings?.length) return null
  return (
    <div className="mb-4 rounded-xl px-4 py-3 text-sm flex flex-wrap gap-2 items-start"
         style={{ background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.3)', color: '#fbbf24' }}>
      <span className="font-semibold">Data warnings:</span>
      {warnings.map((w, i) => (
        <span key={i} className="opacity-80">{w.message}</span>
      ))}
    </div>
  )
}

// ── Execution Summary Card ────────────────────────────────────────────────────

function ExecutionSummaryCard({ run }) {
  const pnl = run.realized_net_pnl
  const pnlColor = pnl == null ? 'var(--text-secondary)' : pnl >= 0 ? 'var(--green)' : 'var(--red)'
  return (
    <div className="wb-card p-5">
      <div className="wb-kicker mb-3">Execution summary</div>
      <div className="grid gap-2 text-sm" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 1fr))' }}>
        <div className="rounded-xl p-3" style={{ background: 'rgba(8,13,23,0.5)', border: '1px solid rgba(148,163,184,0.1)' }}>
          <div className="wb-kicker text-[10px]">Capital</div>
          <div className="mt-1 font-semibold text-[var(--text-primary)]">{fmtINR(run.capital)}</div>
        </div>
        <div className="rounded-xl p-3" style={{ background: 'rgba(8,13,23,0.5)', border: '1px solid rgba(148,163,184,0.1)' }}>
          <div className="wb-kicker text-[10px]">Lots × Lot size</div>
          <div className="mt-1 font-semibold text-[var(--text-primary)]">{run.lots ?? '—'} × {run.lot_size ?? '—'}</div>
        </div>
        <div className="rounded-xl p-3" style={{ background: 'rgba(8,13,23,0.5)', border: '1px solid rgba(148,163,184,0.1)' }}>
          <div className="wb-kicker text-[10px]">Qty</div>
          <div className="mt-1 font-semibold text-[var(--text-primary)]">
            {run.lots && run.lot_size ? (run.lots * run.lot_size).toLocaleString() : '—'}
          </div>
        </div>
        <div className="rounded-xl p-3" style={{ background: 'rgba(8,13,23,0.5)', border: '1px solid rgba(148,163,184,0.1)' }}>
          <div className="wb-kicker text-[10px]">Entry → Exit</div>
          <div className="mt-1 font-semibold text-[var(--text-primary)]">{run.entry_time || '—'} → {run.exit_time || '—'}</div>
        </div>
        <div className="rounded-xl p-3" style={{ background: 'rgba(8,13,23,0.5)', border: '1px solid rgba(148,163,184,0.1)' }}>
          <div className="wb-kicker text-[10px]">Exit reason</div>
          <div className="mt-1 font-semibold text-[var(--text-primary)]">{run.exit_reason || '—'}</div>
        </div>
        <div className="rounded-xl p-3" style={{ background: 'rgba(8,13,23,0.5)', border: '1px solid rgba(148,163,184,0.1)' }}>
          <div className="wb-kicker text-[10px]">Net P/L</div>
          <div className="mt-1 font-semibold" style={{ color: pnlColor }}>{fmtINR(pnl)}</div>
        </div>
        <div className="rounded-xl p-3" style={{ background: 'rgba(8,13,23,0.5)', border: '1px solid rgba(148,163,184,0.1)' }}>
          <div className="wb-kicker text-[10px]">MFE (best)</div>
          <div className="mt-1 font-semibold" style={{ color: run.mfe >= 0 ? 'var(--green)' : 'var(--red)' }}>{fmtINR(run.mfe)}</div>
        </div>
        <div className="rounded-xl p-3" style={{ background: 'rgba(8,13,23,0.5)', border: '1px solid rgba(148,163,184,0.1)' }}>
          <div className="wb-kicker text-[10px]">MAE (worst)</div>
          <div className="mt-1 font-semibold" style={{ color: 'var(--red)' }}>{fmtINR(run.mae)}</div>
        </div>
        <div className="rounded-xl p-3" style={{ background: 'rgba(8,13,23,0.5)', border: '1px solid rgba(148,163,184,0.1)' }}>
          <div className="wb-kicker text-[10px]">Charges</div>
          <div className="mt-1 font-semibold text-[var(--text-primary)]">{fmtINR(run.total_charges)}</div>
        </div>
        <div className="rounded-xl p-3" style={{ background: 'rgba(8,13,23,0.5)', border: '1px solid rgba(148,163,184,0.1)' }}>
          <div className="wb-kicker text-[10px]">Entry credit</div>
          <div className="mt-1 font-semibold text-[var(--text-primary)]">{fmtINR(run.entry_credit_total)}</div>
        </div>
      </div>
    </div>
  )
}

// ── Legs Table ────────────────────────────────────────────────────────────────

function ReplayLegsTable({ legs, instrument }) {
  if (!legs?.length) {
    return (
      <div className="wb-card p-5">
        <div className="wb-kicker">Legs</div>
        <div className="mt-3 text-sm wb-muted">No trade entered.</div>
      </div>
    )
  }
  return (
    <div className="wb-card p-5">
      <div className="wb-kicker mb-3">Legs — full contract identity</div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm min-w-[640px]">
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)' }}>
              {['Contract', 'Expiry', 'Lots', 'Qty', 'Entry price', 'Exit price', 'Gross P/L'].map(h => (
                <th key={h} className="text-left py-2 pr-4 wb-muted text-[11px] uppercase tracking-[0.15em] font-medium">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {legs.map((leg, i) => {
              const pnl = leg.gross_leg_pnl
              const pnlColor = pnl == null ? 'var(--text-secondary)' : pnl >= 0 ? 'var(--green)' : 'var(--red)'
              const label = `${leg.side} ${instrument || ''} ${leg.strike} ${leg.option_type}`
              return (
                <tr key={i} style={{ borderBottom: '1px solid rgba(39,54,75,0.45)' }}>
                  <td className="py-3 pr-4 font-medium text-[var(--text-primary)]">{label}</td>
                  <td className="py-3 pr-4 wb-muted">{fmtExpiry(leg.expiry_date)}</td>
                  <td className="py-3 pr-4 text-[var(--text-primary)]">{leg.lots ?? '—'}</td>
                  <td className="py-3 pr-4 text-[var(--text-primary)]">{leg.quantity?.toLocaleString() ?? '—'}</td>
                  <td className="py-3 pr-4 text-[var(--text-primary)]">{fmtINR(leg.entry_price)}</td>
                  <td className="py-3 pr-4 text-[var(--text-primary)]">{fmtINR(leg.exit_price)}</td>
                  <td className="py-3 pr-4 font-semibold" style={{ color: pnlColor }}>{fmtINR(pnl)}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── SpotVix Chart ─────────────────────────────────────────────────────────────

function SpotVixChart({ spotData, vixData, entryLabel, exitLabel, exitReason }) {
  const [showVix, setShowVix] = useState(true)

  const merged = useMemo(() => {
    if (!spotData?.length) return []
    const vixMap = {}
    vixData?.forEach(r => { vixMap[timeLabel(r.timestamp)] = r.vix_close })
    return spotData.map(r => ({
      label: timeLabel(r.timestamp),
      spot:  Number(r.close),
      vix:   vixMap[timeLabel(r.timestamp)] ?? null,
    }))
  }, [spotData, vixData])

  if (!merged.length) {
    return (
      <div className="wb-card p-5">
        <div className="wb-kicker">NIFTY Spot + India VIX</div>
        <div className="mt-4 text-sm wb-muted">No spot data available.</div>
      </div>
    )
  }

  const spotVals = merged.map(r => r.spot).filter(isFinite)
  const spotLo = Math.floor(Math.min(...spotVals) / 200) * 200
  const spotHi = Math.ceil(Math.max(...spotVals) / 200) * 200
  const vixVals = merged.map(r => r.vix).filter(v => v != null)
  const vixLo = vixVals.length ? Math.floor(Math.min(...vixVals) - 2) : 0
  const vixHi = vixVals.length ? Math.ceil(Math.max(...vixVals)  + 2) : 50

  return (
    <div className="wb-card p-5">
      <div className="flex items-center justify-between mb-1 flex-wrap gap-2">
        <div className="wb-kicker">NIFTY Spot + India VIX</div>
        <div className="flex items-center gap-4 text-[10px]" style={{ color: 'var(--text-secondary)' }}>
          <span className="flex items-center gap-1">
            <span style={{ display: 'inline-block', width: 18, height: 2, background: '#38bdf8', borderRadius: 1 }} />NIFTY
          </span>
          {vixVals.length > 0 && (
            <button
              className="flex items-center gap-1 focus:outline-none"
              onClick={() => setShowVix(v => !v)}
              style={{ color: showVix ? '#fb923c' : 'var(--text-secondary)', cursor: 'pointer' }}
            >
              <span style={{ display: 'inline-block', width: 18, height: 2, background: '#fb923c', opacity: showVix ? 1 : 0.4, borderRadius: 1 }} />VIX {showVix ? '✓' : '○'}
            </button>
          )}
          <span className="flex items-center gap-1">
            <span style={{ display: 'inline-block', width: 2, height: 12, background: '#22c55e', borderRadius: 1 }} />Entry {entryLabel}
          </span>
          <span className="flex items-center gap-1">
            <span style={{ display: 'inline-block', width: 2, height: 12, background: '#ef4444', borderRadius: 1 }} />Exit {exitLabel}
          </span>
        </div>
      </div>
      <div className="mt-3" style={{ height: 280 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={merged} margin={{ top: 8, right: 56, left: 8, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#213047" vertical={false} />
            <XAxis dataKey="label" tick={{ fill: '#8090aa', fontSize: 10 }} tickLine={false} axisLine={{ stroke: '#27364b' }} interval={29} />
            <YAxis
              yAxisId="spot"
              tick={{ fill: '#8090aa', fontSize: 10 }} tickLine={false} axisLine={false}
              width={82} tickFormatter={v => fmtNumber(v, 0)} domain={[spotLo, spotHi]}
            />
            {showVix && vixVals.length > 0 && (
              <YAxis
                yAxisId="vix"
                orientation="right"
                tick={{ fill: '#fb923c', fontSize: 10 }} tickLine={false} axisLine={false}
                width={44} tickFormatter={v => v?.toFixed(1)} domain={[vixLo, vixHi]}
              />
            )}
            <Tooltip
              formatter={(value, name) => {
                if (name === 'spot') return [fmtNumber(value, 1), 'NIFTY']
                if (name === 'vix') return [value?.toFixed(2), 'VIX']
                return [value, name]
              }}
              contentStyle={{ background: '#0f1726', border: '1px solid #27364b', borderRadius: 12, fontSize: 11 }}
              labelStyle={{ color: '#b8c7de' }}
            />
            {entryLabel && (
              <ReferenceLine yAxisId="spot" x={entryLabel} stroke="#22c55e" strokeWidth={1.5} strokeDasharray="4 3">
                <Label value="IN" position="insideTopRight" fill="#22c55e" fontSize={10} fontWeight={700} />
              </ReferenceLine>
            )}
            {exitLabel && (
              <ReferenceLine yAxisId="spot" x={exitLabel} stroke="#ef4444" strokeWidth={1.5} strokeDasharray="4 3">
                <Label value="OUT" position="insideTopLeft" fill="#ef4444" fontSize={10} fontWeight={700} />
              </ReferenceLine>
            )}
            <Line yAxisId="spot" type="monotone" dataKey="spot" stroke="#38bdf8" strokeWidth={1.5} dot={false} />
            {showVix && vixVals.length > 0 && (
              <Line yAxisId="vix" type="monotone" dataKey="vix" stroke="#fb923c" strokeWidth={1.5} dot={false} connectNulls />
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

// ── MTM By Leg Chart ──────────────────────────────────────────────────────────

function MtmByLegChart({ data, showTrail, showShadow, showCePe, entryLabel, exitLabel }) {
  const [showCePeLines, setShowCePeLines] = useState(showCePe)

  if (!data?.length) {
    return (
      <div className="wb-card p-5">
        <div className="wb-kicker">MTM by leg</div>
        <div className="mt-4 text-sm wb-muted">No data available.</div>
      </div>
    )
  }

  const allVals = [
    ...data.map(r => r.net_mtm).filter(v => v != null),
    ...data.map(r => r.trail_stop).filter(v => v != null),
    ...data.map(r => r.shadow_mtm).filter(v => v != null),
    ...(showCePeLines ? data.map(r => r.ce_mtm).filter(v => v != null) : []),
    ...(showCePeLines ? data.map(r => r.pe_mtm).filter(v => v != null) : []),
    0,
  ]
  const lo = Math.floor(Math.min(...allVals) / 1000) * 1000
  const hi = Math.ceil(Math.max(...allVals) / 1000) * 1000

  return (
    <div className="wb-card p-5">
      <div className="flex items-center justify-between mb-1 flex-wrap gap-2">
        <div className="wb-kicker">MTM by leg</div>
        <div className="flex items-center gap-3 text-[10px] flex-wrap" style={{ color: 'var(--text-secondary)' }}>
          <span className="flex items-center gap-1"><span style={{ display: 'inline-block', width: 18, height: 2, background: '#36b37e', borderRadius: 1 }} />Net MTM</span>
          {showCePe && (
            <button
              className="flex items-center gap-1 focus:outline-none"
              onClick={() => setShowCePeLines(v => !v)}
              style={{ color: showCePeLines ? 'var(--text-secondary)' : 'var(--text-secondary)', cursor: 'pointer' }}
            >
              <span style={{ display: 'inline-block', width: 18, height: 2, background: '#fbbf24', opacity: showCePeLines ? 1 : 0.4, borderRadius: 1 }} />CE
              <span style={{ display: 'inline-block', width: 18, height: 2, background: '#67e8f9', opacity: showCePeLines ? 1 : 0.4, borderRadius: 1, marginLeft: 4 }} />PE
              {showCePeLines ? ' ✓' : ' ○'}
            </button>
          )}
          {showTrail  && <span className="flex items-center gap-1"><span style={{ display: 'inline-block', width: 18, height: 2, background: '#f59e0b', borderRadius: 1 }} />Trail</span>}
          {showShadow && <span className="flex items-center gap-1"><span style={{ display: 'inline-block', width: 18, height: 2, background: '#a78bfa', borderRadius: 1 }} />If held</span>}
        </div>
      </div>
      <div className="mt-3" style={{ height: 280 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 16, left: 8, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#213047" vertical={false} />
            <XAxis dataKey="label" tick={{ fill: '#8090aa', fontSize: 10 }} tickLine={false} axisLine={{ stroke: '#27364b' }} interval={29} />
            <YAxis tick={{ fill: '#8090aa', fontSize: 10 }} tickLine={false} axisLine={false} width={88} tickFormatter={v => fmtINR(v)} domain={[lo, hi]} />
            <Tooltip
              formatter={(value, name) => {
                if (value == null) return null
                const labels = { net_mtm: 'Net MTM', trail_stop: 'Trail stop', shadow_mtm: 'If held', ce_mtm: 'CE MTM', pe_mtm: 'PE MTM' }
                return [fmtINR(value), labels[name] || name]
              }}
              contentStyle={{ background: '#0f1726', border: '1px solid #27364b', borderRadius: 12, fontSize: 11 }}
              labelStyle={{ color: '#b8c7de' }}
            />
            <ReferenceLine y={0} stroke="#334155" strokeWidth={1} />
            {entryLabel && (
              <ReferenceLine x={entryLabel} stroke="#22c55e" strokeWidth={1.5} strokeDasharray="4 3">
                <Label value="IN" position="insideTopRight" fill="#22c55e" fontSize={10} fontWeight={700} />
              </ReferenceLine>
            )}
            {exitLabel && (
              <ReferenceLine x={exitLabel} stroke="#ef4444" strokeWidth={1.5} strokeDasharray="4 3">
                <Label value="OUT" position="insideTopLeft" fill="#ef4444" fontSize={10} fontWeight={700} />
              </ReferenceLine>
            )}
            <Line type="monotone" dataKey="net_mtm" stroke="#36b37e" strokeWidth={2} dot={false} connectNulls={false} />
            {showCePeLines && (
              <Line type="monotone" dataKey="ce_mtm" stroke="#fbbf24" strokeWidth={1.5} dot={false} connectNulls={false} strokeOpacity={0.9} />
            )}
            {showCePeLines && (
              <Line type="monotone" dataKey="pe_mtm" stroke="#67e8f9" strokeWidth={1.5} dot={false} connectNulls={false} strokeOpacity={0.9} />
            )}
            {showTrail && (
              <Line type="monotone" dataKey="trail_stop" stroke="#f59e0b" strokeWidth={1.5} strokeDasharray="5 3" dot={false} connectNulls={false} />
            )}
            {showShadow && (
              <Line type="monotone" dataKey="shadow_mtm" stroke="#a78bfa" strokeWidth={1.5} strokeDasharray="6 3" dot={false} connectNulls={false} strokeOpacity={0.8} />
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

// ── Premium Chart (one per leg) ───────────────────────────────────────────────

function PremiumChart({ title, data, entryPrice, exitPrice, entryLabel, exitLabel }) {
  if (!data?.length) {
    return (
      <div className="wb-card p-5">
        <div className="wb-kicker">{title}</div>
        <div className="mt-4 text-sm wb-muted">No candle data available for this leg.</div>
      </div>
    )
  }

  const chartData = data.map(r => ({ label: timeLabel(r.timestamp), close: Number(r.close) }))
  const vals = chartData.map(r => r.close).filter(isFinite)
  const extraVals = [entryPrice, exitPrice].filter(v => v != null)
  const allVals = [...vals, ...extraVals]
  const lo = Math.max(0, Math.floor(Math.min(...allVals) * 0.9))
  const hi = Math.ceil(Math.max(...allVals) * 1.1)

  return (
    <div className="wb-card p-5">
      <div className="flex items-center justify-between mb-1">
        <div className="wb-kicker">{title}</div>
        <div className="flex items-center gap-4 text-[10px]" style={{ color: 'var(--text-secondary)' }}>
          {entryPrice != null && <span>Entry ₹{entryPrice}</span>}
          {exitPrice  != null && <span>Exit ₹{exitPrice}</span>}
        </div>
      </div>
      <div className="mt-3" style={{ height: 200 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData} margin={{ top: 8, right: 16, left: 8, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#213047" vertical={false} />
            <XAxis dataKey="label" tick={{ fill: '#8090aa', fontSize: 10 }} tickLine={false} axisLine={{ stroke: '#27364b' }} />
            <YAxis tick={{ fill: '#8090aa', fontSize: 10 }} tickLine={false} axisLine={false} width={58} tickFormatter={v => `₹${fmtNumber(v, 0)}`} domain={[lo, hi]} />
            <Tooltip
              formatter={v => [`₹${fmtNumber(v, 2)}`, 'Premium']}
              contentStyle={{ background: '#0f1726', border: '1px solid #27364b', borderRadius: 12, fontSize: 11 }}
              labelStyle={{ color: '#b8c7de' }}
            />
            {entryLabel && (
              <ReferenceLine x={entryLabel} stroke="#22c55e" strokeWidth={1.5} strokeDasharray="4 3">
                <Label value="IN" position="insideTopRight" fill="#22c55e" fontSize={9} fontWeight={700} />
              </ReferenceLine>
            )}
            {exitLabel && (
              <ReferenceLine x={exitLabel} stroke="#ef4444" strokeWidth={1.5} strokeDasharray="4 3">
                <Label value="OUT" position="insideTopLeft" fill="#ef4444" fontSize={9} fontWeight={700} />
              </ReferenceLine>
            )}
            {entryPrice != null && (
              <ReferenceLine y={entryPrice} stroke="#22c55e" strokeWidth={1} strokeDasharray="3 3" />
            )}
            <Line type="monotone" dataKey="close" stroke="#c084fc" strokeWidth={1.5} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

// ── Decision Timeline ─────────────────────────────────────────────────────────

const IMPORTANT_EVENTS = new Set(['ENTRY', 'EXIT', 'TRAIL_EXIT', 'STOP_EXIT', 'TARGET_EXIT',
                                    'TIME_EXIT', 'DATA_GAP_EXIT', 'DATA_WARNING', 'NO_TRADE'])

function isImportantEvent(ev) {
  return IMPORTANT_EVENTS.has(ev.event_type) || ev.event_type?.includes('EXIT')
}

function DecisionTimeline({ events }) {
  const [showFull, setShowFull] = useState(false)

  const important = useMemo(() => events.filter(isImportantEvent), [events])
  const visible = showFull ? events : important

  const eventColor = (type) => {
    if (type === 'ENTRY') return 'var(--green)'
    if (type?.includes('EXIT') || type?.includes('STOP')) return 'var(--red)'
    if (type === 'NO_TRADE') return '#f59e0b'
    return 'var(--text-secondary)'
  }

  return (
    <div className="wb-card p-5">
      <div className="flex items-center justify-between gap-4 mb-4 flex-wrap">
        <div>
          <div className="wb-kicker">Decision timeline</div>
          <h2 className="text-base font-semibold text-[var(--text-primary)] mt-1">
            {important.length} key event{important.length !== 1 ? 's' : ''}
            {events.length > important.length && (
              <span className="wb-muted font-normal text-sm"> · {events.length} total</span>
            )}
          </h2>
        </div>
        {events.length > important.length && (
          <button className="wb-secondary-button" onClick={() => setShowFull(v => !v)}>
            {showFull ? 'Show key events only' : 'Show full event log'}
          </button>
        )}
      </div>

      {visible.length === 0 && (
        <div className="text-sm wb-muted">No events recorded.</div>
      )}

      <div className="space-y-1">
        {visible.map((ev, i) => (
          <div key={i}
               className="flex items-start gap-3 rounded-xl px-3 py-2.5"
               style={{
                 background: isImportantEvent(ev) ? 'rgba(8,13,23,0.6)' : 'transparent',
                 border: isImportantEvent(ev) ? '1px solid rgba(148,163,184,0.1)' : '1px solid transparent',
               }}>
            <div className="text-[11px] font-mono mt-0.5" style={{ color: 'var(--text-secondary)', minWidth: 38 }}>
              {timeLabel(ev.timestamp)}
            </div>
            <div className="text-xs font-semibold min-w-[90px]" style={{ color: eventColor(ev.event_type) }}>
              {ev.event_type}
            </div>
            <div className="text-xs wb-muted flex-1">
              {ev.reason_code && <span className="mr-2">{ev.reason_code}</span>}
              {ev.reason_text && ev.reason_text !== ev.reason_code && <span className="opacity-70">{ev.reason_text}</span>}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Export Menu ───────────────────────────────────────────────────────────────

function ExportMenu({ runId, run }) {
  const [open, setOpen] = useState(false)

  const handleCsvDownload = () => {
    const url = getStrategyRunReplayCsvUrl(runId)
    const a = document.createElement('a')
    a.href = url
    // Pass auth header via link click isn't possible for file downloads
    // Use window.open which will use the session cookie / storage
    window.open(url, '_blank')
    setOpen(false)
  }

  const handlePdfPrint = () => {
    setOpen(false)
    setTimeout(() => window.print(), 100)
  }

  return (
    <div className="relative">
      <button
        className="wb-secondary-button flex items-center gap-1.5"
        onClick={() => setOpen(v => !v)}
      >
        Download ▾
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-50 rounded-xl shadow-2xl overflow-hidden"
             style={{ background: '#0f1726', border: '1px solid #27364b', minWidth: 180 }}>
          <button
            className="w-full text-left px-4 py-2.5 text-sm hover:bg-[#1a2740] transition-colors"
            style={{ color: 'var(--text-primary)' }}
            onClick={handleCsvDownload}
          >
            Full Replay CSV
          </button>
          <button
            className="w-full text-left px-4 py-2.5 text-sm hover:bg-[#1a2740] transition-colors"
            style={{ color: 'var(--text-primary)' }}
            onClick={handlePdfPrint}
          >
            PDF Report (Print)
          </button>
        </div>
      )}
    </div>
  )
}

// ── StrategyRunAnalyzer ───────────────────────────────────────────────────────

function StrategyRunAnalyzer({ payload, kind, id, navigate }) {
  const run      = payload?.run      || {}
  const legs     = payload?.legs     || []
  const events   = payload?.events   || []

  const spotSeriesFull    = payload?.spot_series_full    || payload?.spot_series || []
  const vixSeriesFull     = payload?.vix_series_full     || []
  const mtmSeries         = payload?.mtm_series          || []
  const shadowMtmSeries   = payload?.shadow_mtm_series   || []
  const legCandles        = payload?.leg_candles         || {}
  const dataQuality       = payload?.data_quality        || []

  const entryLabel = run.entry_time || null
  const exitLabel  = run.exit_time  || null

  // ── MTM chart data ────────────────────────────────────────────────────────
  const mtmByLabel = useMemo(() => {
    const m = {}
    mtmSeries.forEach(r => {
      m[timeLabel(r.timestamp)] = {
        net_mtm:     r.net_mtm         != null ? Number(r.net_mtm)         : null,
        trail_stop:  r.trail_stop_level != null ? Number(r.trail_stop_level): null,
        ce_mtm:      r.ce_mtm           != null ? Number(r.ce_mtm)          : null,
        pe_mtm:      r.pe_mtm           != null ? Number(r.pe_mtm)          : null,
      }
    })
    return m
  }, [mtmSeries])

  const shadowByLabel = useMemo(() => {
    const m = {}
    shadowMtmSeries.forEach(r => { m[timeLabel(r.timestamp)] = r.net_mtm != null ? Number(r.net_mtm) : null })
    return m
  }, [shadowMtmSeries])

  const chartMtm = useMemo(
    () => spotSeriesFull.map(r => {
      const lbl = timeLabel(r.timestamp)
      const mtm = mtmByLabel[lbl]
      return {
        label:      lbl,
        net_mtm:    mtm ? mtm.net_mtm    : null,
        trail_stop: mtm ? mtm.trail_stop  : null,
        ce_mtm:     mtm ? mtm.ce_mtm      : null,
        pe_mtm:     mtm ? mtm.pe_mtm      : null,
        shadow_mtm: shadowByLabel[lbl] ?? null,
      }
    }),
    [spotSeriesFull, mtmByLabel, shadowByLabel]
  )

  const hasTrail  = chartMtm.some(r => r.trail_stop != null)
  const hasShadow = chartMtm.some(r => r.shadow_mtm != null)
  const hasCePe   = chartMtm.some(r => r.ce_mtm != null || r.pe_mtm != null)

  // ── Premium chart data per leg ────────────────────────────────────────────
  const ceLeg = legs.find(l => l.option_type === 'CE')
  const peLeg = legs.find(l => l.option_type === 'PE')
  const ceCandles = legCandles[String(ceLeg?.leg_index)] || []
  const peCandles = legCandles[String(peLeg?.leg_index)] || []

  const tone = runStatusTone(run.status)
  const pnl  = run.realized_net_pnl

  return (
    <div className="mx-auto max-w-[1360px] replay-v2-container" style={{ padding: '18px 20px 24px', fontSize: 12 }}>

      {/* ── Header ── */}
      <section className="wb-card p-6 print-section">
        <div className="flex items-start justify-between gap-5 flex-wrap">
          <div>
            <div className="flex items-center gap-3 flex-wrap">
              <button className="wb-link no-print" onClick={() => navigate('/workbench/history')}>← Back</button>
              <span className="wb-chip">Session Backtest</span>
              <span className="wb-chip" style={{ background: tone.background, borderColor: tone.border, color: tone.color }}>
                {run.status}
              </span>
            </div>
            <h1 className="mt-4 text-3xl font-semibold text-[var(--text-primary)]">
              {run.instrument} · {run.trade_date}
            </h1>
            <p className="mt-2 text-sm wb-muted">
              {run.strategy_id} · exit: {run.exit_reason || '—'}
            </p>
          </div>
          <div className="flex items-start gap-4">
            <div className="text-right">
              <div className="wb-kicker">Net P/L</div>
              <div className="mt-2 text-4xl font-semibold" style={{ color: pnl == null ? 'var(--text-secondary)' : pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                {fmtINR(pnl)}
              </div>
            </div>
            <div className="no-print mt-1">
              <ExportMenu runId={id} run={run} />
            </div>
          </div>
        </div>
      </section>

      {/* ── Data quality warnings ── */}
      {dataQuality.length > 0 && (
        <div className="mt-4">
          <DataQualityBanner warnings={dataQuality} />
        </div>
      )}

      {/* ── Execution Summary Card ── */}
      <section className="mt-6 print-section">
        <ExecutionSummaryCard run={run} />
      </section>

      {/* ── Legs Table ── */}
      <section className="mt-6 print-section">
        <ReplayLegsTable legs={legs} instrument={run.instrument} />
      </section>

      {/* ── Spot + VIX and MTM by Leg ── */}
      <section className="wb-grid wb-grid-2 mt-6 print-section">
        <SpotVixChart
          spotData={spotSeriesFull}
          vixData={vixSeriesFull}
          entryLabel={entryLabel}
          exitLabel={exitLabel}
          exitReason={run.exit_reason}
        />
        <MtmByLegChart
          data={chartMtm}
          showTrail={hasTrail}
          showShadow={hasShadow}
          showCePe={hasCePe}
          entryLabel={entryLabel}
          exitLabel={exitLabel}
        />
      </section>

      {/* ── Premium charts ── */}
      {(ceCandles.length > 0 || peCandles.length > 0) && (
        <section className="wb-grid wb-grid-2 mt-6 print-section">
          {ceCandles.length > 0 && ceLeg && (
            <PremiumChart
              title={`CE Premium — ${run.instrument} ${ceLeg.strike} CE`}
              data={ceCandles}
              entryPrice={ceLeg.entry_price}
              exitPrice={ceLeg.exit_price}
              entryLabel={entryLabel}
              exitLabel={exitLabel}
            />
          )}
          {peCandles.length > 0 && peLeg && (
            <PremiumChart
              title={`PE Premium — ${run.instrument} ${peLeg.strike} PE`}
              data={peCandles}
              entryPrice={peLeg.entry_price}
              exitPrice={peLeg.exit_price}
              entryLabel={entryLabel}
              exitLabel={exitLabel}
            />
          )}
        </section>
      )}

      {/* ── Decision Timeline ── */}
      <section className="mt-6 print-section">
        <DecisionTimeline events={events} />
      </section>
    </div>
  )
}

// ── Legacy analyzer (ORB paper/historical sessions) ───────────────────────────

function AnalyzerChart({ title, data, lineKey, color, valueFormatter, tightDomain = false, roundTo = 200 }) {
  if (!data?.length) {
    return (
      <div className="wb-card p-5">
        <div className="wb-kicker">{title}</div>
        <div className="mt-4 text-sm wb-muted">No data available for this chart.</div>
      </div>
    )
  }

  let yDomain
  if (tightDomain) {
    const vals = data.map(r => Number(r[lineKey])).filter(v => isFinite(v))
    const lo = Math.floor(Math.min(...vals) / roundTo) * roundTo
    const hi = Math.ceil(Math.max(...vals) / roundTo) * roundTo
    yDomain = [lo, hi]
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
              domain={yDomain}
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

// ── Main export ───────────────────────────────────────────────────────────────

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

  // Generic strategy run — V2 analyzer
  if (kind === 'strategy_run') {
    return <StrategyRunAnalyzer payload={payload} kind={kind} id={id} navigate={navigate} />
  }

  if (!session) return null

  const tone = runStatusTone(session.status)
  const legacyRoute = kind === 'paper_session' ? `/paper/session/${id}` : `/backtests/sessions/${id}`
  const backRoute = kind === 'paper_session' ? '/workbench/replay' : session.batch_id ? `/workbench/history/historical_batch/${session.batch_id}` : '/workbench/history'

  return (
    <div className="mx-auto max-w-[1360px]" style={{ padding: '18px 20px 24px', fontSize: 12 }}>
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
        <AnalyzerChart title="Spot progression" data={spotSeries} lineKey="spot" color="#38bdf8" valueFormatter={value => fmtNumber(value, 0)} tightDomain />
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
