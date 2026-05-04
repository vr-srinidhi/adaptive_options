import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  LineChart, Line, XAxis, YAxis, Tooltip, ReferenceLine,
  ResponsiveContainer, CartesianGrid,
} from 'recharts'
import { useAuth } from '../contexts/AuthContext'
import {
  getLivePaperConfig, updateLivePaperConfig,
  getLivePaperToday, getLivePaperHistory,
  startLivePaper, stopLivePaper,
  zerodhaSetTokenDirect,
} from '../api/index.js'

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtINR(v) {
  if (v == null) return '—'
  const abs = Math.abs(v)
  const s = abs >= 1_00_000
    ? `₹${(abs / 1_00_000).toFixed(2)}L`
    : `₹${abs.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
  return v < 0 ? `−${s}` : `+${s}`
}

function fmtTime(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false })
}

function StatusBadge({ status }) {
  const map = {
    scheduled: { color: '#94a3b8', label: 'Scheduled' },
    waiting:   { color: '#facc15', label: '⏳ Waiting' },
    entered:   { color: '#4ade80', label: '● Live' },
    exited:    { color: '#6366f1', label: '✓ Done' },
    no_trade:  { color: '#64748b', label: 'No Trade' },
    error:     { color: '#f87171', label: '✗ Error' },
  }
  const s = map[status] || { color: '#64748b', label: status || 'Unknown' }
  return (
    <span style={{
      display: 'inline-block', padding: '2px 10px', borderRadius: 12,
      fontSize: 12, fontWeight: 700, color: s.color,
      border: `1px solid ${s.color}44`, background: `${s.color}11`,
    }}>
      {s.label}
    </span>
  )
}

function LegendPill({ label, color, active, onClick }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 5,
        padding: '3px 10px', borderRadius: 12, fontSize: 11, fontWeight: 600,
        cursor: 'pointer', border: `1.5px solid ${active ? color : '#475569'}`,
        background: active ? `${color}18` : 'transparent',
        color: active ? color : '#475569',
        textDecoration: active ? 'none' : 'line-through',
        transition: 'all 0.15s',
      }}
    >
      <span style={{
        width: 8, height: 8, borderRadius: '50%',
        background: active ? color : '#475569', flexShrink: 0,
      }} />
      {label}
    </button>
  )
}

// ── MTM Chart ────────────────────────────────────────────────────────────────

function MtmChart({ data, entryTs, exitTs }) {
  const [vis, setVis] = useState({ net: true, spot: false })
  const toggle = k => setVis(v => ({ ...v, [k]: !v[k] }))

  const visibleData = data.filter(d => d.net_mtm != null || d.spot != null)

  const netVals = vis.net ? visibleData.map(d => d.net_mtm).filter(v => v != null) : []
  const spotVals = vis.spot ? visibleData.map(d => d.spot).filter(v => v != null) : []
  const allVals = [...netVals]
  const yMin = allVals.length ? Math.min(...allVals) * 1.05 : -10000
  const yMax = allVals.length ? Math.max(...allVals) * 1.05 : 10000

  const spotMin = spotVals.length ? Math.min(...spotVals) * 0.999 : undefined
  const spotMax = spotVals.length ? Math.max(...spotVals) * 1.001 : undefined

  function CustomTooltip({ active, payload }) {
    if (!active || !payload?.length) return null
    const d = payload[0]?.payload
    return (
      <div style={{
        background: 'var(--surface-2)', border: '1px solid var(--border)',
        borderRadius: 6, padding: '6px 10px', fontSize: 11,
      }}>
        <div style={{ color: 'var(--text-secondary)' }}>{fmtTime(d?.timestamp)}</div>
        {vis.net && <div style={{ color: d?.net_mtm >= 0 ? '#4ade80' : '#f87171', fontWeight: 600 }}>
          MTM: {fmtINR(d?.net_mtm)}
        </div>}
        {vis.spot && <div style={{ color: '#94a3b8' }}>Spot: {d?.spot?.toFixed(0)}</div>}
        {d?.trail_stop_level != null && (
          <div style={{ color: '#a78bfa' }}>Trail stop: {fmtINR(d.trail_stop_level)}</div>
        )}
      </div>
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
        <LegendPill label="Net MTM" color="#4ade80" active={vis.net} onClick={() => toggle('net')} />
        <LegendPill label="Trail Stop" color="#a78bfa" active={vis.net} onClick={() => toggle('net')} />
        <LegendPill label="Spot" color="#94a3b8" active={vis.spot} onClick={() => toggle('spot')} />
      </div>
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={visibleData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis
            dataKey="timestamp"
            tickFormatter={fmtTime}
            tick={{ fontSize: 10, fill: 'var(--text-secondary)' }}
            minTickGap={40}
          />
          <YAxis
            domain={[yMin, yMax]}
            tickFormatter={v => `₹${(v / 1000).toFixed(0)}k`}
            tick={{ fontSize: 10, fill: 'var(--text-secondary)' }}
            width={55}
            hide={!vis.net}
          />
          {vis.spot && (
            <YAxis
              yAxisId="spot"
              orientation="right"
              domain={[spotMin, spotMax]}
              tickFormatter={v => v?.toFixed(0)}
              tick={{ fontSize: 10, fill: '#64748b' }}
              width={50}
            />
          )}
          <Tooltip content={<CustomTooltip />} />
          <ReferenceLine y={0} stroke="var(--border)" />
          {entryTs && <ReferenceLine x={entryTs} stroke="#4ade80" strokeDasharray="4 2" label={{ value: 'IN', fill: '#4ade80', fontSize: 10 }} />}
          {exitTs  && <ReferenceLine x={exitTs}  stroke="#f87171" strokeDasharray="4 2" label={{ value: 'OUT', fill: '#f87171', fontSize: 10 }} />}
          {vis.net && (
            <Line
              type="monotone" dataKey="net_mtm"
              stroke="#4ade80" strokeWidth={1.5}
              dot={false} activeDot={{ r: 3 }}
              connectNulls={false}
            />
          )}
          {vis.net && (
            <Line
              type="monotone" dataKey="trail_stop_level"
              stroke="#a78bfa" strokeWidth={1} strokeDasharray="4 2"
              dot={false} connectNulls={false}
            />
          )}
          {vis.spot && (
            <Line
              yAxisId="spot" type="monotone" dataKey="spot"
              stroke="#94a3b8" strokeWidth={1}
              dot={false} activeDot={{ r: 2 }}
            />
          )}
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

// ── Config Panel ─────────────────────────────────────────────────────────────

function ConfigPanel({ config, onSave }) {
  const [form, setForm] = useState(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (config) setForm({
      capital:          config.capital,
      entry_time:       config.entry_time,
      enabled:          config.enabled,
      execution_mode:   config.execution_mode,
      lock_trigger:     config.params?.lock_trigger ?? 20000,
      loss_lock_trigger:config.params?.loss_lock_trigger ?? 25000,
      wing_width_steps: config.params?.wing_width_steps ?? 2,
      trail_trigger:    config.params?.trail_trigger ?? 12000,
      stop_capital_pct: config.params?.stop_capital_pct ?? 0.015,
    })
  }, [config])

  if (!form) return null

  const field = (label, key, type = 'number', step) => (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{label}</label>
      <input
        type={type} step={step}
        value={form[key]}
        onChange={e => setForm(f => ({ ...f, [key]: type === 'number' ? Number(e.target.value) : e.target.value }))}
        style={{
          background: 'var(--surface)', border: '1px solid var(--border)',
          borderRadius: 6, padding: '5px 8px', color: 'var(--text-primary)',
          fontSize: 12, width: '100%',
        }}
      />
    </div>
  )

  async function save() {
    setSaving(true)
    try {
      await onSave({
        capital:        form.capital,
        entry_time:     form.entry_time,
        enabled:        form.enabled,
        execution_mode: form.execution_mode,
        params: {
          lock_trigger:        form.lock_trigger,
          loss_lock_trigger:   form.loss_lock_trigger,
          wing_width_steps:    form.wing_width_steps,
          trail_trigger:       form.trail_trigger,
          stop_capital_pct:    form.stop_capital_pct,
          time_exit:           '15:25',
          trail_pct:           0.50,
        },
      })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 4 }}>
        Configuration
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
        {field('Capital (₹)', 'capital')}
        {field('Entry Time', 'entry_time', 'text')}
        {field('Profit Lock (₹)', 'lock_trigger')}
        {field('Loss Lock (₹)', 'loss_lock_trigger')}
        {field('Wing Width Steps', 'wing_width_steps')}
        {field('Trail Trigger (₹)', 'trail_trigger')}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 4 }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: 'var(--text-primary)', cursor: 'pointer' }}>
          <input
            type="checkbox" checked={form.enabled}
            onChange={e => setForm(f => ({ ...f, enabled: e.target.checked }))}
          />
          Auto-run on market days
        </label>
        <select
          value={form.execution_mode}
          onChange={e => setForm(f => ({ ...f, execution_mode: e.target.value }))}
          style={{
            background: 'var(--surface)', border: '1px solid var(--border)',
            borderRadius: 6, padding: '4px 8px', color: 'var(--text-primary)', fontSize: 12,
          }}
        >
          <option value="paper">Paper</option>
          <option value="live">Live ⚠</option>
        </select>
      </div>

      <button
        onClick={save} disabled={saving}
        style={{
          background: '#6366f1', border: 'none', borderRadius: 8,
          color: '#fff', fontWeight: 700, fontSize: 13, padding: '8px 16px',
          cursor: saving ? 'not-allowed' : 'pointer', opacity: saving ? 0.6 : 1,
        }}
      >
        {saving ? 'Saving…' : 'Save Config'}
      </button>
    </div>
  )
}

// ── Event Log ────────────────────────────────────────────────────────────────

function EventLog({ events }) {
  if (!events?.length) return (
    <div style={{ color: 'var(--text-secondary)', fontSize: 12 }}>No events yet.</div>
  )
  const colorMap = { ENTRY: '#4ade80', STOP_EXIT: '#f87171', TRAIL_EXIT: '#a78bfa', TIME_EXIT: '#94a3b8', HOLD: '#64748b', NO_TRADE: '#64748b', WINGS_LOCKED: '#fb923c' }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4, maxHeight: 200, overflowY: 'auto' }}>
      {[...events].reverse().map((ev, i) => (
        <div key={i} style={{
          display: 'flex', gap: 10, fontSize: 11, alignItems: 'baseline',
          borderBottom: '0.5px solid var(--border)', paddingBottom: 4,
        }}>
          <span style={{ color: 'var(--text-secondary)', flexShrink: 0 }}>{fmtTime(ev.timestamp)}</span>
          <span style={{
            color: colorMap[ev.event_type] || '#94a3b8',
            fontWeight: 600, flexShrink: 0, minWidth: 60,
          }}>{ev.event_type}</span>
          <span style={{ color: 'var(--text-secondary)' }}>
            {ev.reason_text || ev.reason_code || ''}
          </span>
        </div>
      ))}
    </div>
  )
}

// ── Token Section ─────────────────────────────────────────────────────────────

function TokenSection({ tokenStatus, onTokenSaved }) {
  const [token, setToken] = useState('')
  const [saving, setSaving] = useState(false)
  const [err, setErr]     = useState(null)

  async function save() {
    if (!token.trim()) return
    setSaving(true)
    setErr(null)
    try {
      await zerodhaSetTokenDirect(token.trim())
      setToken('')
      onTokenSaved()
    } catch (e) {
      setErr(e.response?.data?.detail || 'Failed to save token.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={{ marginTop: 16, padding: '12px 0', borderTop: '0.5px solid var(--border)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
        <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
          Scheduler fires: <strong style={{ color: 'var(--text-primary)' }}>09:14 IST</strong> weekdays
        </div>
        <span style={{
          fontSize: 11, fontWeight: 600,
          color: tokenStatus === 'valid' ? '#4ade80' : '#fb923c',
        }}>
          {tokenStatus === 'valid' ? '✓ Token valid' : tokenStatus === 'expired' ? '⚠ Expired' : '⚠ Missing'}
        </span>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <label style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
          Paste Zerodha access_token
        </label>
        <input
          type="password"
          placeholder="access_token from Kite / Sensibull…"
          value={token}
          onChange={e => setToken(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && save()}
          style={{
            background: 'var(--surface)', border: '1px solid var(--border)',
            borderRadius: 6, padding: '6px 8px', color: 'var(--text-primary)',
            fontSize: 12, width: '100%',
          }}
        />
        {err && <div style={{ fontSize: 11, color: '#f87171' }}>{err}</div>}
        <button
          onClick={save} disabled={saving || !token.trim()}
          style={{
            background: '#fb923c22', border: '1px solid #fb923c66',
            borderRadius: 6, color: '#fb923c', fontSize: 12, fontWeight: 600,
            padding: '5px 10px', cursor: saving || !token.trim() ? 'not-allowed' : 'pointer',
            opacity: saving || !token.trim() ? 0.5 : 1,
          }}
        >
          {saving ? 'Saving…' : 'Save Token'}
        </button>
      </div>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function LivePaperMonitor() {
  const { accessToken } = useAuth()
  const navigate = useNavigate()

  const [config, setConfig]       = useState(null)
  const [session, setSession]     = useState(null)
  const [mtmData, setMtmData]     = useState([])
  const [events, setEvents]       = useState([])
  const [run, setRun]             = useState(null)
  const [tokenStatus, setTokenStatus] = useState(null)
  const [isLive, setIsLive]       = useState(false)
  const [history, setHistory]     = useState([])
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState(null)
  const [actionMsg, setActionMsg] = useState(null)
  const esRef = useRef(null)

  // ── Load initial state ───────────────────────────────────────────────────
  async function loadToday() {
    try {
      const [todayRes, histRes] = await Promise.all([
        getLivePaperToday(),
        getLivePaperHistory({ limit: 10 }),
      ])
      const d = todayRes.data
      setConfig(d.config)
      setSession(d.session)
      setMtmData(d.mtm_series || [])
      setEvents(d.events || [])
      setRun(d.run)
      setTokenStatus(d.token_status)
      setIsLive(d.is_live)
      setHistory(histRes.data)
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadToday() }, [])

  // ── SSE subscription ─────────────────────────────────────────────────────
  useEffect(() => {
    if (!session || !accessToken) return
    const liveStatuses = ['waiting', 'entered', 'scheduled']
    if (!liveStatuses.includes(session?.status)) return

    if (esRef.current) esRef.current.close()

    const url = `/api/v2/live-paper/today/stream?token=${encodeURIComponent(accessToken)}`
    const es = new EventSource(url)
    esRef.current = es

    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        handleSseEvent(data)
      } catch { /* ignore parse errors */ }
    }

    es.onerror = () => {
      // Browser will auto-reconnect; no action needed
    }

    return () => { es.close(); esRef.current = null }
  }, [session?.id, accessToken])

  function handleSseEvent(data) {
    if (data.type === 'SNAPSHOT') {
      setSession(data.session)
      return
    }
    if (data.type === 'DONE' || data.type === 'ERROR') {
      setIsLive(false)
      loadToday()   // refresh everything from DB
      return
    }
    if (data.type === 'STATUS' || data.type === 'RESOLVED') {
      setSession(s => s ? { ...s, ...data } : s)
      return
    }
    if (data.type === 'ENTRY') {
      setSession(s => s ? { ...s, status: 'entered' } : s)
      setIsLive(true)
      setEvents(ev => [...ev, {
        timestamp: data.timestamp, event_type: 'ENTRY',
        reason_code: 'ENTRY', reason_text: `CE@${data.ce_price} PE@${data.pe_price}`,
      }])
      return
    }
    if (data.type === 'LOCK') {
      setSession(s => s ? { ...s, lock_status: `${data.lock_reason}_locked` } : s)
      setEvents(ev => [...ev, {
        timestamp: data.timestamp, event_type: 'WINGS_LOCKED',
        reason_text: `${data.lock_reason} lock — wings bought`,
      }])
      return
    }
    if (data.type === 'MTM') {
      setSession(s => s ? { ...s, net_mtm_latest: data.net_mtm, spot_latest: data.spot } : s)
      setMtmData(prev => [...prev, {
        timestamp: data.timestamp,
        net_mtm: data.net_mtm,
        gross_mtm: data.gross_mtm,
        trail_stop_level: data.trail_stop_level,
        spot: data.spot,
      }])
    }
  }

  async function handleSaveConfig(updates) {
    const res = await updateLivePaperConfig(updates)
    setConfig(res.data)
  }

  async function handleStart() {
    try {
      await startLivePaper()
      setActionMsg('Session started.')
      setTimeout(loadToday, 1500)
    } catch (e) {
      setActionMsg(e.response?.data?.detail || 'Start failed.')
    }
    setTimeout(() => setActionMsg(null), 4000)
  }

  async function handleStop() {
    if (!confirm('Send stop signal? The session will exit on the next minute tick.')) return
    try {
      await stopLivePaper()
      setActionMsg('Stop signal sent.')
      setTimeout(loadToday, 3000)
    } catch (e) {
      setActionMsg(e.response?.data?.detail || 'Stop failed.')
    }
    setTimeout(() => setActionMsg(null), 4000)
  }

  // ── Compute entry/exit timestamps for chart markers ───────────────────────
  const entryEvent = events.find(e => e.event_type === 'ENTRY')
  const exitEvent  = events.find(e => ['STOP_EXIT', 'TRAIL_EXIT', 'TIME_EXIT', 'DATA_GAP_EXIT'].includes(e.event_type))

  if (loading) return <div style={{ padding: 40, color: 'var(--text-secondary)', fontSize: 14 }}>Loading…</div>
  if (error)   return <div style={{ padding: 40, color: '#f87171', fontFamily: 'monospace' }}>Error: {error}</div>

  const isActive = ['waiting', 'entered', 'scheduled'].includes(session?.status)
  const canStop  = session?.status === 'entered'
  const canStart = !session || ['error', 'no_trade', 'exited'].includes(session?.status)

  return (
    <div style={{ padding: '28px 32px', maxWidth: 1200, margin: '0 auto' }}>

      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 24 }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 800, color: 'var(--text-primary)', margin: 0 }}>
            Live Paper Trading
          </h1>
          <p style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 3 }}>
            {config?.strategy_id || '—'} · {config?.instrument} ·{' '}
            {config ? fmtINR(config.capital).replace('+', '') : '—'} capital
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {tokenStatus === 'missing' && (
            <span style={{ fontSize: 11, color: '#f87171', border: '1px solid #f8717144', borderRadius: 6, padding: '3px 8px' }}>
              ⚠ Zerodha token missing
            </span>
          )}
          {tokenStatus === 'expired' && (
            <span style={{ fontSize: 11, color: '#fb923c', border: '1px solid #fb923c44', borderRadius: 6, padding: '3px 8px' }}>
              ⚠ Token expired — reconnect Zerodha
            </span>
          )}
          {canStop && (
            <button onClick={handleStop} style={{
              background: 'none', border: '1px solid #f87171', borderRadius: 8,
              color: '#f87171', fontSize: 12, fontWeight: 600, padding: '6px 14px', cursor: 'pointer',
            }}>
              Emergency Stop
            </button>
          )}
          {canStart && (
            <button onClick={handleStart} style={{
              background: '#6366f1', border: 'none', borderRadius: 8,
              color: '#fff', fontSize: 12, fontWeight: 700, padding: '6px 14px', cursor: 'pointer',
            }}>
              Start Now
            </button>
          )}
        </div>
      </div>

      {actionMsg && (
        <div style={{
          marginBottom: 16, padding: '8px 14px', borderRadius: 8, fontSize: 12,
          background: '#6366f122', border: '1px solid #6366f144', color: '#a5b4fc',
        }}>
          {actionMsg}
        </div>
      )}

      {/* Two-panel layout */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 320px', gap: 20, marginBottom: 24 }}>

        {/* Left: today's session */}
        <div style={{
          background: 'var(--surface-2)', border: '1px solid var(--border)',
          borderRadius: 12, padding: '20px 24px',
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>
              Today — {session?.trade_date || new Date().toISOString().slice(0, 10)}
            </div>
            {session ? <StatusBadge status={session.status} /> : (
              <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>No session yet</span>
            )}
          </div>

          {/* Live stats row */}
          {session && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 20 }}>
              {[
                { label: 'Net MTM', value: fmtINR(session.net_mtm_latest), color: (session.net_mtm_latest ?? 0) >= 0 ? '#4ade80' : '#f87171' },
                { label: 'Spot', value: session.spot_latest?.toFixed(0) || '—', color: 'var(--text-primary)' },
                { label: 'ATM Strike', value: session.atm_strike || '—', color: 'var(--text-primary)' },
                { label: 'Lock', value: session.lock_status === 'none' || !session.lock_status ? '🟢 Watching' : session.lock_status === 'profit_locked' ? '🔒 Profit' : '🛡️ Loss', color: '#94a3b8' },
              ].map(({ label, value, color }) => (
                <div key={label}>
                  <div style={{ fontSize: 10, color: 'var(--text-secondary)', marginBottom: 2 }}>{label}</div>
                  <div style={{ fontSize: 16, fontWeight: 700, color }}>{value}</div>
                </div>
              ))}
            </div>
          )}

          {/* Session meta */}
          {session && (session.atm_strike || session.expiry_date) && (
            <div style={{
              display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap',
              padding: '8px 12px', background: 'var(--surface)', borderRadius: 8, fontSize: 11,
            }}>
              {session.ce_symbol  && <span style={{ color: '#22d3ee' }}>CE: {session.ce_symbol.split(':')[1]}</span>}
              {session.pe_symbol  && <span style={{ color: '#f472b6' }}>PE: {session.pe_symbol.split(':')[1]}</span>}
              {session.expiry_date && <span style={{ color: 'var(--text-secondary)' }}>Expiry: {session.expiry_date}</span>}
              {session.approved_lots && <span style={{ color: 'var(--text-secondary)' }}>{session.approved_lots} lots</span>}
            </div>
          )}

          {/* MTM chart — waiting_spot_json prepended for pre-entry context */}
          {(mtmData.length > 0 || session?.waiting_spot_json?.length > 0) ? (
            <MtmChart
              data={[
                ...(session?.waiting_spot_json || []).map(r => ({ timestamp: r.timestamp, spot: r.spot })),
                ...mtmData,
              ]}
              entryTs={entryEvent?.timestamp}
              exitTs={exitEvent?.timestamp}
            />
          ) : (
            <div style={{
              height: 240, display: 'flex', alignItems: 'center', justifyContent: 'center',
              color: 'var(--text-secondary)', fontSize: 12, border: '1px dashed var(--border)', borderRadius: 8,
            }}>
              {session?.status === 'waiting' ? 'Waiting for entry at ' + config?.entry_time + '…' : 'Chart will appear once trade opens'}
            </div>
          )}

          {/* Final result */}
          {session?.realized_net_pnl != null && (
            <div style={{
              marginTop: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              padding: '10px 14px', background: 'var(--surface)', borderRadius: 8,
            }}>
              <div>
                <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>Final P&L</div>
                <div style={{ fontSize: 20, fontWeight: 800, color: session.realized_net_pnl >= 0 ? '#4ade80' : '#f87171' }}>
                  {fmtINR(session.realized_net_pnl)}
                </div>
              </div>
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', textAlign: 'right' }}>
                <div>{session.exit_reason?.replace(/_/g, ' ')}</div>
                {session.strategy_run_id && (
                  <button
                    onClick={() => navigate(`/workbench/replay/strategy_run/${session.strategy_run_id}`)}
                    style={{
                      marginTop: 4, background: 'none', border: '1px solid var(--border)',
                      borderRadius: 6, padding: '3px 8px', fontSize: 11, color: '#6366f1',
                      cursor: 'pointer',
                    }}
                  >
                    View Full Replay →
                  </button>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Right: config */}
        <div style={{
          background: 'var(--surface-2)', border: '1px solid var(--border)',
          borderRadius: 12, padding: '20px 24px',
        }}>
          <ConfigPanel config={config} onSave={handleSaveConfig} />

          <TokenSection tokenStatus={tokenStatus} onTokenSaved={() => { setTokenStatus('valid'); setActionMsg('Token saved.'); setTimeout(() => setActionMsg(null), 3000) }} />
        </div>
      </div>

      {/* Event log */}
      {events.length > 0 && (
        <div style={{
          background: 'var(--surface-2)', border: '1px solid var(--border)',
          borderRadius: 12, padding: '16px 20px', marginBottom: 24,
        }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 10 }}>
            Event Log
          </div>
          <EventLog events={events} />
        </div>
      )}

      {/* History */}
      {history.length > 0 && (
        <div style={{
          background: 'var(--surface-2)', border: '1px solid var(--border)',
          borderRadius: 12, padding: '16px 20px',
        }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--text-primary)', marginBottom: 12 }}>
            Past Sessions
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {history.map(s => (
              <div key={s.id} style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '8px 12px', background: 'var(--surface)', borderRadius: 8,
                cursor: s.strategy_run_id ? 'pointer' : 'default',
              }}
                onClick={() => s.strategy_run_id && navigate(`/workbench/replay/strategy_run/${s.strategy_run_id}`)}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{s.trade_date}</span>
                  <StatusBadge status={s.status} />
                  {s.exit_reason && <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{s.exit_reason.replace(/_/g, ' ')}</span>}
                </div>
                {s.realized_net_pnl != null && (
                  <span style={{
                    fontSize: 13, fontWeight: 700,
                    color: s.realized_net_pnl >= 0 ? '#4ade80' : '#f87171',
                  }}>
                    {fmtINR(s.realized_net_pnl)}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
