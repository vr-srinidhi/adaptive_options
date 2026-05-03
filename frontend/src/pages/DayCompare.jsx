import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, ReferenceLine,
} from 'recharts'
import { getDayCompare, getStrategyDashboard } from '../api/index.js'

// Auto-assign colours from a palette — works for any number of strategies
const PALETTE = [
  '#94a3b8', // slate   — plain straddle
  '#22d3ee', // cyan    — profit lock
  '#fb923c', // orange  — dual lock
  '#a78bfa', // violet
  '#4ade80', // green
  '#f472b6', // pink
  '#facc15', // yellow
]

function colorFor(index) {
  return PALETTE[index % PALETTE.length]
}

function fmtINR(v, forceSign = false) {
  if (v == null) return '—'
  const abs = Math.abs(v)
  const s = abs >= 1_00_000
    ? `₹${(abs / 1_00_000).toFixed(2)}L`
    : `₹${Math.round(abs).toLocaleString('en-IN')}`
  const sign = v < 0 ? '−' : (forceSign ? '+' : '')
  return `${sign}${s}`
}

function ExitBadge({ reason }) {
  const MAP = {
    TIME_EXIT:     { bg: 'rgba(100,116,139,0.15)', c: '#94a3b8' },
    TRAIL_EXIT:    { bg: 'rgba(34,211,238,0.12)',  c: '#22d3ee' },
    STOP_EXIT:     { bg: 'rgba(248,113,113,0.15)', c: '#f87171' },
    TARGET_EXIT:   { bg: 'rgba(74,222,128,0.12)',  c: '#4ade80' },
    DATA_GAP_EXIT: { bg: 'rgba(251,146,60,0.12)',  c: '#fb923c' },
  }
  const s = MAP[reason] || { bg: 'rgba(255,255,255,0.05)', c: '#94a3b8' }
  return (
    <span style={{
      fontSize: 11, fontWeight: 700, letterSpacing: '0.04em',
      padding: '2px 8px', borderRadius: 4, background: s.bg, color: s.c,
    }}>
      {reason || '—'}
    </span>
  )
}

function LockBadge({ locked, lockReason, lockTime }) {
  if (!locked) return <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>No lock</span>
  const isLoss = lockReason === 'loss'
  return (
    <span style={{
      fontSize: 11, fontWeight: 700, letterSpacing: '0.04em',
      padding: '2px 8px', borderRadius: 4,
      background: isLoss ? 'rgba(251,146,60,0.15)' : 'rgba(34,211,238,0.12)',
      color: isLoss ? '#fb923c' : '#22d3ee',
    }}>
      {isLoss ? '🛡️ Loss lock' : '🔒 Profit lock'} @ {lockTime}
    </span>
  )
}

function Row({ label, value }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
      <span style={{ color: 'var(--text-secondary)' }}>{label}</span>
      <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{value}</span>
    </div>
  )
}

function StrategyCard({ s, color, onClick }) {
  const isPos = (s.pnl || 0) >= 0
  const [hovered, setHovered] = useState(false)
  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        flex: '1 1 220px', minWidth: 220, maxWidth: 320,
        background: hovered ? `${color}0d` : 'var(--surface-2)',
        border: `1px solid ${hovered ? color : color + '44'}`,
        borderTop: `3px solid ${color}`,
        borderRadius: 10,
        padding: '16px 18px',
        cursor: 'pointer',
        transition: 'background 0.15s, border-color 0.15s',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
        <div style={{ fontSize: 11, color, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          {s.strategy_name}
        </div>
        <div style={{ fontSize: 10, color: hovered ? color : 'var(--text-secondary)', fontWeight: 600 }}>
          View replay →
        </div>
      </div>
      <div style={{ fontSize: 26, fontWeight: 900, color: isPos ? '#4ade80' : '#f87171', marginBottom: 12 }}>
        {fmtINR(s.pnl, true)}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, fontSize: 12 }}>
        <Row label="Exit"         value={<ExitBadge reason={s.exit_reason} />} />
        <Row label="Entry → Exit" value={`${s.entry_time || '—'} → ${s.exit_time || '—'}`} />
        <Row label="Lots"         value={`${s.lots} × ${s.lot_size}`} />
        <Row label="Credit"       value={fmtINR(s.entry_credit_total)} />
        <Row label="Charges"      value={fmtINR(s.total_charges)} />
        {s.wings_locked !== undefined && (
          <Row label="Lock" value={<LockBadge locked={s.wings_locked} lockReason={s.lock_reason} lockTime={s.lock_time} />} />
        )}
      </div>
    </div>
  )
}

