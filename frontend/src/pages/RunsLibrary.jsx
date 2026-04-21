import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { compareWorkbenchRuns, getWorkbenchRuns } from '../api'
import { fmtDateTime, fmtINR, runKindLabel, runStatusTone } from '../utils/workbench'

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
    getWorkbenchRuns(kind === 'all' ? undefined : { kind })
      .then(res => {
        setRuns(res.data.runs || [])
        setSelectedRefs(prev => prev.filter(ref => (res.data.runs || []).some(item => `${item.kind}:${item.id}` === ref)))
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

  return (
    <div className="wb-page">
      <section className="wb-hero">
        <div>
          <div className="wb-kicker">Runs Library</div>
          <h1 className="wb-hero-title">Every replay and batch in one ledger.</h1>
          <p className="wb-hero-copy">
            The library unifies paper sessions and historical batches under one index. Session-level replay stays one click away, and compare is normalized over the same v2 shape.
          </p>
        </div>
        <div className="flex gap-3 flex-wrap">
          <button className="wb-primary-button" onClick={() => navigate('/workbench/run')}>New run</button>
          <button className="wb-secondary-button" onClick={() => navigate('/workbench/replay')}>Replay desk</button>
        </div>
      </section>

      <section className="wb-card p-4 mt-6">
        <div className="grid md:grid-cols-[auto_1fr_auto] gap-3 items-center">
          <select className="wb-input" value={kind} onChange={e => setKind(e.target.value)}>
            <option value="all">All run types</option>
            <option value="paper_session">Paper sessions</option>
            <option value="historical_batch">Historical batches</option>
          </select>
          <input
            className="wb-input"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search title, strategy, instrument, or summary"
          />
          <div className="text-sm wb-muted">{selectedRefs.length} selected for compare</div>
        </div>
      </section>

      {error && <div className="wb-alert-error mt-6">{error}</div>}

      {compareItems.length >= 2 && (
        <section className="wb-card p-5 mt-6">
          <div className="flex items-center justify-between gap-4">
            <div>
              <div className="wb-kicker">Compare</div>
              <h2 className="text-lg font-semibold text-[var(--text-primary)] mt-1">Selected runs</h2>
            </div>
            <button className="wb-secondary-button" onClick={() => setSelectedRefs([])}>
              Clear compare
            </button>
          </div>

          <div className="overflow-auto mt-4">
            <table className="w-full text-sm">
              <thead>
                <tr style={{ borderBottom: '1px solid var(--border)' }}>
                  <th className="text-left py-2 pr-4 wb-muted text-[11px] uppercase tracking-[0.18em]">Metric</th>
                  {compareItems.map(item => (
                    <th key={`${item.kind}:${item.id}`} className="text-left py-2 pr-4 text-[var(--text-primary)]">
                      {item.title}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[
                  ['Kind', item => runKindLabel(item.kind)],
                  ['Status', item => item.status],
                  ['P/L', item => fmtINR(item.pnl)],
                  ['Summary', item => item.summary],
                  ['Created', item => fmtDateTime(item.created_at)],
                  ['Capital', item => fmtINR(item.metrics?.capital)],
                ].map(([label, getter]) => (
                  <tr key={label} style={{ borderBottom: '1px solid rgba(39, 54, 75, 0.45)' }}>
                    <td className="py-3 pr-4 wb-muted">{label}</td>
                    {compareItems.map(item => (
                      <td key={`${item.kind}:${item.id}:${label}`} className="py-3 pr-4 text-[var(--text-primary)]">
                        {getter(item)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {loading ? (
        <div className="flex items-center justify-center h-64 gap-2 wb-muted">
          <span className="spinner" /> Loading library…
        </div>
      ) : (
        <section className="wb-grid wb-grid-2 mt-6">
          {filtered.map(run => {
            const tone = runStatusTone(run.status)
            const ref = `${run.kind}:${run.id}`
            const selected = selectedRefs.includes(ref)

            return (
              <article key={ref} className="wb-card p-5">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="text-lg font-semibold text-[var(--text-primary)]">{run.title}</div>
                    <div className="text-sm mt-1 wb-muted">{runKindLabel(run.kind)} · {run.subtitle}</div>
                  </div>
                  <span className="wb-chip" style={{ background: tone.background, borderColor: tone.border, color: tone.color }}>
                    {run.status}
                  </span>
                </div>

                <div className="mt-5 grid grid-cols-2 gap-4 text-sm">
                  <div>
                    <div className="wb-kicker">P/L</div>
                    <div className="mt-1" style={{ color: run.pnl == null ? 'var(--text-secondary)' : run.pnl >= 0 ? 'var(--green)' : 'var(--red)' }}>
                      {fmtINR(run.pnl)}
                    </div>
                  </div>
                  <div>
                    <div className="wb-kicker">Created</div>
                    <div className="mt-1 text-[var(--text-primary)]">{fmtDateTime(run.created_at)}</div>
                  </div>
                  <div>
                    <div className="wb-kicker">Instrument</div>
                    <div className="mt-1 text-[var(--text-primary)]">{run.instrument || '—'}</div>
                  </div>
                  <div>
                    <div className="wb-kicker">Summary</div>
                    <div className="mt-1 text-[var(--text-primary)]">{run.summary}</div>
                  </div>
                </div>

                <div className="mt-5 flex items-center justify-between gap-3">
                  <label className="flex items-center gap-2 text-sm wb-muted">
                    <input
                      type="checkbox"
                      checked={selected}
                      onChange={() => toggleCompare(run)}
                    />
                    Compare
                  </label>
                  <div className="flex gap-2">
                    {run.legacy_route && (
                      <button className="wb-secondary-button" onClick={() => navigate(run.legacy_route)}>
                        Legacy
                      </button>
                    )}
                    <button className="wb-primary-button" onClick={() => navigate(run.route)}>
                      Open
                    </button>
                  </div>
                </div>
              </article>
            )
          })}
        </section>
      )}

      {comparing && (
        <div className="fixed bottom-5 right-5 wb-card px-4 py-3 text-sm wb-muted">
          Preparing compare view…
        </div>
      )}
    </div>
  )
}
