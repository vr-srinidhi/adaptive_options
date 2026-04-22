import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getWorkbenchStrategies } from '../api'

const STEP_LABELS = ['Strategies', 'Run Builder', 'Replay']

const PALETTE = {
  bg: 'var(--surface)',
  card: 'var(--surface-secondary)',
  border: 'var(--border)',
  text: 'var(--text-primary)',
  muted: 'var(--text-secondary)',
  blue: '#3b82f6',
  blueSoft: 'rgba(59,130,246,0.12)',
  green: '#22c55e',
  greenSoft: 'rgba(34,197,94,0.12)',
  red: '#ef4444',
  redSoft: 'rgba(239,68,68,0.12)',
  amber: '#f59e0b',
  amberSoft: 'rgba(245,158,11,0.12)',
}

const PAYOFF_SHAPES = {
  'short-straddle': '4,88 30,26 56,88 96,88',
  'short-strangle': '4,88 26,88 40,26 58,26 72,88 96,88',
  'iron-butterfly': '4,88 28,88 44,26 58,26 74,88 96,88',
  'iron-condor': '4,88 22,88 36,28 62,28 76,88 96,88',
  'buy-call': '4,88 42,88 58,64 96,64',
  'bull-call': '4,88 26,88 48,40 96,40',
  'bull-put': '4,34 34,34 54,88 96,88',
  'sell-put': '4,34 38,88 96,88',
  'buy-put': '4,64 42,64 58,88 96,88',
  'bear-put': '4,40 28,40 52,88 96,88',
  'bear-call': '4,88 40,88 60,34 96,34',
  'sell-call': '4,88 54,88 96,34',
  adaptive: '4,88 30,88 44,42 60,42 76,88 96,88',
}

const CARD_META = {
  orb_intraday_spread: { view: 'Adaptive / ORB', risk: 'Defined risk', type: 'Debit', payoff: 'adaptive', intraday: true, expiry: false },
  buy_call: { view: 'Strongly bullish', risk: 'Defined risk', type: 'Debit', payoff: 'buy-call', intraday: true, expiry: true },
  sell_put: { view: 'Bullish / IV sell', risk: 'High risk', type: 'Credit', payoff: 'sell-put', intraday: true, expiry: false },
  bull_call_spread: { view: 'Moderately bullish', risk: 'Defined risk', type: 'Debit', payoff: 'bull-call', intraday: true, expiry: true },
  bull_put_spread: { view: 'Bullish / sideways', risk: 'Defined risk', type: 'Credit', payoff: 'bull-put', intraday: true, expiry: true },
  buy_put: { view: 'Strongly bearish', risk: 'Defined risk', type: 'Debit', payoff: 'buy-put', intraday: true, expiry: true },
  sell_call: { view: 'Bearish / IV sell', risk: 'High risk', type: 'Credit', payoff: 'sell-call', intraday: true, expiry: false },
  bear_call_spread: { view: 'Moderately bearish', risk: 'Defined risk', type: 'Credit', payoff: 'bear-call', intraday: true, expiry: true },
  bear_put_spread: { view: 'Moderately bearish', risk: 'Defined risk', type: 'Debit', payoff: 'bear-put', intraday: true, expiry: true },
  short_straddle: { view: 'Sideways / IV crush', risk: 'High risk', type: 'Credit', payoff: 'short-straddle', intraday: true, expiry: false },
  short_strangle: { view: 'Sideways / IV sell', risk: 'High risk', type: 'Credit', payoff: 'short-strangle', intraday: true, expiry: true },
  iron_butterfly: { view: 'Low volatility', risk: 'Defined risk', type: 'Credit', payoff: 'iron-butterfly', intraday: true, expiry: true },
  iron_condor: { view: 'Range bound', risk: 'Defined risk', type: 'Credit', payoff: 'iron-condor', intraday: true, expiry: true },
  paper_trade_replay_orb: { view: 'ORB STRATEGY', risk: 'Defined risk', type: 'Replay', payoff: 'adaptive', intraday: true, expiry: false },
}

const TAB_ORDER = [
  { key: 'bullish', label: 'Bullish', badgeColor: PALETTE.green },
  { key: 'bearish', label: 'Bearish', badgeColor: PALETTE.red },
  { key: 'neutral', label: 'Neutral', badgeColor: PALETTE.blue },
  { key: 'others', label: 'Others', badgeColor: '#64748b' },
]

const RECENTLY_USED = ['Short Strangle', 'Bull Put Spread', 'Iron Condor']
const PAPER_REPLAY_ENTRY = {
  id: 'paper_trade_replay_orb',
  name: 'Paper Trade Replay',
  bias: 'other',
  status: 'available',
  family: 'replay',
  chips: ['Paper'],
  externalPath: '/paper',
}

function toTabKey(strategy) {
  if (strategy.bias === 'bullish') return 'bullish'
  if (strategy.bias === 'bearish') return 'bearish'
  if (strategy.bias === 'neutral') return 'neutral'
  return 'others'
}

