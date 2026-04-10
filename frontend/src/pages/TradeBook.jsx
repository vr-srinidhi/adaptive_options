import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { getSession } from '../api'
import { RegimeBadge, RegimeDetailBadge, WLBadge, ActionBadge, TypeBadge, SignalBadge, ScoreBadge } from '../components/RegimeBadge'
import { PnlProgressionChart } from '../components/PnlChart'

const fmtINR = (v) =>
  v == null ? '—' :
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(v)

const fmtNum = (v, dp = 2) =>
  v == null ? '—' : v.toLocaleString('en-IN', { minimumFractionDigits: dp, maximumFractionDigits: dp })

const EXIT_LABELS = {
  PROFIT_TARGET: 'Profit target hit',
  HARD_EXIT: 'Hard stop (75%)',
  END_OF_DAY: 'End of day',
  NO_SIGNAL: '—',
}

function KV({ label, value, valueColor }) {
  return (
    <div className="flex justify-between items-center py-1 border-b" style={{ borderColor: 'rgba(51,65,85,0.5)' }}>
      <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{label}</span>
      <span className="text-xs font-medium" style={{ color: valueColor || 'var(--text-primary)' }}>{value}</span>
    </div>
  )
}

export default function TradeBook() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [session, setSession] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    getSession(id)
      .then(r => setSession(r.data))
      .catch(err => setError(err.response?.data?.detail || err.message))
      .finally(() => setLoading(false))
  }, [id])

  if (loading) return (
    <div className="flex items-center justify-center h-64 gap-2" style={{ color: 'var(--text-secondary)' }}>
      <span className="spinner" /> Loading…
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

  const pnl = session.pnl ?? 0
  const pnlColor = pnl >= 0 ? '#22c55e' : '#ef4444'

  return (
    <div className="max-w-5xl mx-auto p-6">
      {/* Back link */}
      <button
        onClick={() => navigate('/dashboard')}
        className="text-xs mb-4 flex items-center gap-1 transition-colors"
        style={{ color: 'var(--text-secondary)', cursor: 'pointer', background: 'none', border: 'none', padding: 0 }}
      >
        ← Dashboard
      </button>

      {/* Hero row */}
      <div className="flex items-start justify-between mb-5">
        <div>
          <h1 className="text-lg font-bold text-slate-100">
            {session.session_date} — {session.instrument === 'NIFTY' ? 'Nifty 50' : 'Bank Nifty'}
          </h1>
          <div className="flex items-center gap-2 mt-2">
            <RegimeBadge regime={session.regime} />
            {session.regime_detail && session.regime_detail !== session.regime && (
              <RegimeDetailBadge regime={session.regime_detail} />
            )}
            <span className="text-xs px-2 py-0.5 rounded font-medium"
              style={{ background: 'var(--surface-tertiary)', color: 'var(--text-primary)' }}>
              {session.strategy?.replace(/_/g, ' ')}
            </span>
            <WLBadge wl={session.wl} />
            <span className="text-xs px-2 py-0.5 rounded"
              style={{ background: 'var(--surface-tertiary)', color: 'var(--text-secondary)' }}>
              {EXIT_LABELS[session.exit_reason] ?? session.exit_reason}
            </span>
            <span className="text-xs font-medium" style={{ color: 'var(--text-secondary)' }}>
              {(session.pnl_pct ?? 0).toFixed(2)}% cap
            </span>
          </div>
        </div>
        <div className="text-2xl font-bold" style={{ color: pnlColor }}>
          {fmtINR(pnl)}
        </div>
      </div>

      {/* Market state + Position summary */}
      <div className="grid grid-cols-2 gap-4 mb-4">
        <div className="rounded-xl p-4"
          style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>
          <div className="text-xs uppercase tracking-widest mb-3" style={{ color: 'var(--text-secondary)' }}>
            Market State at Entry
          </div>
          <KV label="EMA 9" value={fmtNum(session.ema5)} />
          <KV label="EMA 21" value={fmtNum(session.ema20)} />
          <KV label="RSI (14)" value={fmtNum(session.rsi14)} />
          <KV label="ATR (14)" value={fmtNum(session.atr14)} />
          <KV label="IV Rank" value={`${session.iv_rank}%`} />
          <KV label="Spot In" value={fmtNum(session.spot_in)} />
          <KV label="Spot Out" value={fmtNum(session.spot_out)} />
        </div>

        <div className="rounded-xl p-4"
          style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>
          <div className="text-xs uppercase tracking-widest mb-3" style={{ color: 'var(--text-secondary)' }}>
            Position Summary
          </div>
          <KV label="Lots" value={session.lots ?? '—'} />
          <KV label="Capital" value={fmtINR(session.capital)} />
          <KV label="Max Profit" value={fmtINR(session.max_profit)} valueColor="#22c55e" />
          <KV label="Max Loss" value={fmtINR(session.max_loss)} valueColor="#ef4444" />
          <KV label="R-Multiple" value={session.r_multiple != null ? `${fmtNum(session.r_multiple)}R` : '—'}
            valueColor={session.r_multiple >= 1 ? '#22c55e' : session.r_multiple != null ? '#f59e0b' : null} />
          <KV label="Entry" value={session.entry_time ?? '—'} />
          <KV label="Exit" value={session.exit_time ?? '—'} />
        </div>
      </div>

      {/* Signal Analysis */}
      {session.signal_type && session.signal_type !== 'NO_SIGNAL' && (
        <div className="rounded-xl p-4 mb-4"
          style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>
          <div className="text-xs uppercase tracking-widest mb-3" style={{ color: 'var(--text-secondary)' }}>
            Signal Analysis
          </div>
          <div className="flex items-center gap-4 flex-wrap">
            <div className="flex flex-col gap-1">
              <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>Regime Detail</span>
              <RegimeDetailBadge regime={session.regime_detail} />
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>Signal Type</span>
              <SignalBadge signal={session.signal_type} />
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>Signal Score</span>
              <div className="flex items-center gap-1">
                <ScoreBadge score={session.signal_score} />
                <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>/100</span>
              </div>
            </div>
            <div className="flex flex-col gap-1">
              <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>Data Source</span>
              <span className="text-xs font-medium px-2 py-0.5 rounded"
                style={{ background: 'var(--surface-tertiary)', color: 'var(--text-secondary)' }}>
                {session.data_source ?? '—'}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Option legs */}
      {session.legs?.length > 0 && (
        <div className="rounded-xl overflow-hidden mb-4"
          style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>
          <div className="px-4 py-3 text-xs uppercase tracking-widest"
            style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)' }}>
            Option Legs (Trade Book)
          </div>
          <table className="w-full text-xs">
            <thead>
              <tr style={{ borderBottom: '0.5px solid var(--border)', background: 'var(--surface-tertiary)' }}>
                {['Action', 'Type', 'Strike', 'Delta', 'Entry Px', 'Exit Px', 'Lots', 'Leg P&L'].map(h => (
                  <th key={h} className="text-left px-3 py-2.5 font-medium uppercase tracking-wider"
                    style={{ color: 'var(--text-secondary)' }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {session.legs.map(leg => (
                <tr key={leg.id} style={{ borderBottom: '0.5px solid var(--border)' }}>
                  <td className="px-3 py-2.5"><ActionBadge act={leg.act} /></td>
                  <td className="px-3 py-2.5"><TypeBadge typ={leg.typ} /></td>
                  <td className="px-3 py-2.5 font-medium" style={{ color: 'var(--text-primary)' }}>
                    {leg.strike?.toLocaleString('en-IN')}
                  </td>
                  <td className="px-3 py-2.5" style={{ color: 'var(--text-secondary)' }}>
                    {leg.delta?.toFixed(2)}
                  </td>
                  <td className="px-3 py-2.5" style={{ color: 'var(--text-secondary)' }}>
                    ₹{fmtNum(leg.ep)}
                  </td>
                  <td className="px-3 py-2.5" style={{ color: 'var(--text-secondary)' }}>
                    ₹{fmtNum(leg.ep2)}
                  </td>
                  <td className="px-3 py-2.5" style={{ color: 'var(--text-secondary)' }}>
                    {leg.lots}
                  </td>
                  <td className="px-3 py-2.5 font-medium"
                    style={{ color: (leg.legPnl ?? 0) >= 0 ? '#22c55e' : '#ef4444' }}>
                    {fmtINR(leg.legPnl)}
                  </td>
                </tr>
              ))}
              {/* Sum row */}
              <tr style={{ background: 'var(--surface-tertiary)' }}>
                <td colSpan={7} className="px-3 py-2.5 font-medium text-right"
                  style={{ color: 'var(--text-secondary)' }}>
                  Total P&L
                </td>
                <td className="px-3 py-2.5 font-bold" style={{ color: pnlColor }}>
                  {fmtINR(pnl)}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {/* P&L Progression chart */}
      {session.min_data?.length > 0 && (
        <div className="rounded-xl p-4"
          style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>
          <div className="flex items-center justify-between mb-3">
            <div className="text-xs uppercase tracking-widest" style={{ color: 'var(--text-secondary)' }}>
              P&L Progression (1-min intervals)
            </div>
            <div className="text-xs" style={{ color: 'var(--text-secondary)' }}>
              {session.min_data.length} candles · {session.entry_time} → {session.exit_time}
            </div>
          </div>
          <PnlProgressionChart data={session.min_data} />
        </div>
      )}
    </div>
  )
}
