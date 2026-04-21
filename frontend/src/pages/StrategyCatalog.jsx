import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getWorkbenchStrategies } from '../api'
import { groupStrategiesByBias, strategyStatusTone } from '../utils/workbench'

export default function StrategyCatalog() {
  const navigate = useNavigate()
  const [strategies, setStrategies] = useState([])
  const [query, setQuery] = useState('')
  const [status, setStatus] = useState('all')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    getWorkbenchStrategies()
      .then(res => setStrategies(res.data.strategies || []))
      .catch(err => setError(err.response?.data?.detail || err.message))
      .finally(() => setLoading(false))
  }, [])

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    return strategies.filter(strategy => {
      if (status !== 'all' && strategy.status !== status) return false
      if (!q) return true
      const haystack = [
        strategy.name,
        strategy.playbook,
        strategy.description,
        ...(strategy.chips || []),
      ].join(' ').toLowerCase()
      return haystack.includes(q)
    })
  }, [strategies, query, status])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-72 gap-2 wb-muted">
        <span className="spinner" /> Loading strategy catalog…
      </div>
    )
  }

  if (error) {
    return (
      <div className="max-w-6xl mx-auto p-6">
        <div className="wb-alert-error">{error}</div>
      </div>
    )
  }

  return (
    <div className="wb-page">
      <section className="wb-hero">
        <div>
          <div className="wb-kicker">Strategy Catalog</div>
          <h1 className="wb-hero-title">Catalog first, execution second.</h1>
          <p className="wb-hero-copy">
            The new workbench uses a metadata-driven catalog. Live strategies can launch immediately.
            Planned and research tracks stay visible so the product can scale without changing the shell.
          </p>
        </div>
      </section>

      <section className="wb-card p-4 mt-6">
        <div className="grid md:grid-cols-[1fr_auto_auto] gap-3 items-center">
          <input
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search by strategy, payoff, or tag"
            className="wb-input"
          />
          <select className="wb-input" value={status} onChange={e => setStatus(e.target.value)}>
            <option value="all">All statuses</option>
            <option value="available">Live</option>
            <option value="planned">Planned</option>
            <option value="research">Research</option>
          </select>
          <button className="wb-secondary-button" onClick={() => navigate('/workbench/run')}>
            Open run builder
          </button>
        </div>
      </section>

      <div className="mt-6 space-y-8">
        {groupStrategiesByBias(filtered).map(group => (
          <section key={group.key}>
            <div className="flex items-center justify-between mb-4">
              <div>
                <div className="wb-kicker">{group.label}</div>
                <h2 className="text-xl font-semibold text-[var(--text-primary)]">{group.label} setups</h2>
              </div>
              <span className="wb-chip">{group.items.length} strategies</span>
            </div>

            <div className="wb-grid wb-grid-3">
              {group.items.map(strategy => {
                const tone = strategyStatusTone(strategy.status)
                const canRun = strategy.status === 'available'
                return (
                  <article key={strategy.id} className="wb-card p-5 flex flex-col">
                    <div className="flex items-start justify-between gap-4">
                      <div>
                        <div className="text-lg font-semibold text-[var(--text-primary)]">{strategy.name}</div>
                        <div className="text-sm mt-1 wb-muted">{strategy.playbook}</div>
                      </div>
                      <span className="wb-chip" style={{ background: tone.background, borderColor: tone.border, color: tone.color }}>
                        {tone.label}
                      </span>
                    </div>

                    <p className="text-sm mt-4 leading-6 wb-muted flex-1">{strategy.description}</p>

                    <div className="flex flex-wrap gap-2 mt-4">
                      {(strategy.chips || []).map(chip => (
                        <span key={chip} className="wb-chip">{chip}</span>
                      ))}
                    </div>

                    {strategy.notes?.length > 0 && (
                      <div className="mt-4 rounded-2xl p-3 border" style={{ borderColor: 'rgba(148,163,184,0.12)', background: 'rgba(8, 13, 23, 0.45)' }}>
                        <div className="text-[11px] uppercase tracking-[0.24em] wb-muted">Notes</div>
                        <div className="mt-2 text-sm wb-muted">{strategy.notes[0]}</div>
                      </div>
                    )}

                    <div className="mt-5 flex items-center justify-between gap-3">
                      <span className="text-xs wb-muted">{strategy.family?.replace('_', ' ')}</span>
                      <button
                        className={canRun ? 'wb-primary-button' : 'wb-secondary-button'}
                        onClick={() => navigate(`/workbench/run?strategy=${strategy.id}`)}
                      >
                        {canRun ? 'Use strategy' : 'Inspect in builder'}
                      </button>
                    </div>
                  </article>
                )
              })}
            </div>
          </section>
        ))}
      </div>
    </div>
  )
}
