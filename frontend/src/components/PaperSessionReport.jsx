import BrandLogo from './BrandLogo'
import { PnlProgressionChart } from './PnlChart'
import { PAPER_SESSION_REPORT_NAME } from '../constants/brand'
import { extractSelectionAudit, fmtDate, fmtINR, fmtNum } from '../utils/paperSessionExport'

const ACTION_STYLES = {
  ENTER:        { bg: 'rgba(34,197,94,0.15)', border: 'rgba(34,197,94,0.4)', text: '#22c55e' },
  HOLD:         { bg: 'rgba(59,130,246,0.10)', border: 'rgba(59,130,246,0.3)', text: '#3b82f6' },
  EXIT_TARGET:  { bg: 'rgba(34,197,94,0.20)', border: 'rgba(34,197,94,0.5)', text: '#22c55e' },
  EXIT_STOP:    { bg: 'rgba(239,68,68,0.15)', border: 'rgba(239,68,68,0.4)', text: '#ef4444' },
  EXIT_TIME:    { bg: 'rgba(245,158,11,0.15)', border: 'rgba(245,158,11,0.4)', text: '#f59e0b' },
  NO_TRADE:     { bg: 'rgba(100,116,139,0.10)', border: 'rgba(100,116,139,0.3)', text: '#64748b' },
  DATA_GAP:     { bg: 'rgba(100,116,139,0.10)', border: 'rgba(100,116,139,0.3)', text: '#64748b' },
}

const FILTERS = ['ALL', 'ENTER', 'HOLD', 'EXIT_TARGET', 'EXIT_STOP', 'EXIT_TIME', 'NO_TRADE']

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
              return (
                <tr key={`${l.leg_side}-${l.strike}-${l.option_type}`} style={{ borderBottom: '0.5px solid var(--border)' }}>
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
                  <td className="px-3 py-2" style={{ color: l.exit_price != null ? 'var(--text-primary)' : 'var(--text-secondary)' }}>
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

export default function PaperSessionReport({
  session,
  trade,
  decisions,
  marks,
  candleSeries,
  filter = 'ALL',
  onFilterChange,
  showFilters = false,
}) {
  if (!session) return null

  const pnl = trade?.realized_gross_pnl ?? null
  const netPnl = trade?.realized_net_pnl ?? null
  const pnlColor = pnl == null ? 'var(--text-secondary)' : pnl >= 0 ? '#22c55e' : '#ef4444'
  const visibleDecisions = filter === 'ALL' ? decisions : decisions.filter(d => d.action === filter)
  const chartData = marks.map(m => ({ time: m.timestamp?.slice(11, 16), spot: 0, pnl: m.total_mtm }))
  const selectionAudit = extractSelectionAudit(decisions, trade)

  return (
    <>
      <div className="flex items-start justify-between mb-5">
        <div className="flex items-start gap-4">
          <BrandLogo size={56} />
          <div>
            <div className="text-[11px] uppercase tracking-[0.35em] mb-1" style={{ color: 'var(--text-secondary)' }}>
              {PAPER_SESSION_REPORT_NAME}
            </div>
            <h1 className="text-lg font-bold text-slate-100">
              {session.session_date} — {session.instrument === 'NIFTY' ? 'Nifty 50' : 'Bank Nifty'} · ORB Replay
            </h1>
            <p className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
              {session.decision_count} minutes audited · Capital {fmtINR(session.capital)}
            </p>
          </div>
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

      {trade && (
        <>
          <div className="grid grid-cols-2 gap-4 mb-4">
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

          <div className="rounded-xl p-4 mb-4" style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>
            <LegsTable trade={trade} instrument={session.instrument} />
          </div>

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

      {showFilters && (
        <div className="flex items-center gap-1 mb-3 flex-wrap no-print">
          {FILTERS.map(option => (
            <button key={option} onClick={() => onFilterChange?.(option)}
              className="px-2.5 py-1 rounded text-xs font-medium transition-all"
              style={{
                background: filter === option ? '#2563eb' : 'var(--surface-secondary)',
                color: filter === option ? 'white' : 'var(--text-secondary)',
                border: filter === option ? '1px solid #2563eb' : '1px solid var(--border)',
                cursor: 'pointer',
              }}>
              {option.replace(/_/g, ' ')}
            </button>
          ))}
          <span className="text-xs ml-auto" style={{ color: 'var(--text-secondary)' }}>
            Showing {visibleDecisions.length} of {decisions.length} rows
          </span>
        </div>
      )}

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
                      {d.action === 'ENTER' && cs && (
                        <CandidatePill cs={cs} instrument={session.instrument} />
                      )}
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
                      color: mtmVal == null ? 'var(--text-secondary)' : mtmVal >= 0 ? '#22c55e' : '#ef4444',
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
    </>
  )
}
