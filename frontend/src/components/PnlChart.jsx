import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ReferenceLine, ResponsiveContainer, Area, AreaChart,
} from 'recharts'

const fmt = (v) =>
  v === undefined || v === null ? '' :
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(v)

// Custom tooltip
const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null
  const pnl = payload[0]?.value
  const spot = payload[1]?.value
  return (
    <div className="px-3 py-2 rounded text-xs"
      style={{ background: '#1e293b', border: '1px solid #334155' }}>
      <div style={{ color: '#94a3b8' }}>{label}</div>
      <div style={{ color: pnl >= 0 ? '#22c55e' : '#ef4444' }}>P&L: {fmt(pnl)}</div>
      {spot && <div style={{ color: '#94a3b8' }}>Spot: {spot?.toLocaleString('en-IN')}</div>}
    </div>
  )
}

// 1-minute P&L progression chart (Trade Book)
export function PnlProgressionChart({ data }) {
  if (!data?.length) return null
  const maxAbs = Math.max(...data.map(d => Math.abs(d.pnl)), 1)

  return (
    <ResponsiveContainer width="100%" height={200}>
      <AreaChart data={data} margin={{ top: 8, right: 16, left: 16, bottom: 0 }}>
        <defs>
          <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.3} />
            <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#334155" vertical={false} />
        <XAxis
          dataKey="time"
          tick={{ fontSize: 10, fill: '#64748b' }}
          tickLine={false}
          axisLine={{ stroke: '#334155' }}
          interval="preserveStartEnd"
        />
        <YAxis
          tick={{ fontSize: 10, fill: '#64748b' }}
          tickLine={false}
          axisLine={false}
          tickFormatter={v => fmt(v)}
          domain={[-maxAbs * 1.1, maxAbs * 1.1]}
          width={80}
        />
        <Tooltip content={<CustomTooltip />} />
        <ReferenceLine y={0} stroke="#475569" strokeDasharray="4 4" />
        <Area
          type="monotone"
          dataKey="pnl"
          stroke="#3b82f6"
          strokeWidth={2}
          fill="url(#pnlGrad)"
          dot={false}
          activeDot={{ r: 4, fill: '#3b82f6' }}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}

// Cumulative P&L chart (Dashboard)
export function CumulativePnlChart({ sessions }) {
  if (!sessions?.length) return null

  // Sort ascending by date, compute running total
  const sorted = [...sessions].sort((a, b) => a.session_date.localeCompare(b.session_date))
  let running = 0
  const data = sorted.map(s => {
    running += s.pnl || 0
    return {
      date: s.session_date.slice(5), // MM-DD
      cumPnl: Math.round(running),
    }
  })

  const max = Math.max(...data.map(d => d.cumPnl), 0)
  const min = Math.min(...data.map(d => d.cumPnl), 0)

  return (
    <ResponsiveContainer width="100%" height={180}>
      <AreaChart data={data} margin={{ top: 8, right: 16, left: 16, bottom: 0 }}>
        <defs>
          <linearGradient id="cumGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#22c55e" stopOpacity={0.25} />
            <stop offset="95%" stopColor="#22c55e" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="#334155" vertical={false} />
        <XAxis
          dataKey="date"
          tick={{ fontSize: 10, fill: '#64748b' }}
          tickLine={false}
          axisLine={{ stroke: '#334155' }}
          interval="preserveStartEnd"
        />
        <YAxis
          tick={{ fontSize: 10, fill: '#64748b' }}
          tickLine={false}
          axisLine={false}
          tickFormatter={v => fmt(v)}
          domain={[min * 1.1 - 1, max * 1.1 + 1]}
          width={88}
        />
        <Tooltip
          formatter={(v) => [fmt(v), 'Cumulative P&L']}
          contentStyle={{ background: '#1e293b', border: '1px solid #334155', fontSize: 11 }}
          labelStyle={{ color: '#94a3b8' }}
        />
        <ReferenceLine y={0} stroke="#475569" strokeDasharray="4 4" />
        <Area
          type="monotone"
          dataKey="cumPnl"
          stroke="#22c55e"
          strokeWidth={2}
          fill="url(#cumGrad)"
          dot={false}
          activeDot={{ r: 4, fill: '#22c55e' }}
        />
      </AreaChart>
    </ResponsiveContainer>
  )
}