function stepsCrumb() {
  return (
    <div
      className="flex items-center gap-2 px-1 pb-3 text-[10px]"
      style={{ color: PALETTE.muted, borderBottom: `0.5px solid ${PALETTE.border}` }}
    >
      {STEP_LABELS.map((step, index) => {
        const active = index === 0
        const prior = index < 1
        return (
          <div key={step} className="flex items-center gap-2">
            <span style={{ color: active || prior ? PALETTE.text : PALETTE.muted }}>{index + 1}.</span>
            <span style={{ color: active ? PALETTE.text : PALETTE.muted, fontWeight: active ? 700 : 500 }}>{step}</span>
            {index < STEP_LABELS.length - 1 ? <span>›</span> : null}
          </div>
        )
      })}
    </div>
  )
}

function payoffMeta(strategy) {
  return CARD_META[strategy.id] || {
    view: strategy.family?.replaceAll('_', ' ') || 'Strategy',
    risk: strategy.status === 'available' ? 'Defined risk' : 'Preview only',
    type: strategy.family?.includes('credit') ? 'Credit' : 'Debit',
    payoff: 'adaptive',
    intraday: true,
    expiry: false,
  }
}

function chipTone(label, kind) {
  if (kind === 'risk') {
    return label === 'High risk'
      ? { color: PALETTE.red, background: PALETTE.redSoft, border: 'rgba(239,68,68,0.3)' }
      : { color: PALETTE.green, background: PALETTE.greenSoft, border: 'rgba(34,197,94,0.3)' }
  }
  if (kind === 'type') {
    return label === 'Credit'
      ? { color: PALETTE.blue, background: PALETTE.blueSoft, border: 'rgba(59,130,246,0.3)' }
      : { color: PALETTE.amber, background: PALETTE.amberSoft, border: 'rgba(245,158,11,0.3)' }
  }
  return label.includes('Intraday')
    ? { color: PALETTE.green, background: PALETTE.greenSoft, border: 'rgba(34,197,94,0.25)' }
    : { color: '#64748b', background: 'rgba(100,116,139,0.12)', border: 'rgba(100,116,139,0.25)' }
}

