import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { compareWorkbenchRuns, getWorkbenchRuns } from '../api'
import { fmtDateTime, fmtINR, fmtShortDate, runKindLabel, runStatusTone } from '../utils/workbench'

const PALETTE = {
  bg: 'var(--surface)',
  card: 'var(--surface-secondary)',
  border: 'var(--border)',
  text: 'var(--text-primary)',
  muted: 'var(--text-secondary)',
  blue: '#3b82f6',
  blueSoft: 'rgba(59,130,246,0.12)',
  green: '#22c55e',
  red: '#ef4444',
  amber: '#f59e0b',
}

const KIND_FILTERS = [
  { value: 'all', label: 'All Runs' },
  { value: 'paper_session', label: 'Paper Replay' },
  { value: 'historical_batch', label: 'Historical Batch' },
]

function StatusBadge({ status }) {
  const tone = runStatusTone(status)
  return (
    <span
      className="inline-flex items-center rounded-md px-2.5 py-1 font-semibold"
      style={{
        background: tone.background,
        border: `1px solid ${tone.border}`,
        color: tone.color,
        fontSize: 10,
      }}
    >
      {status}
    </span>
  )
}

function CompareBars({ items }) {
  const points = items
    .map(item => ({
      key: `${item.kind}:${item.id}`,
      label: item.strategy_name || item.title,
      value: Number(item.pnl || 0),
      color: Number(item.pnl || 0) >= 0 ? PALETTE.blue : PALETTE.amber,
    }))

  const maxAbs = Math.max(...points.map(point => Math.abs(point.value)), 1)
  const width = 520
  const height = 90
  const zeroX = width / 2
  const rowHeight = height / Math.max(points.length, 1)

  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} style={{ width: '100%', height: 82, display: 'block' }}>
        <line x1={zeroX} y1={0} x2={zeroX} y2={height} stroke="rgba(51,65,85,0.85)" strokeWidth="1" />
        {points.map((point, index) => {
          const y = index * rowHeight + rowHeight / 2
          const barWidth = (Math.abs(point.value) / maxAbs) * (width / 2 - 22)
          const x = point.value >= 0 ? zeroX : zeroX - barWidth
          return (
            <g key={point.key}>
              <rect
                x={x}
                y={y - 8}
                width={Math.max(barWidth, 6)}
                height={16}
                rx={4}
                fill={point.color}
                fillOpacity="0.2"
                stroke={point.color}
                strokeOpacity="0.5"
              />
              <text
                x={point.value >= 0 ? x + Math.max(barWidth, 6) + 8 : x - 8}
                y={y + 3}
                textAnchor={point.value >= 0 ? 'start' : 'end'}
                style={{ fontSize: 9, fill: point.color, fontWeight: 700 }}
              >
                {fmtINR(point.value)}
              </text>
            </g>
          )
        })}
      </svg>
      <div className="flex flex-wrap gap-3" style={{ marginTop: 6 }}>
        {points.map(point => (
          <div key={point.key} className="flex items-center gap-2">
            <div style={{ width: 16, height: 2, background: point.color, borderRadius: 1 }} />
            <span style={{ fontSize: 9, color: '#64748b' }}>{point.label}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function RunsLibrary() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [runs, setRuns] = useState([])
  const [kind, setKind] = useState(searchParams.get('kind') || 'all')
  const [query, setQuery] = useState('')
  const [selectedRefs, setSelectedRefs] = useState([])
  const [compareItems, setCompareItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [comparing, setComparing] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    getWorkbenchRuns(kind === 'all' ? undefined : { kind })
      .then(res => {
        const nextRuns = res.data.runs || []
        setRuns(nextRuns)
        setSelectedRefs(prev => prev.filter(ref => nextRuns.some(item => `${item.kind}:${item.id}` === ref)))
      })
      .catch(err => setError(err.response?.data?.detail || err.message))
      .finally(() => setLoading(false))
  }, [kind])

  useEffect(() => {
    if (selectedRefs.length < 2) {
      setCompareItems([])
      return
    }
    setComparing(true)
    setError(null)
    compareWorkbenchRuns(selectedRefs.join(','))
      .then(res => setCompareItems(res.data.items || []))
      .catch(err => setError(err.response?.data?.detail || err.message))
      .finally(() => setComparing(false))
  }, [selectedRefs])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return runs.filter(run => {
      if (!q) return true
      return [run.title, run.subtitle, run.summary, run.strategy_name, run.instrument]
        .join(' ')
        .toLowerCase()
        .includes(q)
    })
  }, [runs, query])

  const toggleCompare = run => {
    const ref = `${run.kind}:${run.id}`
    setSelectedRefs(prev => {
      if (prev.includes(ref)) return prev.filter(item => item !== ref)
      if (prev.length >= 4) return prev
      return [...prev, ref]
    })
  }

  const compareSummary = useMemo(() => {
    if (compareItems.length < 2) return null
    const positive = compareItems.filter(item => Number(item.pnl || 0) >= 0).length
    const totalPnl = compareItems.reduce((sum, item) => sum + Number(item.pnl || 0), 0)
    return {
      totalPnl,
      winners: positive,
    }
  }, [compareItems])

  return (
    <div className="mx-auto max-w-[1360px]" style={{ padding: '18px 20px 0', fontSize: 12 }}>
      <div className="space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <div style={{ fontSize: 15, fontWeight: 700, color: PALETTE.text, marginBottom: 3 }}>
              Runs Library
            </div>
            <div style={{ fontSize: 10, color: PALETTE.muted }}>
              {filtered.length} runs · select 2 or more for compare
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <span style={{ fontSize: 10, color: PALETTE.muted }}>{selectedRefs.length} selected</span>
            {KIND_FILTERS.map(filter => (
              <button
                key={filter.value}
                type="button"
                onClick={() => setKind(filter.value)}
                className="rounded-md px-3 py-1.5"
                style={{
                  background: kind === filter.value ? PALETTE.blueSoft : PALETTE.card,
                  border: `1px solid ${kind === filter.value ? 'rgba(59,130,246,0.4)' : PALETTE.border}`,
                  color: kind === filter.value ? PALETTE.blue : PALETTE.muted,
                  fontSize: 9,
                  fontWeight: 600,
                  cursor: 'pointer',
                }}
              >
                {filter.label}
              </button>
            ))}
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              placeholder="Search…"
              className="rounded-md px-3 py-1.5 outline-none"
              style={{
                width: 190,
                background: PALETTE.card,
                border: `1px solid ${PALETTE.border}`,
                color: PALETTE.text,
                fontSize: 10,
              }}
            />
          </div>
        </div>

        {error ? <div className="wb-alert-error">{error}</div> : null}

        {compareItems.length >= 2 ? (
          <section
            className="rounded-[10px]"
            style={{
              background: PALETTE.card,
              border: '1px solid rgba(59,130,246,0.3)',
              padding: 14,
            }}
          >
            <div
              className="mb-3 text-[10px] uppercase tracking-[0.08em]"
              style={{ color: PALETTE.muted, fontWeight: 500 }}
            >
              Compare: P&amp;L Snapshot
            </div>

            <div className="grid gap-3 md:grid-cols-2" style={{ marginBottom: 12 }}>
              {compareItems.map(item => (
                <div
                  key={`${item.kind}:${item.id}`}
                  className="rounded-[7px]"
                  style={{
                    background: PALETTE.bg,
                    border: `1px solid rgba(59,130,246,0.25)`,
                    padding: '9px 12px',
                  }}
                >
                  <div style={{ fontSize: 11, fontWeight: 600, color: PALETTE.text, marginBottom: 2 }}>
                    {item.strategy_name || item.title}
                  </div>
                  <div style={{ fontSize: 9, color: PALETTE.muted, marginBottom: 6 }}>
                    {fmtShortDate(item.date_label || item.subtitle)} · {item.instrument || runKindLabel(item.kind)}
                  </div>
                  <div
                    style={{
                      fontSize: 13,
                      fontWeight: 700,
                      color: Number(item.pnl || 0) >= 0 ? PALETTE.green : PALETTE.red,
                    }}
                  >
                    {fmtINR(item.pnl)}
                  </div>
                </div>
              ))}
            </div>

            <CompareBars items={compareItems} />

            {compareSummary ? (
              <div className="mt-3 flex flex-wrap gap-4" style={{ fontSize: 10, color: PALETTE.muted }}>
                <span>Total compare P&amp;L: <strong style={{ color: compareSummary.totalPnl >= 0 ? PALETTE.green : PALETTE.red }}>{fmtINR(compareSummary.totalPnl)}</strong></span>
                <span>Positive runs: <strong style={{ color: PALETTE.text }}>{compareSummary.winners}</strong></span>
                <button
                  type="button"
                  onClick={() => setSelectedRefs([])}
                  style={{ color: PALETTE.blue, background: 'transparent', cursor: 'pointer', fontSize: 10, fontWeight: 600 }}
                >
                  Clear compare
                </button>
              </div>
            ) : null}
          </section>
        ) : null}

        {loading ? (
          <div className="flex items-center justify-center h-64 gap-2" style={{ color: PALETTE.muted }}>
            <span className="spinner" /> Loading library…
          </div>
        ) : (
          <section
            className="rounded-[10px] overflow-hidden"
            style={{ background: PALETTE.card, border: `1px solid ${PALETTE.border}` }}
          >
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <thead>
                <tr style={{ background: '#334155' }}>
                  <th style={{ padding: '8px 12px', width: 36 }} />
                  {['Date', 'Strategy', 'Instrument', 'Type', 'Created', 'Net P&L', 'Status', ''].map(header => (
                    <th
                      key={header}
                      style={{
                        textAlign: 'left',
                        padding: '8px 12px',
                        fontSize: 9,
                        fontWeight: 500,
                        textTransform: 'uppercase',
                        letterSpacing: '0.06em',
                        color: PALETTE.muted,
                      }}
                    >
                      {header}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {filtered.map(run => {
                  const ref = `${run.kind}:${run.id}`
                  const selected = selectedRefs.includes(ref)
                  return (
                    <tr
                      key={ref}
                      style={{
                        borderTop: `0.5px solid ${PALETTE.border}`,
                        background: selected ? 'rgba(59,130,246,0.04)' : 'transparent',
                      }}
                    >
                      <td style={{ padding: '8px 12px' }}>
                        <input
                          type="checkbox"
                          checked={selected}
                          onChange={() => toggleCompare(run)}
                          aria-label={`compare ${run.title}`}
                        />
                      </td>
                      <td style={{ padding: '8px 12px', fontWeight: 600, color: PALETTE.text }}>
                        {fmtShortDate(run.date_label || run.subtitle)}
                      </td>
                      <td style={{ padding: '8px 12px', color: PALETTE.text }}>
                        {run.strategy_name || run.title}
                      </td>
                      <td style={{ padding: '8px 12px', color: PALETTE.muted }}>
                        {run.instrument || '—'}
                      </td>
                      <td style={{ padding: '8px 12px', color: PALETTE.muted }}>
                        {runKindLabel(run.kind)}
                      </td>
                      <td style={{ padding: '8px 12px', color: PALETTE.muted }}>
                        {fmtDateTime(run.created_at)}
                      </td>
                      <td
                        style={{
                          padding: '8px 12px',
                          fontWeight: 600,
                          color: run.pnl == null ? PALETTE.muted : run.pnl >= 0 ? PALETTE.green : PALETTE.red,
                        }}
                      >
                        {fmtINR(run.pnl)}
                      </td>
                      <td style={{ padding: '8px 12px' }}>
                        <StatusBadge status={run.status} />
                      </td>
                      <td style={{ padding: '8px 12px' }}>
                        <div className="flex items-center gap-2">
                          {run.legacy_route ? (
                            <button
                              type="button"
                              onClick={() => navigate(run.legacy_route)}
                              className="rounded-md px-2.5 py-1"
                              style={{
                                background: PALETTE.card,
                                border: `1px solid ${PALETTE.border}`,
                                color: PALETTE.muted,
                                fontSize: 9,
                                cursor: 'pointer',
                              }}
                            >
                              Legacy
                            </button>
                          ) : null}
                          <button
                            type="button"
                            onClick={() => navigate(run.route)}
                            style={{ color: PALETTE.blue, background: 'transparent', cursor: 'pointer', fontSize: 10, fontWeight: 600 }}
                          >
                            Open →
                          </button>
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>

            <div
              style={{
                padding: '5px 12px',
                fontSize: 9,
                color: '#64748b',
                borderTop: `0.5px solid ${PALETTE.border}`,
                display: 'flex',
                justifyContent: 'space-between',
              }}
            >
              <span>Click a row action to open replay or detail view. Compare supports up to 4 runs.</span>
              <span>{filtered.length} rows</span>
            </div>
          </section>
        )}
      </div>

      {comparing ? (
        <div
          className="fixed bottom-5 right-5 rounded-[8px]"
          style={{
            background: PALETTE.card,
            border: `1px solid ${PALETTE.border}`,
            color: PALETTE.muted,
            fontSize: 10,
            padding: '10px 14px',
          }}
        >
          Preparing compare view…
        </div>
      ) : null}

      <div
        className="text-center"
        style={{ color: '#475569', fontSize: 10, padding: '7px 0', marginTop: 18, borderTop: '0.5px solid #1a2540' }}
      >
        For educational and backtesting purposes only · Not financial advice
      </div>
    </div>
  )
}
