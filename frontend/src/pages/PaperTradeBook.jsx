import { useEffect, useRef, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { getPaperSession, getPaperDecisions, getPaperTrade, getPaperMarks, getPaperCandles } from '../api'
import { PnlProgressionChart } from '../components/PnlChart'

const fmtINR = v => v == null ? '—' :
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(v)
const fmtNum = (v, dp = 2) =>
  v == null ? '—' : v.toLocaleString('en-IN', { minimumFractionDigits: dp, maximumFractionDigits: dp })
const fmtDate = d => {
  if (!d) return '—'
  const dt = new Date(d)
  return dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
}

const ACTION_STYLES = {
  ENTER:        { bg: 'rgba(34,197,94,0.15)',   border: 'rgba(34,197,94,0.4)',   text: '#22c55e' },
  HOLD:         { bg: 'rgba(59,130,246,0.10)',  border: 'rgba(59,130,246,0.3)',  text: '#3b82f6' },
  EXIT_TARGET:  { bg: 'rgba(34,197,94,0.20)',   border: 'rgba(34,197,94,0.5)',   text: '#22c55e' },
  EXIT_STOP:    { bg: 'rgba(239,68,68,0.15)',   border: 'rgba(239,68,68,0.4)',   text: '#ef4444' },
  EXIT_TIME:    { bg: 'rgba(245,158,11,0.15)',  border: 'rgba(245,158,11,0.4)',  text: '#f59e0b' },
  NO_TRADE:     { bg: 'rgba(100,116,139,0.10)', border: 'rgba(100,116,139,0.3)', text: '#64748b' },
  DATA_GAP:     { bg: 'rgba(100,116,139,0.10)', border: 'rgba(100,116,139,0.3)', text: '#64748b' },
}

function ActionBadge({ action }) {
  const s = ACTION_STYLES[action] || ACTION_STYLES.NO_TRADE
  return (
    <span className="px-1.5 py-0.5 rounded text-xs font-semibold"
      style={{ background: s.bg, border: `1px solid ${s.border}`, color: s.text }}>
      {action?.replace(/_/g, ' ')}
    </span>
  )
}

function KV({ label, value, valueColor }) {
  return (
    <div className="flex justify-between items-center py-1 border-b" style={{ borderColor: 'rgba(51,65,85,0.5)' }}>
      <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{label}</span>
      <span className="text-xs font-medium" style={{ color: valueColor || 'var(--text-primary)' }}>{value}</span>
    </div>
  )
}

function extractSelectionAudit(decisions, trade) {
  const decisionWithRanking =
    decisions.find(d => d.action === 'ENTER' && d.candidate_ranking_json) ||
    [...decisions].reverse().find(d => d.candidate_ranking_json)

  if (!decisionWithRanking?.candidate_ranking_json) return null

  const ranking = decisionWithRanking.candidate_ranking_json
  const rankedCandidates = (ranking.candidates || [])
    .filter(c => c.rank != null)
    .sort((a, b) => a.rank - b.rank)

  const selected = rankedCandidates.find(c => c.rank === 1) || rankedCandidates[0] || null
  const chosenSpread = trade
    ? `${trade.long_strike}/${trade.short_strike} ${trade.option_type}`
    : selected
      ? `${selected.long_strike}/${selected.short_strike} ${selected.option_type || selected.opt_type}`
      : '—'

  return {
    selectionMethod: trade?.selection_method || ranking.selection_method || '—',
    signalDirection: trade?.bias || ranking.signal_direction || selected?.direction || '—',
    evaluatedCount: ranking.evaluated_candidates ?? ranking.evaluatedCount ?? 0,
    validCount: ranking.valid_candidates ?? ranking.validCount ?? 0,
    chosenSpread,
    chosenRank: trade?.selected_candidate_rank ?? ranking.selected_candidate_rank ?? selected?.rank ?? null,
    chosenScore: trade?.selected_candidate_score ?? ranking.selected_candidate_score ?? selected?.score ?? null,
    topCandidates: rankedCandidates.slice(0, 3),
  }
}

// ── CSV export ─────────────────────────────────────────────────────────────────
function buildCSV(session, trade, decisions, marks, candleSeries) {
  const rows = []
  const selectionAudit = extractSelectionAudit(decisions, trade)

  rows.push(['SESSION SUMMARY'])
  rows.push(['Date', session.session_date])
  rows.push(['Instrument', session.instrument])
  rows.push(['Capital', session.capital])
  rows.push(['Status', session.status])
  rows.push(['Minutes Audited', session.decision_count])
  rows.push([])

  if (trade) {
    rows.push(['TRADE DETAIL'])
    rows.push(['Bias', trade.bias])
    rows.push(['Option Type', trade.option_type])
    rows.push(['Long Strike', trade.long_strike])
    rows.push(['Short Strike', trade.short_strike])
    rows.push(['Expiry', trade.expiry])
    rows.push(['Lot Size', trade.lot_size])
    rows.push(['Approved Lots', trade.approved_lots])
    rows.push(['Total Qty', trade.lot_size * trade.approved_lots])
    rows.push(['Entry Debit / unit', trade.entry_debit])
    rows.push(['Entry Time', trade.entry_time?.slice(0, 16)])
    rows.push(['Exit Time', trade.exit_time?.slice(0, 16)])
    rows.push(['Exit Reason', trade.exit_reason])
    rows.push(['Max Loss', trade.total_max_loss])
    rows.push(['Target', trade.target_profit])
    rows.push(['Gross P&L', trade.realized_gross_pnl])
    rows.push(['Net P&L', trade.realized_net_pnl])
    rows.push([])

    if (trade.legs?.length) {
      rows.push(['LEGS'])
      rows.push(['Side', 'Contract', 'Lots', 'Lot Size', 'Total Qty', 'Entry Price', 'Exit Price'])
      trade.legs.forEach(l => {
        rows.push([
          l.leg_side === 'LONG' ? 'BUY' : 'SELL',
          `${session.instrument} ${l.strike} ${l.option_type} ${l.expiry}`,
          trade.approved_lots,
          trade.lot_size,
          trade.approved_lots * trade.lot_size,
          l.entry_price ?? '',
          l.exit_price ?? '',
        ])
      })
      rows.push([])
    }
  }

  if (selectionAudit) {
    rows.push(['SPREAD SELECTION'])
    rows.push(['Signal Direction', selectionAudit.signalDirection])
    rows.push(['Selection Method', selectionAudit.selectionMethod])
    rows.push(['Candidates Evaluated', selectionAudit.evaluatedCount])
    rows.push(['Valid Candidates', selectionAudit.validCount])
    rows.push(['Chosen Spread', selectionAudit.chosenSpread])
    rows.push(['Chosen Rank', selectionAudit.chosenRank ?? ''])
    rows.push(['Chosen Score', selectionAudit.chosenScore ?? ''])
    rows.push([])

    if (selectionAudit.topCandidates.length) {
      rows.push(['TOP 3 CANDIDATES'])
      rows.push(['Rank', 'Spread', 'Debit', 'Max Loss', 'Max Gain', 'Volume', 'OI', 'Score', 'Status'])
      selectionAudit.topCandidates.forEach(c => {
        rows.push([
          c.rank,
          `${c.long_strike}/${c.short_strike} ${c.option_type || c.opt_type}`,
          c.spread_debit,
          c.total_max_loss,
          c.max_gain_total,
          c.combined_volume,
          c.combined_oi,
          c.score,
          c.status,
        ])
      })
      rows.push([])
    }
  }

  rows.push(['MINUTE AUDIT LOG'])
  rows.push(['Time', 'Spot Close', 'OR High', 'OR Low', 'State', 'Action', 'Reason Code', 'Reason Text',
    'Max Loss', 'Target/MTM', 'Candidate Structure'])
  decisions.forEach(d => {
    const mark = marks.find(m => m.timestamp === d.timestamp)
    const mtm = mark?.total_mtm ?? ''
    rows.push([
      d.timestamp?.slice(11, 16),
      d.spot_close,
      d.opening_range_high,
      d.opening_range_low,
      d.trade_state === 'OPEN_TRADE' ? 'OPEN' : 'WATCH',
      d.action,
      d.reason_code,
      `"${(d.reason_text || '').replace(/"/g, "'")}"`,
      d.computed_max_loss ?? '',
      mtm !== '' ? mtm : (d.computed_target ?? ''),
      d.candidate_structure ? `"${JSON.stringify(d.candidate_structure).replace(/"/g, "'")}"` : '',
    ])
  })
  rows.push([])

  // ── Candle series sections ────────────────────────────────────────────────
  if (candleSeries?.length) {
    candleSeries.forEach(cs => {
      rows.push([`CANDLES: ${cs.series_type}`])
      rows.push(['Time', 'Open', 'High', 'Low', 'Close', 'Volume'])
      ;(cs.candles || []).forEach(c => {
        rows.push([c.time?.slice(11, 16), c.open, c.high, c.low, c.close, c.volume])
      })
      rows.push([])
    })
  }

  return rows.map(r => r.join(',')).join('\n')
}

function downloadCSV(session, trade, decisions, marks, candleSeries) {
  const csv = buildCSV(session, trade, decisions, marks, candleSeries)
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `ORB_${session.instrument}_${session.session_date}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

// ── Compact OHLCV table for PDF / print ───────────────────────────────────────
function CandleTable({ series }) {
  if (!series?.candles?.length) return null
  const label = series.series_type
    .replace('_WEEKLY', ' (Weekly)')
    .replace('_MONTHLY', ' (Monthly)')
    .replace('_CE_', ' CE ')
    .replace('_PE_', ' PE ')
  return (
    <div className="mt-4">
      <div className="text-xs uppercase tracking-widest mb-1" style={{ color: 'var(--text-secondary)' }}>
        {label} — {series.candles.length} candles
      </div>
      <div className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--border)' }}>
        <div style={{ maxHeight: 220, overflowY: 'auto' }}>
          <table className="w-full text-xs">
            <thead style={{ position: 'sticky', top: 0, background: 'var(--surface-tertiary)' }}>
              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                {['Time', 'Open', 'High', 'Low', 'Close', 'Vol'].map(h => (
                  <th key={h} className="text-left px-2 py-1.5 font-medium uppercase tracking-wider"
                    style={{ color: 'var(--text-secondary)' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {series.candles.map((c, i) => (
                <tr key={i} style={{ borderBottom: '0.5px solid var(--border)' }}>
                  <td className="px-2 py-1 font-mono" style={{ color: 'var(--text-primary)' }}>
                    {c.time?.slice(11, 16)}
                  </td>
                  <td className="px-2 py-1" style={{ color: 'var(--text-secondary)' }}>{fmtNum(c.open)}</td>
                  <td className="px-2 py-1" style={{ color: '#22c55e' }}>{fmtNum(c.high)}</td>
                  <td className="px-2 py-1" style={{ color: '#ef4444' }}>{fmtNum(c.low)}</td>
                  <td className="px-2 py-1 font-medium" style={{ color: 'var(--text-primary)' }}>{fmtNum(c.close)}</td>
                  <td className="px-2 py-1" style={{ color: 'var(--text-secondary)' }}>
                    {c.volume?.toLocaleString('en-IN')}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ── Legs contract table ────────────────────────────────────────────────────────
function LegsTable({ trade, instrument }) {
  if (!trade?.legs?.length) return null
  const totalQty = trade.approved_lots * trade.lot_size

  return (
    <div className="mt-3">
      <div className="text-xs uppercase tracking-widest mb-2" style={{ color: 'var(--text-secondary)' }}>
        Contract Breakdown
      </div>
      <div className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--border)' }}>
        <table className="w-full text-xs">
          <thead style={{ background: 'var(--surface-tertiary)' }}>
            <tr style={{ borderBottom: '1px solid var(--border)' }}>
              {['Side', 'Contract', 'Lots', 'Lot Size', 'Total Qty', 'Entry ₹', 'Exit ₹'].map(h => (
                <th key={h} className="text-left px-3 py-2 font-medium uppercase tracking-wider"
                  style={{ color: 'var(--text-secondary)' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {trade.legs.map(l => {
              const isBuy = l.leg_side === 'LONG'
              const pnlPerUnit = l.exit_price != null ? (isBuy ? (l.exit_price - l.entry_price) : (l.entry_price - l.exit_price)) : null
              return (
                <tr key={l.leg_side} style={{ borderBottom: '0.5px solid var(--border)' }}>
                  <td className="px-3 py-2">
                    <span className="px-1.5 py-0.5 rounded text-xs font-bold"
                      style={isBuy
                        ? { background: 'rgba(34,197,94,0.15)', color: '#22c55e', border: '1px solid rgba(34,197,94,0.3)' }
                        : { background: 'rgba(239,68,68,0.12)', color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)' }}>
                      {isBuy ? 'BUY' : 'SELL'}
                    </span>
                  </td>
                  <td className="px-3 py-2 font-medium" style={{ color: 'var(--text-primary)' }}>
                    {instrument} {l.strike} {l.option_type}
                    <span className="ml-1.5 text-xs" style={{ color: 'var(--text-secondary)' }}>
                      exp {fmtDate(l.expiry)}
                    </span>
                  </td>
                  <td className="px-3 py-2" style={{ color: 'var(--text-secondary)' }}>{trade.approved_lots}</td>
                  <td className="px-3 py-2" style={{ color: 'var(--text-secondary)' }}>{trade.lot_size}</td>
                  <td className="px-3 py-2 font-medium" style={{ color: 'var(--text-primary)' }}>{totalQty}</td>
                  <td className="px-3 py-2" style={{ color: 'var(--text-secondary)' }}>
                    {l.entry_price != null ? `₹${fmtNum(l.entry_price)}` : '—'}
                  </td>
                  <td className="px-3 py-2" style={{
                    color: l.exit_price != null ? 'var(--text-primary)' : 'var(--text-secondary)'
                  }}>
                    {l.exit_price != null ? `₹${fmtNum(l.exit_price)}` : '—'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Candidate detail pill (shown in ENTER audit rows) ─────────────────────────
function CandidatePill({ cs, instrument }) {
  if (!cs || !cs.long_strike) return null
  return (
    <span className="inline-flex items-center gap-1 text-xs ml-2 px-1.5 py-0.5 rounded"
      style={{ background: 'rgba(34,197,94,0.10)', border: '1px solid rgba(34,197,94,0.25)', color: '#22c55e' }}>
      {cs.bias === 'BULLISH' ? '↑' : '↓'} {instrument} {cs.long_strike}/{cs.short_strike} {cs.opt_type}
      <span style={{ color: 'rgba(34,197,94,0.7)' }}>·</span>
      {cs.approved_lots}L × {cs.lot_size} · debit ₹{cs.spread_debit}
      {cs.expiry && <span style={{ color: 'rgba(34,197,94,0.7)' }}> exp {cs.expiry}</span>}
    </span>
  )
}

export default function PaperTradeBook() {
  const { id } = useParams()
  const navigate = useNavigate()
  const printRef = useRef()
  const [session, setSession] = useState(null)
  const [decisions, setDecisions] = useState([])
  const [trade, setTrade] = useState(null)
  const [marks, setMarks] = useState([])
  const [candleSeries, setCandleSeries] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('ALL')
  const [error, setError] = useState(null)

  useEffect(() => {
    Promise.all([
      getPaperSession(id),
      getPaperDecisions(id),
      getPaperTrade(id),
      getPaperMarks(id),
      getPaperCandles(id),
    ])
      .then(([sRes, dRes, tRes, mRes, cRes]) => {
        setSession(sRes.data)
        setDecisions(dRes.data)
        setTrade(tRes.data.trade)
        setMarks(mRes.data)
        setCandleSeries(cRes.data)
      })
      .catch(err => setError(err.response?.data?.detail || err.message))
      .finally(() => setLoading(false))
  }, [id])

  if (loading) return (
    <div className="flex items-center justify-center h-64 gap-2" style={{ color: 'var(--text-secondary)' }}>
      <span className="spinner" /> Loading audit log…
    </div>
  )
  if (error) return (
    <div className="max-w-5xl mx-auto p-6">
      <div className="px-4 py-3 rounded text-sm"
        style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#ef4444' }}>
        {error}
      </div>
    </div>
  )
  if (!session) return null

  const pnl = trade?.realized_gross_pnl ?? null
  const netPnl = trade?.realized_net_pnl ?? null
  const pnlColor = pnl == null ? 'var(--text-secondary)' : pnl >= 0 ? '#22c55e' : '#ef4444'

  const FILTERS = ['ALL', 'ENTER', 'HOLD', 'EXIT_TARGET', 'EXIT_STOP', 'EXIT_TIME', 'NO_TRADE']
  const visibleDecisions = filter === 'ALL' ? decisions : decisions.filter(d => d.action === filter)

  const chartData = marks.map(m => ({ time: m.timestamp?.slice(11, 16), spot: 0, pnl: m.total_mtm }))
  const selectionAudit = extractSelectionAudit(decisions, trade)

  const handlePDF = () => window.print()

  return (
    <>
      {/* Print stylesheet injected inline */}
      <style>{`
        @media print {
          body * { visibility: hidden; }
          #print-region, #print-region * { visibility: visible; }
          #print-region { position: absolute; top: 0; left: 0; width: 100%; font-size: 11px; }
          .no-print { display: none !important; }
          table { border-collapse: collapse; width: 100%; }
          th, td { border: 1px solid #ccc; padding: 4px 6px; }
          thead { background: #f3f4f6 !important; print-color-adjust: exact; }
        }
      `}</style>

      <div id="print-region" className="max-w-6xl mx-auto p-6" ref={printRef}>
        {/* Back + download buttons */}
        <div className="flex items-center justify-between mb-4 no-print">
          <button onClick={() => navigate('/paper/sessions')}
            className="text-xs flex items-center gap-1"
            style={{ color: 'var(--text-secondary)', cursor: 'pointer', background: 'none', border: 'none', padding: 0 }}>
            ← Sessions
          </button>
          <div className="flex gap-2">
            <button onClick={() => downloadCSV(session, trade, decisions, marks, candleSeries)}
              className="px-3 py-1.5 rounded text-xs font-medium flex items-center gap-1.5"
              style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)', color: 'var(--text-secondary)', cursor: 'pointer' }}>
              ↓ CSV
            </button>
            <button onClick={handlePDF}
              className="px-3 py-1.5 rounded text-xs font-medium flex items-center gap-1.5"
              style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)', color: 'var(--text-secondary)', cursor: 'pointer' }}>
              ↓ PDF
            </button>
          </div>
        </div>

        {/* Hero row */}
        <div className="flex items-start justify-between mb-5">
          <div>
            <h1 className="text-lg font-bold text-slate-100">
              {session.session_date} — {session.instrument === 'NIFTY' ? 'Nifty 50' : 'Bank Nifty'} · ORB Replay
            </h1>
            <p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
              {session.decision_count} minutes audited · Capital {fmtINR(session.capital)}
            </p>
          </div>
          {pnl != null && (
            <div className="text-right">
              <div className="text-2xl font-bold" style={{ color: pnlColor }}>{fmtINR(pnl)}</div>
              <div className="text-xs mt-0.5" style={{ color: 'var(--text-secondary)' }}>
                Gross · Net {fmtINR(netPnl)}
              </div>
              <div className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                {trade?.exit_reason?.replace(/_/g, ' ')}
              </div>
            </div>
          )}
        </div>

        {/* Trade section */}
        {trade && (
          <>
            <div className="grid grid-cols-2 gap-4 mb-4">
              {/* Trade summary */}
              <div className="rounded-xl p-4" style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>
                <div className="text-xs uppercase tracking-widest mb-3" style={{ color: 'var(--text-secondary)' }}>
                  Trade Summary
                </div>
                <KV label="Bias" value={trade.bias} />
                <KV label="Strategy"
                  value={trade.bias === 'BULLISH' ? `Bull Call Spread (${trade.option_type})` : `Bear Put Spread (${trade.option_type})`} />
                <KV label="Entry Debit / unit" value={`₹${fmtNum(trade.entry_debit)}`} />
                <KV label="Lots × Lot Size" value={`${trade.approved_lots} lots × ${trade.lot_size} = ${trade.approved_lots * trade.lot_size} units`} />
                <KV label="Max Loss" value={fmtINR(trade.total_max_loss)} valueColor="#ef4444" />
                <KV label="Target" value={fmtINR(trade.target_profit)} valueColor="#22c55e" />
                <KV label="Gross P&L" value={fmtINR(trade.realized_gross_pnl)} valueColor={pnlColor} />
                <KV label="Net P&L (after charges)" value={fmtINR(trade.realized_net_pnl)} valueColor={pnlColor} />
              </div>

              {/* Timing */}
              <div className="rounded-xl p-4" style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>
                <div className="text-xs uppercase tracking-widest mb-3" style={{ color: 'var(--text-secondary)' }}>
                  Timing & Expiry
                </div>
                <KV label="Entry Time" value={trade.entry_time?.slice(11, 16) ?? '—'} />
                <KV label="Exit Time" value={trade.exit_time?.slice(11, 16) ?? '—'} />
                <KV label="Exit Reason" value={trade.exit_reason?.replace(/_/g, ' ') ?? '—'} />
                <KV label="Expiry" value={fmtDate(trade.expiry)} />
                <KV label="Exchange" value="NSE / NFO" />
                <KV label="Segment" value="Options" />
              </div>
            </div>

            {/* Contract breakdown table */}
            <div className="rounded-xl p-4 mb-4" style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>
              <LegsTable trade={trade} instrument={session.instrument} />
            </div>

            {/* MTM chart */}
            {chartData.length > 0 && (
              <div className="rounded-xl p-4 mb-4"
                style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>
                <div className="flex items-center justify-between mb-3">
                  <div className="text-xs uppercase tracking-widest" style={{ color: 'var(--text-secondary)' }}>
                    MTM Progression (while trade was open)
                  </div>
                  <div className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                    {marks.length} minutes
                  </div>
                </div>
                <PnlProgressionChart data={chartData} />
              </div>
            )}
          </>
        )}

        {!trade && (
          <div className="rounded-xl p-4 mb-4 text-sm text-center"
            style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
            No trade was opened this session — all gate conditions failed.
          </div>
        )}

        {selectionAudit && (
          <div className="rounded-xl p-4 mb-4" style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>
            <div className="flex items-center justify-between mb-3">
              <div className="text-xs uppercase tracking-widest" style={{ color: 'var(--text-secondary)' }}>
                Spread Selection
              </div>
              <div className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                {selectionAudit.selectionMethod}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4 mb-4">
              <div>
                <KV label="Signal Direction" value={selectionAudit.signalDirection} />
                <KV label="Candidates Evaluated" value={selectionAudit.evaluatedCount} />
                <KV label="Valid Candidates" value={selectionAudit.validCount} />
              </div>
              <div>
                <KV label="Chosen Spread" value={selectionAudit.chosenSpread} />
                <KV label="Chosen Rank" value={selectionAudit.chosenRank != null ? `#${selectionAudit.chosenRank}` : '—'} />
                <KV label="Chosen Score" value={selectionAudit.chosenScore != null ? Number(selectionAudit.chosenScore).toFixed(4) : '—'} valueColor="#22c55e" />
              </div>
            </div>

            {selectionAudit.topCandidates.length > 0 && (
              <div className="rounded-lg overflow-hidden" style={{ border: '1px solid var(--border)' }}>
                <table className="w-full text-xs">
                  <thead style={{ background: 'var(--surface-tertiary)' }}>
                    <tr style={{ borderBottom: '1px solid var(--border)' }}>
                      {['Rank', 'Spread', 'Debit', 'Max Loss', 'Max Gain', 'Volume', 'OI', 'Score', 'Status'].map(h => (
                        <th key={h} className="text-left px-3 py-2 font-medium uppercase tracking-wider"
                          style={{ color: 'var(--text-secondary)' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {selectionAudit.topCandidates.map(candidate => (
                      <tr key={`${candidate.rank}-${candidate.long_strike}-${candidate.short_strike}`}
                        style={{ borderBottom: '0.5px solid var(--border)' }}>
                        <td className="px-3 py-2 font-medium" style={{ color: 'var(--text-primary)' }}>#{candidate.rank}</td>
                        <td className="px-3 py-2" style={{ color: 'var(--text-primary)' }}>
                          {candidate.long_strike}/{candidate.short_strike} {candidate.option_type || candidate.opt_type}
                        </td>
                        <td className="px-3 py-2" style={{ color: 'var(--text-secondary)' }}>₹{fmtNum(candidate.spread_debit)}</td>
                        <td className="px-3 py-2" style={{ color: '#ef4444' }}>{fmtINR(candidate.total_max_loss)}</td>
                        <td className="px-3 py-2" style={{ color: '#22c55e' }}>{fmtINR(candidate.max_gain_total)}</td>
                        <td className="px-3 py-2" style={{ color: 'var(--text-secondary)' }}>{candidate.combined_volume?.toLocaleString('en-IN') ?? '—'}</td>
                        <td className="px-3 py-2" style={{ color: 'var(--text-secondary)' }}>{candidate.combined_oi?.toLocaleString('en-IN') ?? '—'}</td>
                        <td className="px-3 py-2 font-medium" style={{ color: 'var(--text-primary)' }}>
                          {candidate.score != null ? Number(candidate.score).toFixed(4) : '—'}
                        </td>
                        <td className="px-3 py-2" style={{ color: candidate.rank === 1 ? '#22c55e' : 'var(--text-secondary)' }}>
                          {candidate.rank === 1 ? 'Selected' : 'Rejected'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* Candle data section */}
        {candleSeries.length > 0 && (
          <div className="rounded-xl p-4 mb-4"
            style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>
            <div className="text-xs uppercase tracking-widest mb-3" style={{ color: 'var(--text-secondary)' }}>
              Raw Candle Data ({candleSeries.length} series)
            </div>
            {candleSeries.map(cs => (
              <CandleTable key={cs.series_type} series={cs} />
            ))}
          </div>
        )}

        {/* Action summary chips */}
        <div className="flex items-center gap-2 mb-3 flex-wrap">
          {Object.entries(session.action_summary || {}).map(([action, cnt]) => {
            const s = ACTION_STYLES[action] || ACTION_STYLES.NO_TRADE
            return (
              <span key={action} className="px-2 py-0.5 rounded text-xs font-medium"
                style={{ background: s.bg, border: `1px solid ${s.border}`, color: s.text }}>
                {action.replace(/_/g, ' ')} × {cnt}
              </span>
            )
          })}
        </div>

        {/* Filter tabs */}
        <div className="flex items-center gap-1 mb-3 flex-wrap no-print">
          {FILTERS.map(f => (
            <button key={f} onClick={() => setFilter(f)}
              className="px-2.5 py-1 rounded text-xs font-medium transition-all"
              style={{
                background: filter === f ? '#2563eb' : 'var(--surface-secondary)',
                color: filter === f ? 'white' : 'var(--text-secondary)',
                border: filter === f ? '1px solid #2563eb' : '1px solid var(--border)',
                cursor: 'pointer',
              }}>
              {f.replace(/_/g, ' ')}
            </button>
          ))}
          <span className="text-xs ml-auto" style={{ color: 'var(--text-secondary)' }}>
            Showing {visibleDecisions.length} of {decisions.length} rows
          </span>
        </div>

        {/* Minute audit log table */}
        <div className="rounded-xl overflow-hidden"
          style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>
          <div style={{ maxHeight: 520, overflowY: 'auto' }}>
            <table className="w-full text-xs">
              <thead style={{ position: 'sticky', top: 0, zIndex: 1, background: 'var(--surface-tertiary)' }}>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  {['Time', 'Spot', 'OR Hi/Lo', 'State', 'Action', 'Reason / Contract', 'Max Loss', 'Target / MTM'].map(h => (
                    <th key={h} className="text-left px-3 py-2.5 font-medium uppercase tracking-wider"
                      style={{ color: 'var(--text-secondary)' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visibleDecisions.map((d, i) => {
                  const isTrade = d.trade_state === 'OPEN_TRADE'
                  const mark = marks.find(m => m.timestamp === d.timestamp)
                  const mtmVal = (d.action === 'HOLD' || d.action?.startsWith('EXIT')) ? mark?.total_mtm : null
                  const cs = d.candidate_structure
                  return (
                    <tr key={d.id || i}
                      style={{
                        borderBottom: '0.5px solid var(--border)',
                        background: d.action === 'ENTER' ? 'rgba(34,197,94,0.05)'
                          : d.action?.startsWith('EXIT') ? 'rgba(245,158,11,0.05)' : 'transparent',
                      }}>
                      <td className="px-3 py-2 font-mono" style={{ color: 'var(--text-primary)' }}>
                        {d.timestamp?.slice(11, 16)}
                      </td>
                      <td className="px-3 py-2" style={{ color: 'var(--text-secondary)' }}>
                        {d.spot_close?.toLocaleString('en-IN') ?? '—'}
                      </td>
                      <td className="px-3 py-2" style={{ color: 'var(--text-secondary)' }}>
                        {d.opening_range_high
                          ? `${d.opening_range_high?.toFixed(0)}/${d.opening_range_low?.toFixed(0)}`
                          : '—'}
                      </td>
                      <td className="px-3 py-2">
                        <span className="text-xs" style={{ color: isTrade ? '#f59e0b' : '#64748b' }}>
                          {isTrade ? 'OPEN' : 'WATCH'}
                        </span>
                      </td>
                      <td className="px-3 py-2">
                        <ActionBadge action={d.action} />
                      </td>
                      <td className="px-3 py-2" style={{ color: 'var(--text-secondary)', maxWidth: 260 }}>
                        <span title={d.reason_text} style={{ cursor: 'help' }}>
                          {d.reason_code}
                        </span>
                        {/* Show contract details inline for ENTER rows */}
                        {d.action === 'ENTER' && cs && (
                          <CandidatePill cs={cs} instrument={session.instrument} />
                        )}
                        {/* Show failing gate for NO_TRADE with economics */}
                        {d.action === 'NO_TRADE' && cs?.failing_gate && (
                          <span className="ml-2 text-xs" style={{ color: '#94a3b8' }}>
                            failed {cs.failing_gate}
                            {cs.long_strike && ` · ${cs.long_strike}/${cs.short_strike} ${cs.opt_type}`}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2" style={{ color: '#ef4444' }}>
                        {d.computed_max_loss ? fmtINR(d.computed_max_loss) : '—'}
                      </td>
                      <td className="px-3 py-2" style={{
                        color: mtmVal == null ? 'var(--text-secondary)'
                          : mtmVal >= 0 ? '#22c55e' : '#ef4444',
                        fontWeight: mtmVal != null ? 600 : 400,
                      }}>
                        {mtmVal != null ? fmtINR(mtmVal) : (d.computed_target ? fmtINR(d.computed_target) : '—')}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <div className="px-3 py-2 text-xs" style={{ color: 'var(--text-secondary)', borderTop: '0.5px solid var(--border)' }}>
            Hover Reason Code column for full explanation · ENTER rows show contract + lot details inline · {decisions.length} total rows
          </div>
        </div>
      </div>
    </>
  )
}