function NoDataCard({ name, color }) {
  return (
    <div style={{
      flex: '1 1 220px', minWidth: 220, maxWidth: 320,
      background: 'var(--surface-2)',
      border: `1px solid ${color}22`,
      borderTop: `3px solid ${color}44`,
      borderRadius: 10,
      padding: '16px 18px',
      opacity: 0.45,
    }}>
      <div style={{ fontSize: 11, color, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 6 }}>
        {name}
      </div>
      <div style={{ fontSize: 14, color: 'var(--text-secondary)', marginTop: 12 }}>
        No data for this date
      </div>
    </div>
  )
}

function MtmTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div style={{
      background: 'var(--surface-2)', border: '1px solid var(--border)',
      borderRadius: 6, padding: '8px 12px', fontSize: 11, minWidth: 160,
    }}>
      <div style={{ fontWeight: 700, marginBottom: 6, color: 'var(--text-secondary)' }}>{label}</div>
      {payload.map((p, i) => p.value != null && (
        <div key={i} style={{ color: p.color, display: 'flex', justifyContent: 'space-between', gap: 16 }}>
          <span>{p.name}</span>
          <span style={{ fontWeight: 700 }}>{fmtINR(p.value, true)}</span>
        </div>
      ))}
    </div>
  )
}

function buildChartData(strategies) {
  const timeMap = {}
  for (const s of strategies) {
    for (const row of s.mtm_series) {
      if (!timeMap[row.t]) timeMap[row.t] = { t: row.t }
      timeMap[row.t][s.strategy_name] = row.net_mtm
    }
  }
  return Object.values(timeMap).sort((a, b) => a.t.localeCompare(b.t))
}

// Pre-seeded well-known dates — notable comparison days
const QUICK_DATES = [
  { label: '20 Feb 2026', value: '2026-02-20', note: 'Big loss saved by dual lock' },
  { label: '16 Jan 2026', value: '2026-01-16', note: 'Stop hit — dual lock saved ₹33k' },
  { label: '10 Feb 2026', value: '2026-02-10', note: 'Profit lock day — ₹58k' },
  { label: '27 Jan 2026', value: '2026-01-27', note: 'Profit lock — ₹68k' },
  { label: '10 Mar 2026', value: '2026-03-10', note: 'False loss lock at ₹10k' },
]

function LegendPill({ label, color, active, onToggle }) {
  return (
    <button
      onClick={onToggle}
      style={{
        display: 'flex', alignItems: 'center', gap: 5,
        background: active ? `${color}18` : 'transparent',
        border: `1px solid ${active ? color + '88' : 'var(--border)'}`,
        borderRadius: 20, padding: '3px 10px',
        cursor: 'pointer', fontSize: 11, fontWeight: 600,
        color: active ? color : 'var(--text-secondary)',
        transition: 'all 0.15s',
        textDecoration: active ? 'none' : 'line-through',
        opacity: active ? 1 : 0.5,
      }}
    >
      <span style={{ display: 'inline-block', width: 14, height: 2, borderRadius: 1, background: active ? color : 'var(--border)' }} />
      {label}
    </button>
  )
}

