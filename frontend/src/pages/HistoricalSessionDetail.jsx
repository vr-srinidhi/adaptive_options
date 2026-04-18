import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { getHistSession, getHistDecisions, getHistTrade, getHistMarks } from '../api'
import { PnlProgressionChart } from '../components/PnlChart'

const fmtINR = v => v == null ? '—' :
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(v)
const fmtNum = (v, dp = 2) =>
  v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: dp, maximumFractionDigits: dp })
const fmtTime = ts => ts ? ts.substring(11, 16) : '—'

const ACTION_STYLES = {
  ENTER:       { bg: 'rgba(34,197,94,0.15)',   border: 'rgba(34,197,94,0.4)',   text: '#22c55e' },
  HOLD:        { bg: 'rgba(59,130,246,0.10)',  border: 'rgba(59,130,246,0.3)',  text: '#3b82f6' },
  EXIT_TARGET: { bg: 'rgba(34,197,94,0.20)',   border: 'rgba(34,197,94,0.5)',   text: '#22c55e' },
  EXIT_STOP:   { bg: 'rgba(239,68,68,0.15)',   border: 'rgba(239,68,68,0.4)',   text: '#ef4444' },
  EXIT_TIME:   { bg: 'rgba(245,158,11,0.15)',  border: 'rgba(245,158,11,0.4)',  text: '#f59e0b' },
  NO_TRADE:    { bg: 'rgba(100,116,139,0.10)', border: 'rgba(100,116,139,0.3)', text: '#64748b' },
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

const TABS = ['Summary', 'Decisions', 'Trade']

export default function HistoricalSessionDetail() {
  const { sessionId } = useParams()
  const navigate = useNavigate()
  const [session, setSession] = useState(null)
  const [decisions, setDecisions] = useState([])
  const [trade, setTrade] = useState(null)
  const [marks, setMarks] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [tab, setTab] = useState('Summary')

  useEffect(() => {
    Promise.all([
      getHistSession(sessionId),
      getHistDecisions(sessionId),
      getHistTrade(sessionId),
      getHistMarks(sessionId),
    ]).then(([sRes, dRes, tRes, mRes]) => {
      setSession(sRes.data)
      setDecisions(dRes.data)
      setTrade(tRes.data?.trade || null)
      setMarks(mRes.data || [])
    }).catch(err => setError(err.response?.data?.detail || err.message))
      .finally(() => setLoading(false))
  }, [sessionId])

  if (loading) return (
    <div className="flex items-center justify-center h-64 gap-2" style={{ color: 'var(--text-secondary)' }}>
      <span className="spinner" /> Loading session…
    </div>
  )

  if (error) return (
    <div className="max-w-4xl mx-auto p-6">
      <div className="px-4 py-3 rounded text-sm"
        style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#ef4444' }}>
        {error}
      </div>
    </div>
  )

  if (!session) return null

  const pnlColor = session.summary_pnl == null ? 'var(--text-secondary)'
    : session.summary_pnl >= 0 ? '#22c55e' : '#ef4444'

  const chartData = marks.map(m => ({
    time: fmtTime(m.timestamp),
    netMTM: m.estimated_net_mtm ?? m.total_mtm,
  }))

  const batchId = session.batch_id

  return (
    <div className="max-w-5xl mx-auto p-6">
      {/* Breadcrumb */}
      <div className="flex items-center gap-2 mb-2 text-xs" style={{ color: 'var(--text-secondary)' }}>
        <button onClick={() => navigate('/backtests')} className="hover:text-slate-200 transition-colors" style={{ cursor: 'pointer', background: 'none', border: 'none', padding: 0 }}>Backtests</button>
        {batchId && <>
          <span>/</span>
          <button onClick={() => navigate(`/backtests/${batchId}`)} className="hover:text-slate-200 transition-colors" style={{ cursor: 'pointer', background: 'none', border: 'none', padding: 0 }}>Batch</button>
        </>}
        <span>/</span>
        <span className="text-slate-300">{session.session_date}</span>
      </div>

      <div className="flex items-baseline gap-3 mb-1">
        <h1 className="text-lg font-bold text-slate-100">
          {session.instrument} · {session.session_date}
        </h1>
        <span className="text-xs px-2 py-0.5 rounded font-semibold"
          style={{ background: 'rgba(148,163,184,0.1)', border: '1px solid rgba(148,163,184,0.25)', color: '#94a3b8' }}>
          {session.final_session_state || session.status}
        </span>
      </div>
      <p className="text-xs mb-5" style={{ color: 'var(--text-secondary)' }}>
        Historical backtest · {session.source_mode} · {session.decision_count} decisions
      </p>

      {/* P&L Hero */}
      <div className="rounded-xl p-5 mb-6" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
        <div className="text-xs mb-1" style={{ color: 'var(--text-secondary)' }}>Net P&L</div>
        <div className="text-3xl font-bold font-mono" style={{ color: pnlColor }}>
          {fmtINR(session.summary_pnl)}
        </div>
        {chartData.length > 0 && (
          <div className="mt-4 h-32">
            <PnlProgressionChart data={chartData} />
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 mb-4">
        {TABS.map(t => (
          <button key={t} onClick={() => setTab(t)}
            className="px-4 py-1.5 rounded text-xs font-medium transition-colors"
            style={{
              background: tab === t ? '#f59e0b' : 'var(--surface)',
              color: tab === t ? '#0f172a' : 'var(--text-secondary)',
              border: tab === t ? 'none' : '1px solid var(--border)',
              cursor: 'pointer',
            }}>
            {t}
          </button>
        ))}
      </div>

      {/* Summary tab */}
      {tab === 'Summary' && (
        <div className="grid grid-cols-2 gap-4">
          <div className="rounded-xl p-4" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
            <div className="text-xs font-semibold mb-3" style={{ color: 'var(--text-secondary)' }}>SESSION</div>
            <KV label="Date" value={session.session_date} />
            <KV label="Instrument" value={session.instrument} />
            <KV label="Capital" value={fmtINR(session.capital)} />
            <KV label="Source" value={session.source_mode} />
            <KV label="Final state" value={session.final_session_state || '—'} />
            <KV label="Decisions" value={session.decision_count} />
          </div>
          {trade && (
            <div className="rounded-xl p-4" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
              <div className="text-xs font-semibold mb-3" style={{ color: 'var(--text-secondary)' }}>TRADE</div>
              <KV label="Bias" value={trade.bias} />
              <KV label="Spread" value={`${trade.long_strike} / ${trade.short_strike} ${trade.option_type}`} />
              <KV label="Lots" value={trade.approved_lots} />
              <KV label="Entry debit" value={fmtNum(trade.entry_debit)} />
              <KV label="Gross P&L" value={fmtINR(trade.realized_gross_pnl)}
                valueColor={trade.realized_gross_pnl == null ? undefined : trade.realized_gross_pnl >= 0 ? '#22c55e' : '#ef4444'} />
              <KV label="Net P&L" value={fmtINR(trade.realized_net_pnl)}
                valueColor={trade.realized_net_pnl == null ? undefined : trade.realized_net_pnl >= 0 ? '#22c55e' : '#ef4444'} />
              <KV label="Charges" value={fmtINR(trade.charges)} />
              <KV label="Exit reason" value={trade.exit_reason || '—'} />
            </div>
          )}
        </div>
      )}

      {/* Decisions tab */}
      {tab === 'Decisions' && (
        <div className="rounded-xl overflow-hidden" style={{ border: '1px solid var(--border)' }}>
          <table className="w-full text-xs">
            <thead>
              <tr style={{ background: 'var(--surface)', borderBottom: '1px solid var(--border)' }}>
                {['Time', 'Spot', 'Action', 'Gate', 'State', 'Reason'].map(h => (
                  <th key={h} className="px-3 py-2.5 text-left font-semibold"
                    style={{ color: 'var(--text-secondary)' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {decisions.map((d, i) => (
                <tr key={d.id}
                  style={{ borderBottom: i < decisions.length - 1 ? '1px solid rgba(51,65,85,0.4)' : undefined }}>
                  <td className="px-3 py-2 font-mono text-slate-400">{fmtTime(d.timestamp)}</td>
                  <td className="px-3 py-2 font-mono text-slate-300">{fmtNum(d.spot_close, 0)}</td>
                  <td className="px-3 py-2"><ActionBadge action={d.action} /></td>
                  <td className="px-3 py-2 text-slate-500">{d.rejection_gate || '—'}</td>
                  <td className="px-3 py-2 text-slate-400">{d.session_state || '—'}</td>
                  <td className="px-3 py-2 text-slate-500 max-w-xs truncate" title={d.reason_text || d.reason_code}>
                    {d.reason_code}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Trade tab */}
      {tab === 'Trade' && !trade && (
        <div className="flex flex-col items-center justify-center py-20" style={{ color: 'var(--text-secondary)' }}>
          <div className="text-3xl mb-2">—</div>
          <div className="text-sm">No trade was taken this session.</div>
        </div>
      )}
      {tab === 'Trade' && trade && (
        <div className="space-y-4">
          <div className="rounded-xl p-4" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
            <div className="text-xs font-semibold mb-3" style={{ color: 'var(--text-secondary)' }}>TRADE HEADER</div>
            <div className="grid grid-cols-3 gap-x-8">
              <div>
                <KV label="Entry" value={fmtTime(trade.entry_time)} />
                <KV label="Exit" value={fmtTime(trade.exit_time)} />
                <KV label="Bias" value={trade.bias} />
                <KV label="Exit reason" value={trade.exit_reason || '—'} />
              </div>
              <div>
                <KV label="Long strike" value={trade.long_strike} />
                <KV label="Short strike" value={trade.short_strike} />
                <KV label="Option type" value={trade.option_type} />
                <KV label="Lots" value={trade.approved_lots} />
              </div>
              <div>
                <KV label="Entry debit" value={fmtNum(trade.entry_debit)} />
                <KV label="Max loss" value={fmtINR(trade.total_max_loss)} />
                <KV label="Target" value={fmtINR(trade.target_profit)} />
                <KV label="Gross P&L" value={fmtINR(trade.realized_gross_pnl)}
                  valueColor={trade.realized_gross_pnl == null ? undefined : trade.realized_gross_pnl >= 0 ? '#22c55e' : '#ef4444'} />
                <KV label="Charges" value={fmtINR(trade.charges)} />
                <KV label="Net P&L" value={fmtINR(trade.realized_net_pnl)}
                  valueColor={trade.realized_net_pnl == null ? undefined : trade.realized_net_pnl >= 0 ? '#22c55e' : '#ef4444'} />
              </div>
            </div>
          </div>

          {marks.length > 0 && (
            <div className="rounded-xl overflow-hidden" style={{ border: '1px solid var(--border)' }}>
              <div className="px-4 py-2.5" style={{ background: 'var(--surface)', borderBottom: '1px solid var(--border)' }}>
                <span className="text-xs font-semibold" style={{ color: 'var(--text-secondary)' }}>
                  MINUTE MARKS ({marks.length})
                </span>
              </div>
              <table className="w-full text-xs">
                <thead>
                  <tr style={{ background: 'var(--surface)', borderBottom: '1px solid var(--border)' }}>
                    {['Time', 'Long leg', 'Short leg', 'Gross MTM', 'Net MTM', 'Action'].map(h => (
                      <th key={h} className="px-3 py-2 text-left font-semibold"
                        style={{ color: 'var(--text-secondary)' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {marks.map((m, i) => (
                    <tr key={i} style={{ borderBottom: i < marks.length - 1 ? '1px solid rgba(51,65,85,0.3)' : undefined }}>
                      <td className="px-3 py-2 font-mono text-slate-400">{fmtTime(m.timestamp)}</td>
                      <td className="px-3 py-2 font-mono text-slate-300">{fmtNum(m.long_leg_price)}</td>
                      <td className="px-3 py-2 font-mono text-slate-300">{fmtNum(m.short_leg_price)}</td>
                      <td className="px-3 py-2 font-mono"
                        style={{ color: m.gross_mtm == null ? 'var(--text-secondary)' : m.gross_mtm >= 0 ? '#22c55e' : '#ef4444' }}>
                        {fmtINR(m.gross_mtm)}
                      </td>
                      <td className="px-3 py-2 font-mono"
                        style={{ color: m.estimated_net_mtm == null ? 'var(--text-secondary)' : m.estimated_net_mtm >= 0 ? '#22c55e' : '#ef4444' }}>
                        {fmtINR(m.estimated_net_mtm)}
                      </td>
                      <td className="px-3 py-2"><ActionBadge action={m.action} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