function MiniPayoff({ payoff }) {
  const points = PAYOFF_SHAPES[payoff] || PAYOFF_SHAPES.adaptive
  return (
    <svg viewBox="0 0 100 54" width="86" height="42" aria-hidden="true">
      <line x1="4" y1="42" x2="96" y2="42" stroke="rgba(148,163,184,0.16)" strokeWidth="0.7" />
      <polyline
        points={points}
        fill="none"
        stroke={PALETTE.blue}
        strokeWidth="2.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function StrategyChip({ label, kind }) {
  const tone = chipTone(label, kind)
  return (
    <span
      className="inline-flex items-center rounded-md px-2 py-0.5 text-[10px] font-semibold"
      style={{ color: tone.color, background: tone.background, border: `1px solid ${tone.border}` }}
    >
      {label}
    </span>
  )
}

function StrategyCard({ strategy, onUse, dimmed = false }) {
  const meta = payoffMeta(strategy)

  return (
    <article
      className="rounded-[10px] p-4 flex flex-col gap-2.5"
      style={{
        background: PALETTE.bg,
        border: `1px solid ${PALETTE.border}`,
        opacity: dimmed ? 0.45 : 1,
      }}
    >
      <MiniPayoff payoff={meta.payoff} />

      <div>
        <div className="text-[12px] font-bold" style={{ color: PALETTE.text }}>
          {strategy.name}
        </div>
        <div className="mt-1 text-[10px]" style={{ color: PALETTE.muted }}>
          {meta.view}
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        <StrategyChip label={meta.risk} kind="risk" />
        <StrategyChip label={meta.type} kind="type" />
      </div>

      <div className="flex flex-wrap gap-2">
        {meta.intraday ? <StrategyChip label="Intraday ✓" kind="scope" /> : null}
        {meta.expiry ? <StrategyChip label="Expiry ✓" kind="scope" /> : null}
      </div>

      <div className="mt-auto flex gap-2">
        <button
          type="button"
          onClick={() => onUse(strategy.id)}
          className="flex-1 rounded-md px-3 py-2 text-[11px] font-bold"
          style={{ background: PALETTE.blue, color: '#fff', cursor: 'pointer' }}
        >
          Use →
        </button>
        <button
          type="button"
          onClick={() => onUse(strategy.id)}
          className="rounded-md px-3 py-2 text-[11px] font-semibold"
          style={{ background: 'transparent', color: PALETTE.muted, border: `1px solid ${PALETTE.border}`, cursor: 'pointer' }}
        >
          Info
        </button>
      </div>
    </article>
  )
}

export default function StrategyCatalog() {
  const navigate = useNavigate()
  const [strategies, setStrategies] = useState([])
  const [query, setQuery] = useState('')
  const [activeTab, setActiveTab] = useState('neutral')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  useEffect(() => {
    getWorkbenchStrategies()
      .then(res => setStrategies([...(res.data.strategies || []), PAPER_REPLAY_ENTRY]))
      .catch(err => setError(err.response?.data?.detail || err.message))
      .finally(() => setLoading(false))
  }, [])

  const grouped = useMemo(() => {
    const groups = { bullish: [], bearish: [], neutral: [], others: [] }
    strategies.forEach(strategy => {
      groups[toTabKey(strategy)].push(strategy)
    })
    return groups
  }, [strategies])

  useEffect(() => {
    if (!strategies.length) return
    if ((grouped[activeTab] || []).length > 0) return
    const fallback = TAB_ORDER.find(tab => (grouped[tab.key] || []).length > 0)
    if (fallback && fallback.key !== activeTab) {
      setActiveTab(fallback.key)
    }
  }, [activeTab, grouped, strategies.length])

  const filteredGrouped = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return grouped

    const matches = strategy => {
      const meta = payoffMeta(strategy)
      return [
        strategy.name,
        meta.view,
        meta.risk,
        meta.type,
        ...(strategy.chips || []),
      ].join(' ').toLowerCase().includes(q)
    }

    return Object.fromEntries(
      Object.entries(grouped).map(([key, list]) => [key, list.filter(matches)])
    )
  }, [grouped, query])

  const activeStrategies = filteredGrouped[activeTab] || []
  const previewTab = TAB_ORDER.find(tab => tab.key !== activeTab && (filteredGrouped[tab.key] || []).length)?.key
  const previewStrategies = previewTab ? filteredGrouped[previewTab] : []

  const handleUse = strategyId => {
    const selected = strategies.find(strategy => strategy.id === strategyId)
    if (selected?.externalPath) {
      navigate(selected.externalPath)
      return
    }
    navigate(`/workbench/run?strategy=${strategyId}`)
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-72 gap-2" style={{ color: PALETTE.muted }}>
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
    <div className="mx-auto max-w-[1360px] px-4 pb-10 pt-4" style={{ fontSize: 11 }}>
      {stepsCrumb()}

      <div className="pt-6">
        <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
          <div>
            <div className="text-[15px] font-bold leading-none" style={{ color: PALETTE.text }}>
              Strategy Catalog
            </div>
            <div className="mt-2 text-[10px]" style={{ color: PALETTE.muted }}>
              Select a strategy template to open the Run Builder
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <div className="text-[10px]" style={{ color: '#64748b' }}>Recently used:</div>
            {RECENTLY_USED.map(item => (
              <span
                key={item}
                className="rounded-md px-3 py-1 text-[10px] font-medium"
                style={{ background: PALETTE.card, border: `1px solid ${PALETTE.border}`, color: PALETTE.muted }}
              >
                {item}
              </span>
            ))}
            <div className="mx-1 h-4 w-px" style={{ background: PALETTE.border }} />
            <input
              value={query}
              onChange={event => setQuery(event.target.value)}
              placeholder="Search..."
              className="rounded-md px-3 py-2 text-[10px] outline-none"
              style={{ width: 190, background: PALETTE.card, border: `1px solid ${PALETTE.border}`, color: PALETTE.text }}
            />
          </div>
        </div>

        <div className="mb-5 flex gap-0 border-b" style={{ borderColor: PALETTE.border }}>
          {TAB_ORDER.map(tab => {
            const active = tab.key === activeTab
            const count = grouped[tab.key]?.length || 0
            return (
              <button
                key={tab.key}
                type="button"
                onClick={() => setActiveTab(tab.key)}
                className="mb-[-1px] inline-flex items-center gap-2 px-5 py-2.5 text-[10px] font-semibold"
                style={{
                  color: active ? tab.badgeColor : '#64748b',
                  borderBottom: active ? `2px solid ${tab.badgeColor}` : '2px solid transparent',
                  cursor: 'pointer',
                  background: 'transparent',
                }}
              >
                {tab.label}
                <span
                  className="rounded-full px-2 py-0.5 text-[9px]"
                  style={{
                    color: tab.badgeColor,
                    background: `${tab.badgeColor}18`,
                  }}
                >
                  {count}
                </span>
              </button>
            )
          })}
        </div>

        <div className="grid gap-4 xl:grid-cols-4 lg:grid-cols-3 md:grid-cols-2">
          {activeStrategies.map(strategy => (
            <StrategyCard key={strategy.id} strategy={strategy} onUse={handleUse} />
          ))}
        </div>

        {previewStrategies.length ? (
          <section className="mt-6 border-t pt-4" style={{ borderColor: PALETTE.border }}>
            <div className="mb-3 text-[10px] uppercase tracking-[0.08em]" style={{ color: '#64748b', fontWeight: 500 }}>
              {TAB_ORDER.find(tab => tab.key === previewTab)?.label} — {previewStrategies.length} strategies
            </div>
            <div className="grid gap-4 xl:grid-cols-4 lg:grid-cols-3 md:grid-cols-2">
              {previewStrategies.map(strategy => (
                <StrategyCard key={strategy.id} strategy={strategy} onUse={handleUse} dimmed />
              ))}
            </div>
          </section>
        ) : null}

        <div className="pt-8 text-center text-[10px]" style={{ color: '#475569' }}>
          For educational and backtesting purposes only · Not financial advice
        </div>
      </div>
    </div>
  )
}
