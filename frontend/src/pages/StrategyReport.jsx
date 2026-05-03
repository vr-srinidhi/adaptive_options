import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, ReferenceLine, LineChart, Line, Cell,
} from 'recharts'
import { getStrategyDashboard } from '../api/index.js'

function fmtINR(v, opts = {}) {
  if (v == null) return '—'
  const abs = Math.abs(v)
  const s = abs >= 1_00_000
    ? `₹${(abs / 1_00_000).toFixed(2)}L`
    : `₹${abs.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
  const sign = v < 0 ? '−' : (opts.forceSign ? '+' : '')
  return `${sign}${s}`
}

function MetricCard({ label, value, sub, color }) {
  return (
    <div style={{
      background: 'var(--surface-2)',
      border: '1px solid var(--border)',
      borderRadius: 10,
      padding: '14px 18px',
      flex: 1,
      minWidth: 120,
    }}>
      <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 800, color: color || 'var(--text-primary)' }}>{value}</div>
      {sub && <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4 }}>{sub}</div>}
    </div>
  )
}

function SectionTitle({ title }) {
  return (
    <div style={{
      fontSize: 14,
      fontWeight: 700,
      color: 'var(--text-primary)',
      marginBottom: 12,
      marginTop: 32,
      letterSpacing: '0.03em',
      textTransform: 'uppercase',
    }}>
      {title}
    </div>
  )
}

function MonthBarTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  return (
    <div style={{
      background: 'var(--surface-2)', border: '1px solid var(--border)',
      borderRadius: 6, padding: '8px 12px', fontSize: 12,
    }}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{label}</div>
      <div style={{ color: d.pnl >= 0 ? '#4ade80' : '#f87171' }}>
        P&L: {fmtINR(d.pnl, { forceSign: true })}
      </div>
      <div style={{ color: 'var(--text-secondary)' }}>
        {d.wins}W / {d.losses}L of {d.runs} days
      </div>
    </div>
  )
}

function DailyBarTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  const isLocked = d.wings_locked
  return (
    <div style={{
      background: 'var(--surface-2)', border: '1px solid var(--border)',
      borderRadius: 6, padding: '8px 12px', fontSize: 12,
    }}>
      <div style={{ color: 'var(--text-secondary)', marginBottom: 2 }}>{d.date}</div>
      <div style={{ color: d.pnl >= 0 ? '#4ade80' : '#f87171', fontWeight: 600 }}>
        {fmtINR(d.pnl, { forceSign: true })}
      </div>
      <div style={{ color: 'var(--text-secondary)', fontSize: 11 }}>{d.exit_reason}</div>
      {isLocked && (
        <div style={{ color: d.lock_reason === 'loss' ? '#fb923c' : '#22d3ee', fontSize: 11, marginTop: 3 }}>
          {d.lock_reason === 'loss' ? '🛡️ Loss lock' : '🔒 Profit lock'} @ {d.lock_time}
        </div>
      )}
    </div>
  )
}

function CumTooltip({ active, payload }) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload
  return (
    <div style={{
      background: 'var(--surface-2)', border: '1px solid var(--border)',
      borderRadius: 6, padding: '8px 12px', fontSize: 12,
    }}>
      <div style={{ color: 'var(--text-secondary)', marginBottom: 2 }}>{d.date}</div>
      <div style={{ color: d.cumulative_pnl >= 0 ? '#4ade80' : '#f87171', fontWeight: 600 }}>
        {fmtINR(d.cumulative_pnl, { forceSign: true })}
      </div>
    </div>
  )
}

export default function StrategyReport() {
  const { strategyId } = useParams()
  const navigate = useNavigate()
  const [strategy, setStrategy] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    getStrategyDashboard()
      .then(r => {
        const found = (r.data.strategies || []).find(s => s.strategy_id === strategyId)
        if (!found) setError('Strategy not found or no runs yet.')
        else setStrategy(found)
      })
      .catch(e => setError(e.response?.data?.detail || e.message))
  }, [strategyId])

  if (error) {
    return (
      <div style={{ padding: 40 }}>
        <div style={{ color: '#f87171', marginBottom: 16 }}>{error}</div>
        <button onClick={() => navigate('/workbench')} style={{ color: '#6366f1', background: 'none', border: 'none', cursor: 'pointer', fontSize: 13 }}>
          ← Back to Dashboard
        </button>
      </div>
    )
  }

  if (!strategy) {
    return <div style={{ padding: 40, color: 'var(--text-secondary)' }}>Loading…</div>
  }

  const s = strategy
  const isPositive = s.net_pnl >= 0

  return (
    <div style={{ padding: '32px 40px', maxWidth: 1100, margin: '0 auto' }}>
      {/* Back link */}
      <button
        onClick={() => navigate('/workbench')}
        style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#6366f1', fontSize: 13, padding: 0, marginBottom: 20 }}
      >
        ← Dashboard
      </button>

      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 28 }}>
        <div>
          <h1 style={{ fontSize: 26, fontWeight: 800, color: 'var(--text-primary)', margin: 0 }}>
            {s.strategy_name}
          </h1>
          <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginTop: 4 }}>
            {s.date_range.from} → {s.date_range.to}
          </div>
        </div>
        <div style={{ fontSize: 28, fontWeight: 900, color: isPositive ? '#4ade80' : '#f87171' }}>
          {fmtINR(s.net_pnl, { forceSign: true })}
        </div>
      </div>

      {/* Key metrics */}
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <MetricCard label="Total Sessions" value={s.total_runs} sub={`${s.date_range.from} – ${s.date_range.to}`} />
        <MetricCard label="Win Rate" value={`${s.win_rate}%`} sub={`${s.wins}W / ${s.losses}L`} color={s.win_rate >= 50 ? '#4ade80' : '#f87171'} />
        <MetricCard label="Avg Win" value={fmtINR(s.avg_win)} color="#4ade80" />
        <MetricCard label="Avg Loss" value={fmtINR(s.avg_loss)} color="#f87171" />
        <MetricCard label="Best Day" value={fmtINR(s.best_day?.pnl, { forceSign: true })} sub={s.best_day?.date} color="#4ade80" />
        <MetricCard label="Worst Day" value={fmtINR(s.worst_day?.pnl, { forceSign: true })} sub={s.worst_day?.date} color="#f87171" />
      </div>

      {/* Profit Lock stats — only shown for straddle_profit_lock strategies */}
      {s.lock_stats && (() => {
        const ls = s.lock_stats
        const lockWinRate = ls.count ? Math.round(ls.wins / ls.count * 100) : 0
        return (
          <>
            <SectionTitle title="Profit Lock Stats" />
            <div style={{
              background: 'linear-gradient(135deg, rgba(34,211,238,0.08) 0%, rgba(8,145,178,0.04) 100%)',
              border: '1px solid rgba(34,211,238,0.25)',
              borderRadius: 10,
              padding: '16px 20px',
            }}>
              <div style={{ fontSize: 12, color: '#22d3ee', fontWeight: 700, marginBottom: 12, letterSpacing: '0.05em', textTransform: 'uppercase' }}>
                🔒 OTM Wings Added Mid-Session
              </div>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                <MetricCard
                  label="Lock Fired"
                  value={`${ls.count} days`}
                  sub={`${ls.pct}% of sessions`}
                  color="#22d3ee"
                />
                <MetricCard
                  label="Win Rate (Locked)"
                  value={`${lockWinRate}%`}
                  sub={`${ls.wins}W / ${ls.losses}L`}
                  color={lockWinRate >= 50 ? '#4ade80' : '#f87171'}
                />
                <MetricCard
                  label="Avg P&L (Locked)"
                  value={fmtINR(ls.avg_pnl, { forceSign: true })}
                  sub="when wings added"
                  color={ls.avg_pnl >= 0 ? '#4ade80' : '#f87171'}
                />
                <MetricCard
                  label="Avg P&L (No Lock)"
                  value={fmtINR(ls.avg_pnl_no_lock, { forceSign: true })}
                  sub="plain straddle days"
                  color={ls.avg_pnl_no_lock >= 0 ? '#4ade80' : '#f87171'}
                />
              </div>
              {(ls.profit_lock_count > 0 || ls.loss_lock_count > 0) && (
                <div style={{ display: 'flex', gap: 24, marginTop: 14, paddingTop: 12, borderTop: '1px solid rgba(34,211,238,0.15)', fontSize: 12 }}>
                  {ls.profit_lock_count > 0 && (
                    <div>
                      <span style={{ color: '#22d3ee', fontWeight: 700 }}>🔒 Profit Lock</span>
                      <span style={{ color: 'var(--text-secondary)', marginLeft: 8 }}>
                        {ls.profit_lock_count} days
                        {ls.profit_lock_avg_pnl != null && ` · avg ${fmtINR(ls.profit_lock_avg_pnl, { forceSign: true })}`}
                      </span>
                    </div>
                  )}
                  {ls.loss_lock_count > 0 && (
                    <div>
                      <span style={{ color: '#fb923c', fontWeight: 700 }}>🛡️ Loss Lock</span>
                      <span style={{ color: 'var(--text-secondary)', marginLeft: 8 }}>
                        {ls.loss_lock_count} days
                        {ls.loss_lock_avg_pnl != null && ` · avg ${fmtINR(ls.loss_lock_avg_pnl, { forceSign: true })}`}
                      </span>
                    </div>
                  )}
                </div>
              )}
            </div>
          </>
        )
      })()}

      {/* Cumulative P&L */}
      <SectionTitle title="Cumulative P&L" />
      <div style={{
        background: 'var(--surface-2)', border: '1px solid var(--border)',
        borderRadius: 10, padding: '16px 8px',
      }}>
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={s.daily_pnl} margin={{ top: 8, right: 20, left: 10, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 10, fill: 'var(--text-secondary)' }}
              tickLine={false}
              interval={Math.floor(s.daily_pnl.length / 8)}
            />
            <YAxis
              tick={{ fontSize: 10, fill: 'var(--text-secondary)' }}
              tickLine={false}
              axisLine={false}
              tickFormatter={v => v >= 1e5 ? `${(v/1e5).toFixed(1)}L` : `${(v/1000).toFixed(0)}k`}
            />
            <Tooltip content={<CumTooltip />} />
            <ReferenceLine y={0} stroke="var(--border)" strokeDasharray="4 2" />
            <Line
              type="monotone"
              dataKey="cumulative_pnl"
              stroke={isPositive ? '#4ade80' : '#f87171'}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4 }}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* Monthly P&L */}
      <SectionTitle title="Monthly P&L" />
      <div style={{
        background: 'var(--surface-2)', border: '1px solid var(--border)',
        borderRadius: 10, padding: '16px 8px',
      }}>
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={s.monthly_pnl} margin={{ top: 8, right: 20, left: 10, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis
              dataKey="month"
              tick={{ fontSize: 10, fill: 'var(--text-secondary)' }}
              tickLine={false}
            />
            <YAxis
              tick={{ fontSize: 10, fill: 'var(--text-secondary)' }}
              tickLine={false}
              axisLine={false}
              tickFormatter={v => v >= 1e5 ? `${(v/1e5).toFixed(1)}L` : `${(v/1000).toFixed(0)}k`}
            />
            <Tooltip content={<MonthBarTooltip />} />
            <ReferenceLine y={0} stroke="var(--border)" />
            <Bar dataKey="pnl" radius={[3, 3, 0, 0]}>
              {s.monthly_pnl.map((entry, i) => (
                <Cell key={i} fill={entry.pnl >= 0 ? '#4ade80' : '#f87171'} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Daily P&L bars */}
      <SectionTitle title="Daily P&L" />
      {s.lock_stats && (
        <div style={{ display: 'flex', gap: 16, marginBottom: 10, fontSize: 11, color: 'var(--text-secondary)' }}>
          <span><span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 2, background: '#4ade80', marginRight: 5 }} />Win</span>
          <span><span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 2, background: '#f87171', marginRight: 5 }} />Loss</span>
          <span><span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 2, background: '#22d3ee', marginRight: 5 }} />Win + Profit Lock 🔒</span>
          <span><span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 2, background: '#0891b2', marginRight: 5 }} />Loss + Profit Lock 🔒</span>
          <span><span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 2, background: '#fb923c', marginRight: 5 }} />Win + Loss Lock 🛡️</span>
          <span><span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 2, background: '#c2410c', marginRight: 5 }} />Loss + Loss Lock 🛡️</span>
        </div>
      )}
      <div style={{
        background: 'var(--surface-2)', border: '1px solid var(--border)',
        borderRadius: 10, padding: '16px 8px',
      }}>
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={s.daily_pnl} margin={{ top: 8, right: 20, left: 10, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis dataKey="date" hide />
            <YAxis
              tick={{ fontSize: 10, fill: 'var(--text-secondary)' }}
              tickLine={false}
              axisLine={false}
              tickFormatter={v => v >= 1e5 ? `${(v/1e5).toFixed(1)}L` : `${(v/1000).toFixed(0)}k`}
            />
            <Tooltip content={<DailyBarTooltip />} />
            <ReferenceLine y={0} stroke="var(--border)" />
            <Bar dataKey="pnl" radius={[2, 2, 0, 0]}>
              {s.daily_pnl.map((entry, i) => {
                if (entry.wings_locked) {
                  if (entry.lock_reason === 'loss') {
                    return <Cell key={i} fill={entry.pnl >= 0 ? '#fb923c' : '#c2410c'} />
                  }
                  return <Cell key={i} fill={entry.pnl >= 0 ? '#22d3ee' : '#0891b2'} />
                }
                return <Cell key={i} fill={entry.pnl >= 0 ? '#4ade80' : '#f87171'} />
              })}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Monthly table */}
      <SectionTitle title="Monthly Breakdown" />
      <div style={{
        background: 'var(--surface-2)', border: '1px solid var(--border)',
        borderRadius: 10, overflow: 'hidden',
      }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)' }}>
              {['Month', 'Sessions', 'Wins', 'Losses', 'Win %', 'P&L'].map(h => (
                <th key={h} style={{
                  padding: '10px 16px', textAlign: h === 'Month' ? 'left' : 'right',
                  color: 'var(--text-secondary)', fontWeight: 600, fontSize: 11, textTransform: 'uppercase',
                }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {s.monthly_pnl.map((m, i) => {
              const wr = m.runs ? Math.round(m.wins / m.runs * 100) : 0
              return (
                <tr key={m.month} style={{
                  borderBottom: i < s.monthly_pnl.length - 1 ? '1px solid var(--border)' : 'none',
                  background: i % 2 === 0 ? 'transparent' : 'var(--surface-3, rgba(255,255,255,0.02))',
                }}>
                  <td style={{ padding: '10px 16px', fontWeight: 600, color: 'var(--text-primary)' }}>{m.month}</td>
                  <td style={{ padding: '10px 16px', textAlign: 'right', color: 'var(--text-secondary)' }}>{m.runs}</td>
                  <td style={{ padding: '10px 16px', textAlign: 'right', color: '#4ade80' }}>{m.wins}</td>
                  <td style={{ padding: '10px 16px', textAlign: 'right', color: '#f87171' }}>{m.losses}</td>
                  <td style={{ padding: '10px 16px', textAlign: 'right', color: wr >= 50 ? '#4ade80' : '#f87171' }}>{wr}%</td>
                  <td style={{
                    padding: '10px 16px', textAlign: 'right', fontWeight: 700,
                    color: m.pnl >= 0 ? '#4ade80' : '#f87171',
                  }}>{fmtINR(m.pnl, { forceSign: true })}</td>
                </tr>
              )
            })}
          </tbody>
          <tfoot>
            <tr style={{ borderTop: '2px solid var(--border)' }}>
              <td style={{ padding: '10px 16px', fontWeight: 700, color: 'var(--text-primary)' }}>Total</td>
              <td style={{ padding: '10px 16px', textAlign: 'right', color: 'var(--text-secondary)', fontWeight: 600 }}>{s.total_runs}</td>
              <td style={{ padding: '10px 16px', textAlign: 'right', color: '#4ade80', fontWeight: 600 }}>{s.wins}</td>
              <td style={{ padding: '10px 16px', textAlign: 'right', color: '#f87171', fontWeight: 600 }}>{s.losses}</td>
              <td style={{ padding: '10px 16px', textAlign: 'right', color: s.win_rate >= 50 ? '#4ade80' : '#f87171', fontWeight: 600 }}>{s.win_rate}%</td>
              <td style={{
                padding: '10px 16px', textAlign: 'right', fontWeight: 800, fontSize: 15,
                color: s.net_pnl >= 0 ? '#4ade80' : '#f87171',
              }}>{fmtINR(s.net_pnl, { forceSign: true })}</td>
            </tr>
          </tfoot>
        </table>
      </div>
    </div>
  )
}
