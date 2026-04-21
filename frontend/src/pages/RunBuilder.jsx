import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { createWorkbenchRun, getTradingDays, getWorkbenchStrategies } from '../api'
import { fmtINR, strategyStatusTone } from '../utils/workbench'

function defaultRunTypeFor(strategy) {
  return strategy?.modes?.[0] || 'paper_replay'
}

function normalizeConfig(strategy, runType) {
  return { ...(strategy?.defaults?.[runType] || {}) }
}

function ReadinessRail({ strategy, runType, readyDays, selectedDate, selectedRange }) {
  const readySet = new Set((readyDays || []).map(item => item.trade_date))
  const tone = strategyStatusTone(strategy?.status)
  const isReady = selectedDate ? readySet.has(selectedDate) : false

  return (
    <aside className="wb-card p-5">
      <div className="wb-kicker">Builder Context</div>
      <h2 className="text-lg font-semibold text-[var(--text-primary)] mt-1">{strategy?.name || 'Select a strategy'}</h2>
      <div className="mt-2 text-sm wb-muted">{strategy?.description || 'Choose a strategy to load execution defaults and parameter hints.'}</div>

      <div className="mt-4 flex flex-wrap gap-2">
        <span className="wb-chip" style={{ background: tone.background, borderColor: tone.border, color: tone.color }}>
          {tone.label}
        </span>
        {(strategy?.chips || []).map(chip => (
          <span key={chip} className="wb-chip">{chip}</span>
        ))}
      </div>

      <div className="mt-6 space-y-3 text-sm">
        <div className="wb-stat-row">
          <span>Run mode</span>
          <strong>{runType === 'paper_replay' ? 'Paper Replay' : 'Historical Backtest'}</strong>
        </div>
        <div className="wb-stat-row">
          <span>Executable now</span>
          <strong>{strategy?.status === 'available' ? 'Yes' : 'No'}</strong>
        </div>
        <div className="wb-stat-row">
          <span>Warehouse-ready days</span>
          <strong>{readyDays.length}</strong>
        </div>
        {selectedDate && (
          <div className="wb-stat-row">
            <span>Selected date ready</span>
            <strong style={{ color: isReady ? 'var(--green)' : 'var(--amber)' }}>
              {isReady ? 'Yes' : 'Paper-only / not in ready set'}
            </strong>
          </div>
        )}
        {selectedRange && (
          <div className="wb-stat-row">
            <span>Selected range</span>
            <strong>{selectedRange}</strong>
          </div>
        )}
      </div>

      <div className="mt-6 rounded-2xl p-4 border" style={{ borderColor: 'rgba(148,163,184,0.12)', background: 'rgba(8, 13, 23, 0.45)' }}>
        <div className="text-[11px] uppercase tracking-[0.24em] wb-muted">Execution notes</div>
        <ul className="mt-3 space-y-2 text-sm wb-muted list-disc pl-5">
          {(strategy?.notes || ['No extra notes for this strategy yet.']).map(note => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      </div>
    </aside>
  )
}

export default function RunBuilder() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [strategies, setStrategies] = useState([])
  const [strategyId, setStrategyId] = useState('orb_intraday_spread')
  const [runType, setRunType] = useState('paper_replay')
  const [config, setConfig] = useState({})
  const [readyDays, setReadyDays] = useState([])
  const [guidedMode, setGuidedMode] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    Promise.all([
      getWorkbenchStrategies(),
      getTradingDays({ backtest_ready: true, limit: 500 }),
    ])
      .then(([strategyRes, tradingDaysRes]) => {
        const list = strategyRes.data.strategies || []
        setStrategies(list)
        setReadyDays(tradingDaysRes.data || [])
        const requested = searchParams.get('strategy')
        const selected = list.find(item => item.id === requested) || list.find(item => item.id === 'orb_intraday_spread') || list[0]
        if (selected) {
          const nextRunType = defaultRunTypeFor(selected)
          setStrategyId(selected.id)
          setRunType(nextRunType)
          setConfig(normalizeConfig(selected, nextRunType))
        }
      })
      .catch(err => setError(err.response?.data?.detail || err.message))
      .finally(() => setLoading(false))
  }, [searchParams])

  const strategy = useMemo(
    () => strategies.find(item => item.id === strategyId) || null,
    [strategies, strategyId]
  )

  const modeOptions = strategy?.modes || []
  const canSubmit = strategy?.status === 'available'
  const fields = useMemo(
    () => (strategy?.params_schema || []).filter(field => !field.modes || field.modes.includes(runType)),
    [strategy, runType]
  )

  useEffect(() => {
    if (!strategy) return
    if (!modeOptions.includes(runType)) {
      const nextRunType = defaultRunTypeFor(strategy)
      setRunType(nextRunType)
      setConfig(normalizeConfig(strategy, nextRunType))
      return
    }
    const defaults = normalizeConfig(strategy, runType)
    setConfig(prev => ({ ...defaults, ...prev }))
  }, [strategyId]) // eslint-disable-line react-hooks/exhaustive-deps

  const setField = (key, value) => setConfig(prev => ({ ...prev, [key]: value }))

  const payloadPreview = useMemo(() => ({
    run_type: runType,
    strategy_id: strategyId,
    config,
  }), [runType, strategyId, config])

  const selectedRange = runType === 'historical_backtest' && config.start_date && config.end_date
    ? `${config.start_date} → ${config.end_date}`
    : null

  const handleStrategyChange = nextId => {
    const nextStrategy = strategies.find(item => item.id === nextId)
    if (!nextStrategy) return
    const nextRunType = defaultRunTypeFor(nextStrategy)
    setStrategyId(nextStrategy.id)
    setRunType(nextRunType)
    setConfig(normalizeConfig(nextStrategy, nextRunType))
  }

  const handleRunTypeChange = nextType => {
    setRunType(nextType)
    setConfig(normalizeConfig(strategy, nextType))
  }

  const handleSubmit = async event => {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const res = await createWorkbenchRun(payloadPreview)
      navigate(res.data.navigate_to)
    } catch (err) {
      setError(err.response?.data?.detail || err.message)
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-72 gap-2 wb-muted">
        <span className="spinner" /> Loading builder…
      </div>
    )
  }

  if (error && !strategy) {
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
          <div className="wb-kicker">Run Builder</div>
          <h1 className="wb-hero-title">One builder, multiple run modes.</h1>
          <p className="wb-hero-copy">
            The builder is strategy-aware, but it only enables flows the backend can actually execute.
            The current live path is the ORB spread executor across paper replay and historical batches.
          </p>
        </div>
      </section>

      <div className="wb-grid wb-grid-builder mt-6">
        <form className="wb-card p-6" onSubmit={handleSubmit}>
          <div className="flex items-center justify-between gap-4 flex-wrap">
            <div>
              <div className="wb-kicker">Configuration</div>
              <h2 className="text-xl font-semibold text-[var(--text-primary)] mt-1">Build a run payload</h2>
            </div>
            <button
              type="button"
              className="wb-secondary-button"
              onClick={() => setGuidedMode(prev => !prev)}
            >
              {guidedMode ? 'Switch to advanced' : 'Switch to guided'}
            </button>
          </div>

          <div className="mt-5 grid md:grid-cols-2 gap-4">
            <div>
              <label className="wb-label">Strategy</label>
              <select className="wb-input" value={strategyId} onChange={e => handleStrategyChange(e.target.value)}>
                {strategies.map(item => (
                  <option key={item.id} value={item.id}>{item.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="wb-label">Run mode</label>
              <select className="wb-input" value={runType} onChange={e => handleRunTypeChange(e.target.value)}>
                {modeOptions.map(mode => (
                  <option key={mode} value={mode}>
                    {mode === 'paper_replay' ? 'Paper replay' : 'Historical backtest'}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="mt-6 grid md:grid-cols-2 gap-4">
            {fields.map(field => {
              const value = config[field.key]
              const commonProps = {
                id: field.key,
                className: 'wb-input',
                value: field.type === 'boolean' ? undefined : value ?? '',
                onChange: event => {
                  if (field.type === 'boolean') {
                    setField(field.key, event.target.checked)
                  } else {
                    setField(field.key, event.target.value)
                  }
                },
              }

              return (
                <div key={`${runType}-${field.key}`} className={field.key === 'name' ? 'md:col-span-2' : ''}>
                  <label className="wb-label" htmlFor={field.key}>{field.label}</label>
                  {field.type === 'select' ? (
                    <select {...commonProps}>
                      {(field.options || []).map(option => (
                        <option key={option} value={option}>{option}</option>
                      ))}
                    </select>
                  ) : field.type === 'boolean' ? (
                    <label className="flex items-center gap-3 text-sm text-[var(--text-primary)]">
                      <input
                        id={field.key}
                        type="checkbox"
                        checked={Boolean(config[field.key])}
                        onChange={event => setField(field.key, event.target.checked)}
                      />
                      {field.label}
                    </label>
                  ) : (
                    <input
                      {...commonProps}
                      type={field.type === 'number' ? 'number' : field.type === 'date' ? 'date' : 'text'}
                      min={field.min}
                      max={field.max}
                    />
                  )}
                </div>
              )
            })}
          </div>

          {guidedMode ? (
            <div className="mt-6 rounded-2xl p-4 border" style={{ borderColor: 'rgba(148,163,184,0.12)', background: 'rgba(8, 13, 23, 0.45)' }}>
              <div className="wb-kicker">Guided Preview</div>
              <div className="mt-3 grid md:grid-cols-3 gap-4">
                <div>
                  <div className="text-[11px] uppercase tracking-[0.18em] wb-muted">Capital</div>
                  <div className="mt-1 text-sm text-[var(--text-primary)]">{fmtINR(config.capital)}</div>
                </div>
                <div>
                  <div className="text-[11px] uppercase tracking-[0.18em] wb-muted">Instrument</div>
                  <div className="mt-1 text-sm text-[var(--text-primary)]">{config.instrument || '—'}</div>
                </div>
                <div>
                  <div className="text-[11px] uppercase tracking-[0.18em] wb-muted">Execution path</div>
                  <div className="mt-1 text-sm text-[var(--text-primary)]">
                    {runType === 'paper_replay' ? 'Zerodha-backed replay' : 'Historical warehouse batch'}
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <div className="mt-6">
              <div className="wb-kicker mb-2">Payload Preview</div>
              <pre className="rounded-2xl p-4 overflow-auto text-xs" style={{ background: '#09111f', border: '1px solid var(--border)', color: '#b8c7de' }}>
                {JSON.stringify(payloadPreview, null, 2)}
              </pre>
            </div>
          )}

          {error && <div className="wb-alert-error mt-5">{error}</div>}

          {!canSubmit && (
            <div className="mt-5 wb-alert-warning">
              This strategy is visible in the catalog, but the backend executor is not live yet. You can inspect defaults here, but submission stays disabled.
            </div>
          )}

          <div className="mt-6 flex items-center justify-between gap-4 flex-wrap">
            <button type="button" className="wb-secondary-button" onClick={() => navigate('/workbench/strategies')}>
              Back to catalog
            </button>
            <button type="submit" className="wb-primary-button" disabled={!canSubmit || submitting}>
              {submitting ? 'Submitting…' : runType === 'paper_replay' ? 'Launch replay' : 'Create batch'}
            </button>
          </div>
        </form>

        <ReadinessRail
          strategy={strategy}
          runType={runType}
          readyDays={readyDays}
          selectedDate={config.date}
          selectedRange={selectedRange}
        />
      </div>
    </div>
  )
}
