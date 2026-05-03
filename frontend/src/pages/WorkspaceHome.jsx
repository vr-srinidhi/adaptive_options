import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer,
} from 'recharts'
import { getStrategyDashboard } from '../api/index.js'

function fmtINR(v) {
  if (v == null) return '—'
  const abs = Math.abs(v)
  const s = abs >= 1_00_000
    ? `₹${(abs / 1_00_000).toFixed(2)}L`
    : `₹${abs.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
  return v < 0 ? `−${s}` : `+${s}`
}

function SparkTooltip({ active, payload }) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  return (
    <div style={{
      background: 'var(--surface-2)',
      border: '1px solid var(--border)',
      borderRadius: 6,
      padding: '6px 10px',
      fontSize: 11,
    }}>
      <div style={{ color: 'var(--text-secondary)' }}>{d.date}</div>
      <div style={{ color: d.cumulative_pnl >= 0 ? '#4ade80' : '#f87171', fontWeight: 600 }}>
        {fmtINR(d.cumulative_pnl)}
      </div>
    </div>
  )
}

function StatBox({ label, value, color }) {
  return (
    <div style={{ flex: 1 }}>
      <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 3 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 700, color: color || 'var(--text-primary)' }}>{value}</div>
    </div>
  )
}

function StrategyCard({ s, onClick }) {
  const gradientId = `grad-${s.strategy_id}`
  const isPositive = s.net_pnl >= 0
  const lineColor = isPositive ? '#4ade80' : '#f87171'

  return (
    <div
      onClick={onClick}
      style={{
        background: 'var(--surface-2)',
        border: '1px solid var(--border)',
        borderRadius: 12,
        padding: '20px 24px',
        cursor: 'pointer',
        transition: 'border-color 0.15s, box-shadow 0.15s',
      }}
      onMouseEnter={e => {
        e.currentTarget.style.borderColor = '#6366f1'
        e.currentTarget.style.boxShadow = '0 0 0 1px #6366f133'
      }}
      onMouseLeave={e => {
        e.currentTarget.style.borderColor = 'var(--border)'
        e.currentTarget.style.boxShadow = 'none'
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 16 }}>
        <div>
          <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--text-primary)' }}>{s.strategy_name}</div>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>
            {s.date_range.from} → {s.date_range.to} · {s.total_runs} sessions
          </div>
        </div>
        <div style={{ fontSize: 22, fontWeight: 800, color: lineColor }}>
          {fmtINR(s.net_pnl)}
        </div>
      </div>

      <div style={{ display: 'flex', gap: 16, marginBottom: 20 }}>
        <StatBox label="Win Rate" value={`${s.win_rate}%`} color={s.win_rate >= 50 ? '#4ade80' : '#f87171'} />
        <StatBox label="Wins / Losses" value={`${s.wins} / ${s.losses}`} />
        <StatBox label="Avg Win" value={fmtINR(s.avg_win)} color="#4ade80" />
        <StatBox label="Avg Loss" value={fmtINR(s.avg_loss)} color="#f87171" />
      </div>

      <div style={{ height: 90 }}>
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={s.daily_pnl} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={lineColor} stopOpacity={0.25} />
                <stop offset="95%" stopColor={lineColor} stopOpacity={0} />
              </linearGradient>
            </defs>
            <XAxis dataKey="date" hide />
            <YAxis hide />
            <Tooltip content={<SparkTooltip />} />
            <Area
              type="monotone"
              dataKey="cumulative_pnl"
              stroke={lineColor}
              strokeWidth={1.5}
              fill={`url(#${gradientId})`}
              dot={false}
              activeDot={{ r: 3 }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      <div style={{ textAlign: 'right', fontSize: 11, color: 'var(--text-secondary)', marginTop: 8 }}>
        Click for full report →
      </div>
    </div>
  )
}

export default function WorkspaceHome() {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    getStrategyDashboard()
      .then(r => setData(r.data))
      .catch(e => setError(e.response?.data?.detail || e.message))
  }, [])

  if (error) {
    return (
      <div style={{ padding: 40, color: '#f87171', fontFamily: 'monospace' }}>
        Error: {error}
      </div>
    )
  }

  const strategies = data?.strategies || []

  return (
    <div style={{ padding: '32px 40px', maxWidth: 1100, margin: '0 auto' }}>
      <div style={{ marginBottom: 28 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <h1 style={{ fontSize: 26, fontWeight: 800, color: 'var(--text-primary)', margin: 0 }}>
              Strategy Dashboard
            </h1>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 4 }}>
              Cumulative P&L across all backtest sessions. Click a card for the full report.
            </p>
          </div>
          <button
            onClick={() => navigate('/workbench/compare')}
            style={{
              background: 'var(--surface-2)', border: '1px solid var(--border)',
              color: 'var(--text-primary)', borderRadius: 8, padding: '8px 16px',
              fontSize: 13, fontWeight: 600, cursor: 'pointer', whiteSpace: 'nowrap',
            }}
          >
            ⚖ Day Compare
          </button>
        </div>
      </div>

      {!data ? (
        <div style={{ color: 'var(--text-secondary)', fontSize: 14 }}>Loading…</div>
      ) : strategies.length === 0 ? (
        <div style={{ color: 'var(--text-secondary)', fontSize: 14 }}>
          No completed strategy runs yet. Run a backtest from the{' '}
          <span
            style={{ color: '#6366f1', cursor: 'pointer', textDecoration: 'underline' }}
            onClick={() => navigate('/workbench/run')}
          >
            Run Builder
          </span>.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
          {strategies.map(s => (
            <StrategyCard
              key={s.strategy_id}
              s={s}
              onClick={() => navigate(`/workbench/report/${s.strategy_id}`)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
