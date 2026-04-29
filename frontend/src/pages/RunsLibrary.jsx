import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { exportStrategyRunsBundle, getWorkbenchRuns } from '../api'
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
  { value: 'strategy_run', label: 'Strategy Runs' },
  { value: 'paper_session', label: 'Paper Replay' },
  { value: 'historical_batch', label: 'Historical Batch' },
]

const PAGE_SIZE = 20

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

export default function RunsLibrary() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [runs, setRuns] = useState([])
  const [kind, setKind] = useState(searchParams.get('kind') || 'all')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [selectedIds, setSelectedIds] = useState(new Set())
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [error, setError] = useState(null)
  const sentinelRef = useRef(null)

  // Initial load + kind filter change: reset list
  useEffect(() => {
    setLoading(true)
    setError(null)
    setRuns([])
    setPage(0)
    setHasMore(false)
    setSelectedIds(new Set())
    const params = {
      limit: PAGE_SIZE + 1,
      offset: 0,
      ...(kind !== 'all' ? { kind } : {}),
    }
    getWorkbenchRuns(params)
      .then(res => {
        const allRows = res.data.runs || []
        setHasMore(allRows.length > PAGE_SIZE)
        setRuns(allRows.slice(0, PAGE_SIZE))
      })
      .catch(err => setError(err.response?.data?.detail || err.message))
      .finally(() => setLoading(false))
  }, [kind])

  // Subsequent pages: append
  useEffect(() => {
    if (page === 0) return
    setLoadingMore(true)
    setError(null)
    const params = {
      limit: PAGE_SIZE + 1,
      offset: page * PAGE_SIZE,
      ...(kind !== 'all' ? { kind } : {}),
    }
    getWorkbenchRuns(params)
      .then(res => {
        const allRows = res.data.runs || []
        setHasMore(allRows.length > PAGE_SIZE)
        setRuns(prev => [...prev, ...allRows.slice(0, PAGE_SIZE)])
      })
      .catch(err => setError(err.response?.data?.detail || err.message))
      .finally(() => setLoadingMore(false))
  }, [page]) // eslint-disable-line react-hooks/exhaustive-deps

  // IntersectionObserver: load next page when sentinel enters view
  useEffect(() => {
    if (!sentinelRef.current) return
    const observer = new IntersectionObserver(
      entries => {
        if (entries[0].isIntersecting && hasMore && !loadingMore && !loading) {
          setPage(p => p + 1)
        }
      },
      { threshold: 0.1 },
    )
    observer.observe(sentinelRef.current)
    return () => observer.disconnect()
  }, [hasMore, loadingMore, loading])

  const handleKindChange = useCallback((nextKind) => {
    setKind(nextKind)
  }, [])

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

  // IDs of visible strategy_run rows — the only ones eligible for CSV export
  const visibleStrategyRunIds = useMemo(
    () => filtered.filter(r => r.kind === 'strategy_run').map(r => r.id),
    [filtered],
  )

  const allSelected =
    visibleStrategyRunIds.length > 0 &&
    visibleStrategyRunIds.every(id => selectedIds.has(id))
  const someSelected = visibleStrategyRunIds.some(id => selectedIds.has(id))

  const toggleSelectAll = () => {
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (allSelected) {
        visibleStrategyRunIds.forEach(id => next.delete(id))
      } else {
        visibleStrategyRunIds.forEach(id => next.add(id))
      }
      return next
    })
  }

  const toggleSelectOne = run => {
    if (run.kind !== 'strategy_run') return
    setSelectedIds(prev => {
      const next = new Set(prev)
      if (next.has(run.id)) next.delete(run.id)
      else next.add(run.id)
      return next
    })
  }

  const exportableIds       = [...selectedIds]
  const exportCount         = exportableIds.length
  const willBeZip           = exportCount > 20
  const visibleSelectedCount = visibleStrategyRunIds.filter(id => selectedIds.has(id)).length

  const handleBundleExport = async () => {
    if (exportCount === 0 || exporting) return
    setExporting(true)
    setError(null)
    try {
      const res         = await exportStrategyRunsBundle(exportableIds)
      const contentDisp = res.headers?.['content-disposition'] || ''
      const match       = contentDisp.match(/filename="([^"]+)"/)
      const filename    = match ? match[1] : `bundle_${exportCount}runs.${willBeZip ? 'zip' : 'csv'}`
      const blob        = new Blob([res.data], { type: res.headers?.['content-type'] || 'text/csv' })
      const url         = URL.createObjectURL(blob)
      const a           = document.createElement('a')
      a.href     = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch (err) {
      setError(err.response?.data?.detail || 'Bundle export failed.')
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="mx-auto max-w-[1360px]" style={{ padding: '18px 20px 0', fontSize: 12 }}>
      <div className="space-y-4">
        {/* ── Header ── */}
        <div className="flex items-start justify-between gap-4">
          <div>
            <div style={{ fontSize: 15, fontWeight: 700, color: PALETTE.text, marginBottom: 3 }}>
              Runs Library
            </div>
            <div style={{ fontSize: 10, color: PALETTE.muted }} className="flex items-center gap-2">
              <span>
                {runs.length} loaded{hasMore ? ' · scroll for more' : ' · all loaded'}
                {exportCount > 0
                  ? ` · ${exportCount} selected${visibleSelectedCount < exportCount ? ` (${visibleSelectedCount} visible)` : ''}`
                  : ''}
              </span>
              {exportCount > 0 && (
                <button
                  type="button"
                  onClick={() => setSelectedIds(new Set())}
                  style={{ color: PALETTE.muted, background: 'transparent', cursor: 'pointer', fontSize: 9, textDecoration: 'underline' }}
                >
                  Clear
                </button>
              )}
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {KIND_FILTERS.map(filter => (
              <button
                key={filter.value}
                type="button"
                onClick={() => handleKindChange(filter.value)}
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

            {exportCount > 0 && (
              <button
                type="button"
                onClick={handleBundleExport}
                disabled={exporting}
                className="rounded-md px-3 py-1.5"
                style={{
                  background: willBeZip ? 'rgba(59,130,246,0.12)' : 'rgba(34,197,94,0.12)',
                  border: `1px solid ${willBeZip ? 'rgba(59,130,246,0.4)' : 'rgba(34,197,94,0.4)'}`,
                  color: willBeZip ? PALETTE.blue : PALETTE.green,
                  fontSize: 9,
                  fontWeight: 700,
                  cursor: exporting ? 'not-allowed' : 'pointer',
                  opacity: exporting ? 0.6 : 1,
                }}
                title={willBeZip ? `>20 runs — downloads as ZIP of individual CSVs` : `Download ${exportCount} run${exportCount > 1 ? 's' : ''} as single CSV`}
              >
                {exporting
                  ? 'Exporting…'
                  : willBeZip
                    ? `↓ Export ${exportCount} runs  ZIP`
                    : `↓ Export ${exportCount} run${exportCount > 1 ? 's' : ''}  CSV`}
              </button>
            )}
          </div>
        </div>

        {error ? <div className="wb-alert-error">{error}</div> : null}

        {/* ── Table ── */}
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
                  {/* Select-all checkbox — only meaningful when strategy_run rows are visible */}
                  <th style={{ padding: '8px 12px', width: 36 }}>
                    {visibleStrategyRunIds.length > 0 && (
                      <input
                        type="checkbox"
                        checked={allSelected}
                        ref={el => { if (el) el.indeterminate = someSelected && !allSelected }}
                        onChange={toggleSelectAll}
                        title={allSelected ? 'Deselect all' : `Select all ${visibleStrategyRunIds.length} strategy runs`}
                        style={{ cursor: 'pointer' }}
                      />
                    )}
                  </th>
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
                  const isStrategyRun = run.kind === 'strategy_run'
                  const selected      = isStrategyRun && selectedIds.has(run.id)
                  return (
                    <tr
                      key={run.id}
                      style={{
                        borderTop: `0.5px solid ${PALETTE.border}`,
                        background: selected ? 'rgba(34,197,94,0.04)' : 'transparent',
                      }}
                    >
                      <td style={{ padding: '8px 12px' }}>
                        {isStrategyRun ? (
                          <input
                            type="checkbox"
                            checked={selected}
                            onChange={() => toggleSelectOne(run)}
                            aria-label={`select ${run.title}`}
                            style={{ cursor: 'pointer' }}
                          />
                        ) : null}
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
              <span>Check strategy run rows to export. ≤20 runs → single CSV · &gt;20 runs → ZIP of individual CSVs.</span>
              <span>{filtered.length} rows shown</span>
            </div>

            <div ref={sentinelRef} style={{ height: 1 }} />

            {loadingMore && (
              <div
                className="flex items-center justify-center gap-2"
                style={{ padding: '10px 0', fontSize: 10, color: PALETTE.muted }}
              >
                <span className="spinner" /> Loading more…
              </div>
            )}

            {!hasMore && !loading && runs.length > 0 && (
              <div
                className="text-center"
                style={{ padding: '8px 0', fontSize: 9, color: '#334155' }}
              >
                All {runs.length} runs loaded
              </div>
            )}
          </section>
        )}
      </div>

      <div
        className="text-center"
        style={{ color: '#475569', fontSize: 10, padding: '7px 0', marginTop: 18, borderTop: '0.5px solid #1a2540' }}
      >
        For educational and backtesting purposes only · Not financial advice
      </div>
    </div>
  )
}
