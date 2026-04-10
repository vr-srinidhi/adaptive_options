import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { getPaperSession, getPaperDecisions, getPaperTrade, getPaperMarks } from '../api'
import { PnlProgressionChart } from '../components/PnlChart'

const fmtINR = v => v == null ? '—' :
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(v)
const fmtNum = (v, dp = 2) =>
  v == null ? '—' : v.toLocaleString('en-IN', { minimumFractionDigits: dp, maximumFractionDigits: dp })

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

export default function PaperTradeBook() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [session, setSession] = useState(null)
  const [decisions, setDecisions] = useState([])
  const [trade, setTrade] = useState(null)
  const [marks, setMarks] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('ALL')
  const [error, setError] = useState(null)

  useEffect(() => {
    Promise.all([
      getPaperSession(id),
      getPaperDecisions(id),
      getPaperTrade(id),
      getPaperMarks(id),
    ])
      .then(([sRes, dRes, tRes, mRes]) => {
        setSession(sRes.data)
        setDecisions(dRes.data)
        setTrade(tRes.data.trade)
        setMarks(mRes.data)
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
  const pnlColor = pnl == null ? 'var(--text-secondary)' : pnl >= 0 ? '#22c55e' : '#ef4444'

  const FILTERS = ['ALL', 'ENTER', 'HOLD', 'EXIT_TARGET', 'EXIT_STOP', 'EXIT_TIME', 'NO_TRADE']
  const visibleDecisions = filter === 'ALL' ? decisions : decisions.filter(d => d.action === filter)

  // Build chart data from marks (for MTM progression)
  const chartData = marks.map(m => ({ time: m.timestamp?.slice(11, 16), spot: 0, pnl: m.total_mtm }))

  return (
    <div className="max-w-6xl mx-auto p-6">
      {/* Back */}
      <button onClick={() => navigate('/paper/sessions')}
        className="text-xs mb-4 flex items-center gap-1"
        style={{ color: 'var(--text-secondary)', cursor: 'pointer', background: 'none', border: 'none', padding: 0 }}>
        ← Sessions
      </button>

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
              {trade?.exit_reason?.replace(/_/g, ' ')}
            </div>
          </div>
        )}
      </div>

      {/* Trade summary + MTM chart (if trade opened) */}
      {trade && (
        <>
          <div className="grid grid-cols-2 gap-4 mb-4">
            <div className="rounded-xl p-4" style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>
              <div className="text-xs uppercase tracking-widest mb-3" style={{ color: 'var(--text-secondary)' }}>
                Trade Summary
              </div>
              <KV label="Bias" value={trade.bias} />
              <KV label="Spread" value={`${trade.long_strike}${trade.option_type} / ${trade.short_strike}${trade.option_type}`} />
              <KV label="Entry Debit" value={`₹${fmtNum(trade.entry_debit)}`} />
              <KV label="Approved Lots" value={`${trade.approved_lots} × ${trade.lot_size}`} />
              <KV label="Max Loss" value={fmtINR(trade.total_max_loss)} valueColor="#ef4444" />
              <KV label="Target" value={fmtINR(trade.target_profit)} valueColor="#22c55e" />
              <KV label="Realized P&L" value={fmtINR(trade.realized_gross_pnl)}
                valueColor={pnlColor} />
            </div>

            <div className="rounded-xl p-4" style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>
              <div className="text-xs uppercase tracking-widest mb-3" style={{ color: 'var(--text-secondary)' }}>
                Timing
              </div>
              <KV label="Entry" value={trade.entry_time?.slice(11, 16) ?? '—'} />
              <KV label="Exit" value={trade.exit_time?.slice(11, 16) ?? '—'} />
              <KV label="Exit Reason" value={trade.exit_reason?.replace(/_/g, ' ') ?? '—'} />
              <KV label="Expiry" value={trade.expiry ?? '—'} />
              {trade.legs?.map(l => (
                <KV key={l.leg_side}
                  label={`${l.leg_side} ${l.strike}${l.option_type}`}
                  value={`₹${fmtNum(l.entry_price)} → ₹${fmtNum(l.exit_price)}`} />
              ))}
            </div>
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
      <div className="flex items-center gap-1 mb-3 flex-wrap">
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
        <div style={{ maxHeight: 480, overflowY: 'auto' }}>
          <table className="w-full text-xs">
            <thead style={{ position: 'sticky', top: 0, zIndex: 1, background: 'var(--surface-tertiary)' }}>
              <tr style={{ borderBottom: '1px solid var(--border)' }}>
                {['Time', 'Spot', 'OR Hi/Lo', 'State', 'Action', 'Reason Code', 'Max Loss', 'Target / MTM'].map(h => (
                  <th key={h} className="text-left px-3 py-2.5 font-medium uppercase tracking-wider"
                    style={{ color: 'var(--text-secondary)' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visibleDecisions.map((d, i) => {
                const isTrade = d.trade_state === 'OPEN_TRADE'
                const mtmVal = d.action === 'HOLD' || d.action?.startsWith('EXIT')
                  ? marks.find(m => m.timestamp === d.timestamp)?.total_mtm
                  : null
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
                    <td className="px-3 py-2" style={{ color: 'var(--text-secondary)', maxWidth: 200 }}>
                      <span title={d.reason_text} style={{ cursor: 'help' }}>
                        {d.reason_code}
                      </span>
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
          Hover Reason Code column to see full explanation · {decisions.length} total minute rows
        </div>
      </div>
    </div>
  )
}