function MtmCompareChart({ chartData, strategies, lockEvents }) {
  const [vis, setVis] = useState(() => {
    const init = {}
    strategies.forEach(s => { init[s.strategy_name] = true })
    return init
  })
  const toggle = name => setVis(v => ({ ...v, [name]: !v[name] }))

  const allVals = strategies.flatMap((s, i) =>
    vis[s.strategy_name] ? chartData.map(r => r[s.strategy_name]).filter(v => v != null) : []
  )
  const lo = allVals.length ? Math.floor(Math.min(...allVals, 0) / 5000) * 5000 : -50000
  const hi = allVals.length ? Math.ceil(Math.max(...allVals, 0) / 5000) * 5000 : 50000

  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
          Net MTM — All Strategies
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {strategies.map((s, i) => (
            <LegendPill key={s.strategy_id} label={s.strategy_name} color={colorFor(i)} active={vis[s.strategy_name]} onToggle={() => toggle(s.strategy_name)} />
          ))}
        </div>
      </div>
      <div style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 10, padding: '16px 8px' }}>
        <ResponsiveContainer width="100%" height={320}>
          <LineChart data={chartData} margin={{ top: 8, right: 24, left: 10, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis dataKey="t" tick={{ fontSize: 10, fill: 'var(--text-secondary)' }} tickLine={false} interval={Math.floor(chartData.length / 10)} />
            <YAxis tick={{ fontSize: 10, fill: 'var(--text-secondary)' }} tickLine={false} axisLine={false} tickFormatter={v => `${Math.round(v / 1000)}k`} domain={[lo, hi]} />
            <Tooltip content={<MtmTooltip />} />
            <ReferenceLine y={0} stroke="var(--border)" strokeDasharray="4 2" />
            {lockEvents.map((lk, i) => (
              <ReferenceLine key={i} x={lk.time} stroke={lk.color} strokeDasharray="5 3" strokeWidth={1.5}
                label={{ value: lk.isLoss ? '🛡️' : '🔒', position: 'insideTopRight', fontSize: 12 }}
              />
            ))}
            {strategies.map((s, i) => vis[s.strategy_name] && (
              <Line key={s.strategy_id} type="monotone" dataKey={s.strategy_name}
                stroke={colorFor(i)} strokeWidth={2} dot={false} activeDot={{ r: 4 }} connectNulls={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </>
  )
}

export default function DayCompare() {
  const navigate = useNavigate()
  const [dateInput, setDateInput] = useState('')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // Strategy selector state
  const [availableStrategies, setAvailableStrategies] = useState([])   // [{id, name}] from dashboard
  const [selected, setSelected] = useState(new Set())                  // selected strategy IDs

  useEffect(() => {
    getStrategyDashboard()
      .then(r => {
        const list = (r.data.strategies || []).map(s => ({
          id: s.strategy_id,
          name: s.strategy_name,
        }))
        setAvailableStrategies(list)
        setSelected(new Set(list.map(s => s.id)))  // all selected by default
      })
      .catch(() => {})  // fail silently — selector just won't show
  }, [])

  function toggleStrategy(id) {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(id)) {
        if (next.size > 1) next.delete(id)  // always keep at least one
      } else {
        next.add(id)
      }
      return next
    })
  }

  function load(d) {
    if (!d) return
    setLoading(true)
    setError(null)
    setData(null)
    const ids = selected.size > 0 ? [...selected] : null
    getDayCompare(d, ids)
      .then(r => setData(r.data))
      .catch(e => setError(e.response?.data?.detail || e.message))
      .finally(() => setLoading(false))
  }

  const strategies = data?.strategies || []
  const chartData  = strategies.length ? buildChartData(strategies) : []

  // Lock event verticals for chart
  const lockEvents = strategies
    .filter(s => s.wings_locked && s.lock_time)
    .map((s, idx) => ({
      time: s.lock_time,
      color: colorFor(strategies.indexOf(s)),
      isLoss: s.lock_reason === 'loss',
    }))

  // Best / worst strategy for the day
  const sorted = [...strategies].sort((a, b) => (b.pnl || 0) - (a.pnl || 0))
  const best   = sorted[0]
  const worst  = sorted[sorted.length - 1]

  return (
    <div style={{ padding: '32px 40px', maxWidth: 1300, margin: '0 auto' }}>
      <button
        onClick={() => navigate('/workbench')}
        style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#6366f1', fontSize: 13, padding: 0, marginBottom: 20 }}
      >
        ← Dashboard
      </button>

      <div style={{ marginBottom: 28 }}>
        <h1 style={{ fontSize: 24, fontWeight: 800, color: 'var(--text-primary)', margin: '0 0 4px' }}>
          Strategy Day Compare
        </h1>
        <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
          Pick a date to see all strategies side by side — MTM chart, lock events, P&L diff
        </div>
      </div>

      {/* Strategy selector */}
      {availableStrategies.length > 0 && (
        <div style={{
          background: 'var(--surface-2)', border: '1px solid var(--border)',
          borderRadius: 10, padding: '14px 20px', marginBottom: 20,
          display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center',
        }}>
          <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.04em', marginRight: 4 }}>
            Compare:
          </span>
          {availableStrategies.map((s, i) => {
            const on = selected.has(s.id)
            const color = colorFor(availableStrategies.findIndex(x => x.id === s.id))
            return (
              <button
                key={s.id}
                onClick={() => toggleStrategy(s.id)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 6,
                  background: on ? `${color}18` : 'transparent',
                  border: `1px solid ${on ? color : 'var(--border)'}`,
                  borderRadius: 20, padding: '5px 12px',
                  cursor: 'pointer', fontSize: 12, fontWeight: 600,
                  color: on ? color : 'var(--text-secondary)',
                  transition: 'all 0.15s',
                }}
              >
                <span style={{
                  width: 8, height: 8, borderRadius: '50%',
                  background: on ? color : 'var(--border)',
                  flexShrink: 0,
                }} />
                {s.name}
              </button>
            )
          })}
          <button
            onClick={() => setSelected(new Set(availableStrategies.map(s => s.id)))}
            style={{ fontSize: 11, color: '#6366f1', background: 'none', border: 'none', cursor: 'pointer', marginLeft: 4 }}
          >
            Select all
          </button>
        </div>
      )}

      {/* Date picker + quick dates */}
      <div style={{ marginBottom: 32 }}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12 }}>
          <input
            type="date"
            value={dateInput}
            onChange={e => setDateInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && load(dateInput)}
            style={{
              background: 'var(--surface-2)', border: '1px solid var(--border)',
              color: 'var(--text-primary)', borderRadius: 8, padding: '9px 14px',
              fontSize: 14, outline: 'none',
            }}
          />
          <button
            onClick={() => load(dateInput)}
            disabled={!dateInput || loading}
            style={{
              background: '#6366f1', color: '#fff', border: 'none', borderRadius: 8,
              padding: '9px 22px', fontSize: 14, fontWeight: 600, cursor: 'pointer',
              opacity: (!dateInput || loading) ? 0.5 : 1,
            }}
          >
            {loading ? 'Loading…' : 'Compare'}
          </button>
        </div>
        {/* Quick dates */}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {QUICK_DATES.map(d => (
            <button key={d.value} onClick={() => { setDateInput(d.value); load(d.value) }} style={{
              background: 'var(--surface-2)', border: '1px solid var(--border)',
              color: 'var(--text-secondary)', borderRadius: 6, padding: '5px 10px',
              fontSize: 11, cursor: 'pointer', textAlign: 'left',
            }} title={d.note}>
              {d.label}
            </button>
          ))}
        </div>
      </div>

      {error && <div style={{ color: '#f87171', marginBottom: 20 }}>{error}</div>}

      {data && strategies.length === 0 && (
        <div style={{
          color: 'var(--text-secondary)', padding: '60px 0', textAlign: 'center', fontSize: 14,
        }}>
          No completed strategy runs found for {data.date}.
        </div>
      )}

      {data && strategies.length > 0 && (
        <>
          {/* Strategy cards */}
          <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', marginBottom: 24 }}>
            {strategies.map((s, i) => (
              <StrategyCard
                key={s.strategy_id} s={s} color={colorFor(i)}
                onClick={() => window.open(`/workbench/replay/strategy_run/${s.run_id}`, '_blank')}
              />
            ))}
          </div>

          {/* Best / worst callout */}
          {strategies.length >= 2 && best && worst && best.strategy_id !== worst.strategy_id && (
            <div style={{
              background: 'var(--surface-2)', border: '1px solid var(--border)',
              borderRadius: 10, padding: '14px 20px', marginBottom: 24,
              display: 'flex', gap: 32, flexWrap: 'wrap', fontSize: 13, alignItems: 'center',
            }}>
              <span style={{ color: 'var(--text-secondary)', fontWeight: 600 }}>Day winner/loser</span>
              <span>
                <span style={{ color: '#4ade80', fontWeight: 700 }}>Best: </span>
                {best.strategy_name} {fmtINR(best.pnl, true)}
              </span>
              <span>
                <span style={{ color: '#f87171', fontWeight: 700 }}>Worst: </span>
                {worst.strategy_name} {fmtINR(worst.pnl, true)}
              </span>
              <span style={{ color: 'var(--text-secondary)' }}>
                Spread: <span style={{ color: 'var(--text-primary)', fontWeight: 700 }}>
                  {fmtINR((best.pnl || 0) - (worst.pnl || 0), true)}
                </span>
              </span>
            </div>
          )}

          {/* All-pairs diff table */}
          {strategies.length >= 2 && (
            <div style={{
              background: 'var(--surface-2)', border: '1px solid var(--border)',
              borderRadius: 10, overflow: 'hidden', marginBottom: 32,
            }}>
              <div style={{ padding: '12px 20px', borderBottom: '1px solid var(--border)', fontSize: 12, fontWeight: 700, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                P&L Differences
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border)' }}>
                    <th style={{ padding: '10px 16px', textAlign: 'left', color: 'var(--text-secondary)', fontWeight: 600, fontSize: 11 }}>Strategy</th>
                    <th style={{ padding: '10px 16px', textAlign: 'right', color: 'var(--text-secondary)', fontWeight: 600, fontSize: 11 }}>P&L</th>
                    {strategies.map((s, i) => (
                      <th key={s.strategy_id} style={{ padding: '10px 16px', textAlign: 'right', color: colorFor(i), fontWeight: 600, fontSize: 11 }}>
                        vs {s.strategy_name}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {strategies.map((row, ri) => (
                    <tr
                      key={row.strategy_id}
                      onClick={() => window.open(`/workbench/replay/strategy_run/${row.run_id}`, '_blank')}
                      style={{
                        borderBottom: ri < strategies.length - 1 ? '1px solid var(--border)' : 'none',
                        cursor: 'pointer',
                      }}
                      onMouseEnter={e => e.currentTarget.style.background = `${colorFor(ri)}0d`}
                      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                    >
                      <td style={{ padding: '10px 16px', fontWeight: 600, color: colorFor(ri) }}>
                        {row.strategy_name}
                        <span style={{ fontSize: 10, color: 'var(--text-secondary)', marginLeft: 6, fontWeight: 400 }}>↗ view</span>
                      </td>
                      <td style={{ padding: '10px 16px', textAlign: 'right', fontWeight: 700, color: (row.pnl || 0) >= 0 ? '#4ade80' : '#f87171' }}>
                        {fmtINR(row.pnl, true)}
                      </td>
                      {strategies.map((col, ci) => {
                        const diff = (row.pnl || 0) - (col.pnl || 0)
                        return (
                          <td key={col.strategy_id} style={{ padding: '10px 16px', textAlign: 'right', color: ri === ci ? 'var(--text-secondary)' : diff >= 0 ? '#4ade80' : '#f87171', fontWeight: ri === ci ? 400 : 700 }}>
                            {ri === ci ? '—' : fmtINR(diff, true)}
                          </td>
                        )
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Combined MTM chart */}
          {chartData.length > 0 && (() => {
            // Per-strategy visibility state lifted into a local component via IIFE + useState workaround
            return <MtmCompareChart chartData={chartData} strategies={strategies} lockEvents={lockEvents} />
          })()}
        </>
      )}
    </div>
  )
}
