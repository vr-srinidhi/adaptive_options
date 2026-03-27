import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getResults, getSummary, clearResults } from '../api'
import MetricCard from '../components/MetricCard'
import { RegimeBadge, WLBadge } from '../components/RegimeBadge'
import { CumulativePnlChart } from '../components/PnlChart'

const fmtINR = (v) =>
  v == null ? '—' :
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(v)

const EXIT_LABELS = {
  PROFIT_TARGET: 'Profit target hit',
  HARD_EXIT: 'Hard stop (75%)',
  END_OF_DAY: 'End of day',
  NO_SIGNAL: '—',
}

export default function Dashboard() {
  const navigate = useNavigate()
  const [sessions, setSessions] = useState([])
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [clearing, setClearing] = useState(false)

  const load = async () => {
    setLoading(true)
    setError(null)
    try {
      const [resRows, resSummary] = await Promise.all([
        getResults({ limit: 200 }),
        getSummary(),
      ])
      setSessions(resRows.data)
      setSummary(resSummary.data)
    } catch (err) {
      setError(err.response?.data?.detail || err.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const handleClear = async () => {
    if (!window.confirm('Delete all backtest results?')) return
    setClearing(true)
    try {
      await clearResults()
      await load()
    } finally {
      setClearing(false)
    }
  }

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

  const totalPnl = summary?.totalPnl ?? 0

  return (
    <div className="max-w-6xl mx-auto p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-lg font-bold text-slate-100">Backtest Dashboard</h1>
          <p className="text-xs mt-0.5" style={{ color: 'var(--text-secondary)' }}>
            {summary?.totalSessions ?? 0} sessions · {summary?.totalTrades ?? 0} trades
          </p>
        </div>
        {sessions.length > 0 && (
          <button
            onClick={handleClear}
            disabled={clearing}
            className="px-4 py-1.5 rounded text-xs font-medium transition-all"
            style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)', color: '#ef4444', cursor: 'pointer' }}
          >
            {clearing ? 'Clearing…' : 'Clear All'}
          </button>
        )}
      </div>

      {sessions.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-24 gap-4">
          <div className="text-4xl">📊</div>
          <div className="text-sm" style={{ color: 'var(--text-secondary)' }}>No backtest results yet.</div>
          <button onClick={() => navigate('/backtest')}
            className="px-4 py-2 rounded text-sm font-medium"
            style={{ background: '#2563eb', color: 'white', cursor: 'pointer' }}>
            Run your first backtest →
          </button>
        </div>
      ) : (
        <>
          {/* Metric cards */}
          <div className="grid grid-cols-4 gap-3 mb-5">
            <MetricCard
              label="Total P&L"
              value={fmtINR(totalPnl)}
              color={totalPnl >= 0 ? '#22c55e' : '#ef4444'}
            />
            <MetricCard
              label="Win Rate"
              value={`${summary?.winRate ?? 0}%`}
              subtext={`${summary?.totalTrades ?? 0} trades`}
              color="#3b82f6"
            />
            <MetricCard
              label="Best Day"
              value={summary?.bestDay ? fmtINR(summary.bestDay.pnl) : '—'}
              color="#22c55e"
              onClick={summary?.bestDay ? () => navigate(`/tradebook/${summary.bestDay.id}`) : null}
            />
            <MetricCard
              label="Worst Day"
              value={summary?.worstDay ? fmtINR(summary.worstDay.pnl) : '—'}
              color="#ef4444"
              onClick={summary?.worstDay ? () => navigate(`/tradebook/${summary.worstDay.id}`) : null}
            />
          </div>

          {/* Cumulative P&L chart */}
          <div className="rounded-xl p-4 mb-5"
            style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>
            <div className="text-xs uppercase tracking-widest mb-3" style={{ color: 'var(--text-secondary)' }}>
              Cumulative P&L
            </div>
            <CumulativePnlChart sessions={sessions} />
          </div>

          {/* Results table */}
          <div className="rounded-xl overflow-hidden"
            style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>
            <table className="w-full text-xs">
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface-tertiary)' }}>
                  {['Date', 'Instrument', 'Regime', 'Strategy', 'Lots', 'Spot In', 'Spot Out', 'P&L', 'P&L %', 'Exit Reason', 'Result'].map(h => (
                    <th key={h} className="text-left px-3 py-2.5 font-medium uppercase tracking-wider"
                      style={{ color: 'var(--text-secondary)' }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sessions.map(s => {
                  const pnl = s.pnl ?? 0
                  const isNoTrade = s.wl === 'NO_TRADE'
                  return (
                    <tr
                      key={s.id}
                      className="table-row-hover"
                      style={{ borderBottom: '0.5px solid var(--border)' }}
                      onClick={() => !isNoTrade && navigate(`/tradebook/${s.id}`)}
                    >
                      <td className="px-3 py-2.5 font-medium" style={{ color: 'var(--text-primary)' }}>
                        {s.session_date}
                      </td>
                      <td className="px-3 py-2.5" style={{ color: 'var(--text-secondary)' }}>
                        {s.instrument}
                      </td>
                      <td className="px-3 py-2.5">
                        <RegimeBadge regime={s.regime} />
                      </td>
                      <td className="px-3 py-2.5" style={{ color: 'var(--text-primary)' }}>
                        {s.strategy?.replace(/_/g, ' ')}
                      </td>
                      <td className="px-3 py-2.5" style={{ color: 'var(--text-secondary)' }}>
                        {s.lots || '—'}
                      </td>
                      <td className="px-3 py-2.5" style={{ color: 'var(--text-secondary)' }}>
                        {s.spot_in ? s.spot_in.toLocaleString('en-IN') : '—'}
                      </td>
                      <td className="px-3 py-2.5" style={{ color: 'var(--text-secondary)' }}>
                        {s.spot_out ? s.spot_out.toLocaleString('en-IN') : '—'}
                      </td>
                      <td className="px-3 py-2.5 font-medium" style={{ color: pnl >= 0 ? '#22c55e' : '#ef4444' }}>
                        {isNoTrade ? '—' : fmtINR(pnl)}
                      </td>
                      <td className="px-3 py-2.5" style={{ color: pnl >= 0 ? '#22c55e' : '#ef4444' }}>
                        {isNoTrade ? '—' : `${(s.pnl_pct ?? 0).toFixed(2)}%`}
                      </td>
                      <td className="px-3 py-2.5" style={{ color: 'var(--text-secondary)' }}>
                        {EXIT_LABELS[s.exit_reason] ?? s.exit_reason}
                      </td>
                      <td className="px-3 py-2.5">
                        <WLBadge wl={s.wl} />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
            <div className="px-3 py-2 text-xs" style={{ color: 'var(--text-secondary)', borderTop: '0.5px solid var(--border)' }}>
              ← Click any row to view trade book
            </div>
          </div>
        </>
      )}
    </div>
  )
}
