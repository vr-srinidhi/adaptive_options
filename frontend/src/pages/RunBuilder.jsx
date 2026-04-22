import { useEffect, useId, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { createWorkbenchRun, getTradingDays, getWorkbenchStrategies } from '../api'
import { fmtINR, fmtNumber, strategyStatusTone } from '../utils/workbench'

const RUN_STEPS = ['Strategies', 'Run Builder', 'Replay']
const ADVANCED_ONLY_FIELDS = new Set(['request_token', 'autorun'])

const SURFACE = {
  card: 'var(--surface-secondary)',
  bg: 'var(--surface)',
  border: 'var(--border)',
  text: 'var(--text-primary)',
  muted: 'var(--text-secondary)',
  blue: '#3b82f6',
  blueSoft: 'rgba(59,130,246,0.12)',
  green: '#22c55e',
  greenSoft: 'rgba(34,197,94,0.12)',
  amber: '#f59e0b',
  amberSoft: 'rgba(245,158,11,0.12)',
  red: '#ef4444',
  redSoft: 'rgba(239,68,68,0.12)',
}

const PAYOFF_SHAPES = {
  adaptive: '4,88 30,88 44,40 58,40 72,88 96,88',
  spread: '4,88 30,88 48,56 66,24 88,24 96,24',
  tent: '4,88 30,88 46,28 62,28 78,88 96,88',
  call: '4,88 42,88 58,66 74,24 96,24',
  put: '4,24 28,24 44,70 60,88 96,88',
}

const STRATEGY_VISUALS = {
  orb_intraday_spread: {
    badge: 'Opening Range Spread',
    assumption: 'Fills use candle close price and ranked ORB spread candidates. Bid/ask is not available in the current executor.',
    summaryTitle: 'Opening Range Spread',
    summaryCopy: 'Breakout confirms after the OR window. The executor selects the directional debit spread and replays the session minute by minute.',
    shape: 'adaptive',
    expiryLabel: 'Weekly (auto)',
    exitRule: 'Stop / Target / Time',
    constraintFields: [
      { label: 'Target %', value: '45', hint: 'of max profit' },
      { label: 'Stop %', value: '100', hint: 'of max loss' },
      { label: 'VIX Min', value: '14', hint: '' },
      { label: 'VIX Max', value: '22', hint: '' },
    ],
    legs: [
      { side: 'BUY', optionType: 'ENTRY', strike: 'ATM', expiry: 'Weekly', premium: 'auto' },
      { side: 'SELL', optionType: 'HEDGE', strike: 'ATM ± 200', expiry: 'Weekly', premium: 'auto' },
    ],
    payoffHint: 'Defined-risk breakout profile: capped downside, capped upside once the spread reaches max value.',
    metrics: { maxProfitRatio: 0.008, maxRiskRatio: 0.02, marginRatio: 0.12, maxLossText: 'Defined risk' },
  },
  short_strangle: {
    badge: 'Short Strangle',
    assumption: 'Fills use candle close price. Bid/ask not available. Results may overestimate entry quality.',
    summaryTitle: 'Short Strangle',
    summaryCopy: 'Sell OTM CE + OTM PE. Profit when spot stays within short strikes. Loss if spot moves sharply beyond either strike.',
    shape: 'tent',
    expiryLabel: 'Weekly (10 Apr)',
    exitRule: 'Stop / Target / Time',
    constraintFields: [
      { label: 'Target %', value: '45', hint: 'of max profit' },
      { label: 'Stop %', value: '100', hint: 'of max loss' },
      { label: 'VIX Min', value: '14', hint: '' },
      { label: 'VIX Max', value: '22', hint: '' },
    ],
    legs: [
      { side: 'SELL', optionType: 'CE', strike: 'ATM+200', expiry: 'Weekly', premium: 'auto' },
      { side: 'SELL', optionType: 'PE', strike: 'ATM-200', expiry: 'Weekly', premium: 'auto' },
    ],
    payoffHint: 'Tent-shaped expiry payoff: premium decay wins, large moves hurt.',
    metrics: { maxProfitRatio: 0.02, maxRiskRatio: 0.02, marginRatio: 0.28, maxLossText: 'Unlimited' },
  },
  buy_call: {
    badge: 'Buy Call',
    assumption: 'Single-leg debit setup. Preview shell matches the workbench, while execution remains pending.',
    summaryTitle: 'Buy Call',
    summaryCopy: 'Long bullish delta with fixed debit risk and convex upside exposure.',
    shape: 'call',
    expiryLabel: 'Weekly',
    exitRule: 'Stop / Target / Time',
    constraintFields: [
      { label: 'Target %', value: '60', hint: 'of max profit' },
      { label: 'Stop %', value: '100', hint: 'premium paid' },
      { label: 'VIX Min', value: '12', hint: '' },
      { label: 'VIX Max', value: '28', hint: '' },
    ],
    legs: [
      { side: 'BUY', optionType: 'CE', strike: 'ATM', expiry: 'Weekly', premium: 'auto' },
    ],
    payoffHint: 'Limited downside to premium paid with open-ended upside.',
    metrics: { maxProfitRatio: 0.018, maxRiskRatio: 0.01, marginRatio: 0.06, maxLossText: 'Premium paid' },
  },
  buy_put: {
    badge: 'Buy Put',
    assumption: 'Single-leg debit setup. Preview shell matches the workbench, while execution remains pending.',
    summaryTitle: 'Buy Put',
    summaryCopy: 'Long bearish delta with fixed debit risk and convex downside exposure.',
    shape: 'put',
    expiryLabel: 'Weekly',
    exitRule: 'Stop / Target / Time',
    constraintFields: [
      { label: 'Target %', value: '60', hint: 'of max profit' },
      { label: 'Stop %', value: '100', hint: 'premium paid' },
      { label: 'VIX Min', value: '12', hint: '' },
      { label: 'VIX Max', value: '28', hint: '' },
    ],
    legs: [
      { side: 'BUY', optionType: 'PE', strike: 'ATM', expiry: 'Weekly', premium: 'auto' },
    ],
    payoffHint: 'Limited downside to premium paid with accelerated profit on downside expansion.',
    metrics: { maxProfitRatio: 0.018, maxRiskRatio: 0.01, marginRatio: 0.06, maxLossText: 'Premium paid' },
  },
}

function defaultRunTypeFor(strategy) {
  return strategy?.modes?.[0] || 'paper_replay'
}

function normalizeConfig(strategy, runType) {
  return { ...(strategy?.defaults?.[runType] || {}) }
}

function getStatusTone(level) {
  const tones = {
    good: { icon: '✓', color: SURFACE.green, border: 'rgba(34,197,94,0.35)', background: SURFACE.greenSoft },
    warn: { icon: '!', color: SURFACE.amber, border: 'rgba(245,158,11,0.35)', background: SURFACE.amberSoft },
    info: { icon: '•', color: SURFACE.blue, border: 'rgba(59,130,246,0.35)', background: SURFACE.blueSoft },
  }
  return tones[level] || tones.info
}

function fallbackVisual(strategy) {
  return {
    badge: strategy?.name || 'Strategy',
    assumption: 'Preview-only strategy shell. Execution will follow once the matching executor ships.',
    summaryTitle: strategy?.name || 'Strategy',
    summaryCopy: strategy?.description || 'Choose a strategy to load its preview shell.',
    shape: 'spread',
    expiryLabel: 'Weekly',
    exitRule: 'Stop / Target / Time',
    constraintFields: [
      { label: 'Target %', value: 'Auto', hint: '' },
      { label: 'Stop %', value: 'Auto', hint: '' },
      { label: 'VIX Min', value: 'Auto', hint: '' },
      { label: 'VIX Max', value: 'Auto', hint: '' },
    ],
    legs: [
      { side: 'PLAN', optionType: 'UI', strike: 'Template', expiry: 'Pending', premium: 'auto' },
    ],
    payoffHint: 'Payoff preview will align once strategy-specific execution is added.',
    metrics: { maxProfitRatio: 0.005, maxRiskRatio: 0.02, marginRatio: 0.1, maxLossText: 'Strategy dependent' },
  }
}

function buildPreview(strategy, runType, config, readyDays) {
  const capital = Number(config.capital || 0)
  const capitalValid = Number.isFinite(capital) && capital > 0
  const visual = STRATEGY_VISUALS[strategy?.id] || fallbackVisual(strategy)
  const readySet = new Set((readyDays || []).map(item => item.trade_date))
  const selectedDate = config.date || config.start_date
  const exactDayReady = selectedDate ? readySet.has(selectedDate) : false
  const tone = strategyStatusTone(strategy?.status)

  const readinessItems = [
    {
      label: 'Spot candles',
      detail: runType === 'paper_replay'
        ? `${config.instrument || 'NIFTY'} 1-min · ${config.date || 'Pick a session date'}`
        : `${readyDays.length} warehouse-ready sessions`,
      level: runType === 'historical_backtest' || exactDayReady ? 'good' : 'warn',
    },
    {
      label: 'Option chain',
      detail: runType === 'paper_replay'
        ? visual.expiryLabel
        : `${config.start_date || 'Start'} → ${config.end_date || 'End'}`,
      level: 'good',
    },
    {
      label: 'VIX data',
      detail: `${visual.constraintFields[2]?.value || 'Auto'} to ${visual.constraintFields[3]?.value || 'Auto'} guardrail`,
      level: 'good',
    },
    {
      label: 'Charges model',
      detail: 'Zerodha brokerage preset',
      level: 'good',
    },
    {
      label: 'Bid/ask spread',
      detail: 'Fill proxy: candle close',
      level: 'warn',
    },
  ]

  const maxProfit = capitalValid ? capital * (visual.metrics?.maxProfitRatio || 0.005) : 0
  const maxRisk = capitalValid ? capital * (visual.metrics?.maxRiskRatio || 0.02) : 0
  const estMargin = capitalValid ? capital * (visual.metrics?.marginRatio || 0.1) : 0
  const validated = runType === 'historical_backtest'
    ? Boolean(config.start_date && config.end_date && config.instrument)
    : exactDayReady

  return {
    tone,
    visual,
    readinessItems,
    validated,
    metrics: {
      maxProfitValue: fmtINR(maxProfit),
      maxLossValue: visual.metrics?.maxLossText === 'Unlimited'
        ? 'Unlimited'
        : visual.metrics?.maxLossText && !capitalValid
          ? visual.metrics.maxLossText
          : capitalValid
            ? fmtINR(maxRisk)
            : visual.metrics?.maxLossText || 'Defined risk',
      marginValue: fmtINR(estMargin),
    },
  }
}

function orderFields(fields, runType) {
  const order = runType === 'paper_replay'
    ? ['instrument', 'date', 'capital', 'request_token']
    : ['instrument', 'start_date', 'end_date', 'name', 'capital', 'execution_order', 'autorun']

  return [...fields].sort((a, b) => {
    const aIndex = order.indexOf(a.key)
    const bIndex = order.indexOf(b.key)
    return (aIndex === -1 ? 999 : aIndex) - (bIndex === -1 ? 999 : bIndex)
  })
}

function optionTone(optionType) {
  if (optionType === 'PE') return { color: SURFACE.amber, border: 'rgba(245,158,11,0.35)', background: SURFACE.amberSoft }
  if (optionType === 'ENTRY' || optionType === 'CE') return { color: SURFACE.blue, border: 'rgba(59,130,246,0.35)', background: SURFACE.blueSoft }
  if (optionType === 'HEDGE') return { color: '#93c5fd', border: 'rgba(96,165,250,0.35)', background: 'rgba(96,165,250,0.12)' }
  return { color: SURFACE.muted, border: 'rgba(148,163,184,0.25)', background: 'rgba(148,163,184,0.08)' }
}

function sideTone(side) {
  if (side === 'SELL') return { color: SURFACE.red, border: 'rgba(239,68,68,0.35)', background: SURFACE.redSoft }
  if (side === 'BUY') return { color: SURFACE.green, border: 'rgba(34,197,94,0.35)', background: SURFACE.greenSoft }
  return { color: SURFACE.muted, border: 'rgba(148,163,184,0.25)', background: 'rgba(148,163,184,0.08)' }
}

function StrategyGlyph({ shape }) {
  const points = PAYOFF_SHAPES[shape] || PAYOFF_SHAPES.spread
  return (
    <svg width="30" height="18" viewBox="0 0 100 100" aria-hidden="true">
      <polyline
        points={points}
        fill="none"
        stroke={SURFACE.blue}
        strokeWidth="7"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}

function PayoffChart({ shape, compact = false }) {
  const gradientId = useId().replace(/:/g, '')
  const points = PAYOFF_SHAPES[shape] || PAYOFF_SHAPES.spread
  const viewHeight = compact ? 96 : 120
  const lineY = compact ? 74 : 80

  return (
    <svg viewBox={`0 0 100 ${viewHeight}`} width="100%" height="100%" preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="rgba(59,130,246,0.24)" />
          <stop offset="100%" stopColor="rgba(59,130,246,0.02)" />
        </linearGradient>
      </defs>
      <line x1="4" y1={lineY} x2="96" y2={lineY} stroke="rgba(148,163,184,0.16)" strokeWidth="0.6" />
      <polygon points={`4,${lineY} ${points} 96,${lineY}`} fill={`url(#${gradientId})`} />
      <polyline
        points={points}
        fill="none"
        stroke={SURFACE.blue}
        strokeWidth={compact ? '1.9' : '1.35'}
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  )
}

function StrategyPicker({ strategies, strategyId, onChange, visual }) {
  return (
    <div
      className="inline-flex items-center gap-2 rounded-md px-3 py-1.5"
      style={{ background: SURFACE.blueSoft, border: '1px solid rgba(59,130,246,0.28)' }}
    >
      <StrategyGlyph shape={visual.shape} />
      <div className="relative">
        <select
          aria-label="Strategy"
          value={strategyId}
          onChange={event => onChange(event.target.value)}
          className="appearance-none bg-transparent pr-5 text-[11px] font-semibold outline-none"
          style={{ color: '#60a5fa' }}
        >
          {strategies.map(item => (
            <option key={item.id} value={item.id} style={{ color: '#0f172a' }}>
              {item.name}
            </option>
          ))}
        </select>
        <span
          className="pointer-events-none absolute right-0 top-1/2 -translate-y-1/2 text-[10px]"
          style={{ color: '#60a5fa' }}
        >
          v
        </span>
      </div>
    </div>
  )
}

function ToggleGroup({ value, onChange, options }) {
  return (
    <div
      className="inline-flex rounded-md overflow-hidden"
      style={{ background: SURFACE.bg, border: `1px solid ${SURFACE.border}` }}
    >
      {options.map((option, index) => {
        const active = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            onClick={() => onChange(option.value)}
            className="px-3 py-1.5 text-[10px] font-semibold uppercase tracking-[0.08em]"
            style={{
              background: active ? 'var(--surface-tertiary)' : 'transparent',
              borderLeft: index === 0 ? 'none' : `1px solid ${SURFACE.border}`,
              color: active ? SURFACE.text : SURFACE.muted,
              cursor: 'pointer',
            }}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}

function InputShell({ children }) {
  return (
    <div
      className="rounded-[7px] px-3 py-2"
      style={{ background: SURFACE.bg, border: `1px solid ${SURFACE.border}`, minHeight: 40 }}
    >
      {children}
    </div>
  )
}

function FieldControl({ field, value, onChange }) {
  if (field.type === 'boolean') {
    return (
      <label className="flex items-center gap-3 text-[11px]" style={{ color: SURFACE.text }}>
        <input
          id={field.key}
          type="checkbox"
          checked={Boolean(value)}
          onChange={event => onChange(event.target.checked)}
        />
        <span>{field.label}</span>
      </label>
    )
  }

  if (field.type === 'select') {
    return (
      <div className="relative">
        <select
          id={field.key}
          value={value ?? ''}
          onChange={event => onChange(event.target.value)}
          className="w-full appearance-none bg-transparent pr-5 text-[11px] outline-none"
          style={{ color: SURFACE.text }}
        >
          {(field.options || []).map(option => (
            <option key={option} value={option} style={{ color: '#0f172a' }}>
              {option}
            </option>
          ))}
        </select>
        <span className="pointer-events-none absolute right-0 top-1/2 -translate-y-1/2 text-[10px]" style={{ color: SURFACE.muted }}>
          v
        </span>
      </div>
    )
  }

  return (
    <input
      id={field.key}
      type={field.type === 'number' ? 'number' : field.type === 'date' ? 'date' : 'text'}
      min={field.min}
      max={field.max}
      value={value ?? ''}
      onChange={event => onChange(event.target.value)}
      className="w-full bg-transparent text-[11px] outline-none"
      style={{ color: SURFACE.text }}
    />
  )
}

function BuilderField({ label, field, value, onChange, displayValue, hint, className = '' }) {
  const control = field
    ? <FieldControl field={field} value={value} onChange={onChange} />
    : <div className="text-[11px] leading-5" style={{ color: SURFACE.muted }}>{displayValue}</div>

  return (
    <div className={className}>
      <label
        className="block text-[10px] font-medium uppercase tracking-[0.09em] mb-1.5"
        htmlFor={field?.key}
        style={{ color: SURFACE.muted }}
      >
        {label}
      </label>
      <InputShell>{control}</InputShell>
      {hint ? (
        <div className="mt-1 text-[10px]" style={{ color: SURFACE.muted }}>
          {hint}
        </div>
      ) : null}
    </div>
  )
}

function StepCrumbs() {
  return (
    <div
      className="flex items-center gap-2 px-1 pb-3 text-[11px]"
      style={{ color: SURFACE.muted, borderBottom: `0.5px solid ${SURFACE.border}` }}
    >
      {RUN_STEPS.map((step, index) => {
        const active = index === 1
        const prior = index < 1
        return (
          <div key={step} className="flex items-center gap-2">
            <span style={{ color: active || prior ? SURFACE.blue : SURFACE.muted }}>{index + 1}.</span>
            <span style={{ color: active ? SURFACE.text : prior ? SURFACE.blue : SURFACE.muted, fontWeight: active ? 700 : 500 }}>
              {step}
            </span>
            {index < RUN_STEPS.length - 1 ? <span>›</span> : null}
          </div>
        )
      })}
    </div>
  )
}

function ReadinessRail({ preview, strategy }) {
  return (
    <aside className="space-y-4">
      <div className="rounded-[10px] p-5" style={{ background: SURFACE.card, border: `1px solid ${SURFACE.border}` }}>
        <div className="mb-4 text-[10px] font-medium uppercase tracking-[0.1em]" style={{ color: SURFACE.muted }}>
          Data Readiness
        </div>
        <div className="space-y-4">
          {preview.readinessItems.map(item => {
            const tone = getStatusTone(item.level)
            return (
              <div key={item.label} className="flex gap-3">
                <span
                  className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold"
                  style={{ color: tone.color, border: `1px solid ${tone.border}`, background: tone.background }}
                >
                  {tone.icon}
                </span>
                <div>
                  <div className="text-[12px] font-semibold" style={{ color: SURFACE.text }}>{item.label}</div>
                  <div className="mt-1 text-[11px] leading-5" style={{ color: SURFACE.muted }}>{item.detail}</div>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      <div className="rounded-[10px] p-4" style={{ background: SURFACE.amberSoft, border: '1px solid rgba(245,158,11,0.24)' }}>
        <div className="mb-2 text-[12px] font-semibold" style={{ color: SURFACE.amber }}>Assumption</div>
        <div className="text-[11px] leading-5" style={{ color: SURFACE.muted }}>{preview.visual.assumption}</div>
      </div>

      <div className="rounded-[10px] p-4" style={{ background: SURFACE.card, border: `1px solid ${SURFACE.border}` }}>
        <div className="mb-3 text-[10px] font-medium uppercase tracking-[0.1em]" style={{ color: SURFACE.muted }}>
          {preview.visual.summaryTitle}
        </div>
        <div
          className="rounded-[8px] px-2 pt-2"
          style={{ background: SURFACE.bg, border: `1px solid ${SURFACE.border}`, height: 112 }}
        >
          <PayoffChart shape={preview.visual.shape} compact />
        </div>
        <div className="mt-3 text-[11px] leading-5" style={{ color: SURFACE.muted }}>
          {preview.visual.summaryCopy}
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <span
            className="inline-flex items-center rounded px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.08em]"
            style={{
              color: preview.tone.color,
              border: `1px solid ${preview.tone.border}`,
              background: preview.tone.background,
            }}
          >
            {preview.tone.label}
          </span>
          {(strategy?.chips || []).slice(0, 2).map(chip => (
            <span key={chip} className="wb-chip">{chip}</span>
          ))}
        </div>
      </div>
    </aside>
  )
}

function SummaryCard({ canSubmit, submitting, runType, preview, onSubmit }) {
  const accessibleLabel = runType === 'paper_replay' ? 'Launch replay' : 'Create batch'
  return (
    <div
      className="rounded-[10px] p-4 flex flex-col gap-5"
      style={{ background: SURFACE.card, border: `1px solid ${SURFACE.border}`, minHeight: 100 }}
    >
      <div className="text-[10px] font-medium uppercase tracking-[0.1em]" style={{ color: SURFACE.muted }}>
        Risk / Reward
      </div>

      <div className="space-y-4">
        <div>
          <div className="text-[11px]" style={{ color: SURFACE.muted }}>Max Profit</div>
          <div className="mt-1 text-[1.05rem] leading-none font-bold" style={{ color: SURFACE.green }}>
            {preview.metrics.maxProfitValue}
          </div>
        </div>

        <div>
          <div className="text-[11px]" style={{ color: SURFACE.muted }}>Max Loss</div>
          <div className="mt-1 text-[1.05rem] leading-none font-bold" style={{ color: SURFACE.red }}>
            {preview.metrics.maxLossValue}
          </div>
        </div>

        <div>
          <div className="text-[11px]" style={{ color: SURFACE.muted }}>Est. Margin</div>
          <div className="mt-1 text-[13px] font-semibold" style={{ color: SURFACE.text }}>
            {preview.metrics.marginValue}
          </div>
        </div>
      </div>

      <div className="mt-auto space-y-3">
        <div
          className="rounded-md px-3 py-2 text-center text-[11px] font-semibold"
          style={{
            background: preview.validated ? 'var(--surface-tertiary)' : SURFACE.amberSoft,
            border: preview.validated ? `1px solid ${SURFACE.border}` : '1px solid rgba(245,158,11,0.28)',
            color: preview.validated ? SURFACE.text : '#fbbf24',
          }}
        >
          {preview.validated ? '✓ Data Validated' : 'Requires validated session date'}
        </div>

        <button
          type="submit"
          onClick={onSubmit}
          disabled={!canSubmit || submitting}
          aria-label={accessibleLabel}
          className="w-full rounded-md px-4 py-2.5 text-[12px] font-bold"
          style={{
            background: canSubmit ? '#2563eb' : 'var(--surface-tertiary)',
            color: canSubmit ? '#ffffff' : SURFACE.muted,
            cursor: canSubmit ? 'pointer' : 'not-allowed',
          }}
        >
          {submitting ? 'Submitting…' : 'Run →'}
        </button>
      </div>
    </div>
  )
}

function buildPrimaryLayout(runType, fieldMap, preview) {
  if (runType === 'historical_backtest') {
    return [
      { key: 'instrument', label: 'Instrument', field: fieldMap.instrument },
      { key: 'start_date', label: 'Start Date', field: fieldMap.start_date },
      { key: 'end_date', label: 'End Date', field: fieldMap.end_date },
      { key: 'name', label: 'Backtest Name', field: fieldMap.name },
      { key: 'capital', label: 'Capital (₹)', field: fieldMap.capital },
      { key: 'execution_order', label: 'Execution Order', field: fieldMap.execution_order },
    ]
  }

  return [
    { key: 'instrument', label: 'Instrument', field: fieldMap.instrument },
    { key: 'date', label: 'Date', field: fieldMap.date },
    { key: 'expiry', label: 'Expiry', displayValue: preview.visual.expiryLabel },
    { key: 'entry_time', label: 'Entry Time', displayValue: '09:30' },
    { key: 'capital', label: 'Capital (₹)', field: fieldMap.capital },
    { key: 'exit_rule', label: 'Exit Rule', displayValue: preview.visual.exitRule },
  ]
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

  const scopedFields = useMemo(() => {
    const schema = (strategy?.params_schema || []).filter(field => !field.modes || field.modes.includes(runType))
    return orderFields(schema, runType)
  }, [strategy, runType])

  const fieldMap = useMemo(
    () => Object.fromEntries(scopedFields.map(field => [field.key, field])),
    [scopedFields]
  )

  const preview = useMemo(
    () => buildPreview(strategy, runType, config, readyDays),
    [strategy, runType, config, readyDays]
  )

  const payloadPreview = useMemo(() => ({
    run_type: runType,
    strategy_id: strategyId,
    config,
  }), [runType, strategyId, config])

  const primaryLayout = useMemo(
    () => buildPrimaryLayout(runType, fieldMap, preview),
    [runType, fieldMap, preview]
  )

  const primaryKeys = useMemo(
    () => new Set(primaryLayout.map(item => item.field?.key).filter(Boolean)),
    [primaryLayout]
  )

  const secondaryFields = useMemo(() => {
    const visible = guidedMode ? scopedFields.filter(field => !ADVANCED_ONLY_FIELDS.has(field.key)) : scopedFields
    return visible.filter(field => !primaryKeys.has(field.key))
  }, [guidedMode, primaryKeys, scopedFields])

  const modeOptions = strategy?.modes || []
  const canSubmit = strategy?.status === 'available'

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

  const setField = (key, value) => setConfig(prev => ({ ...prev, [key]: value }))

  const handleSubmit = async event => {
    if (event) event.preventDefault()
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
      <div className="flex h-72 items-center justify-center gap-2" style={{ color: SURFACE.muted }}>
        <span className="spinner" /> Loading builder…
      </div>
    )
  }

  if (error && !strategy) {
    return (
      <div className="mx-auto max-w-6xl p-6">
        <div className="wb-alert-error">{error}</div>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-[1360px] px-4 pb-10 pt-4" style={{ fontSize: 12 }}>
      <StepCrumbs />

      <div className="mt-4 grid items-start gap-4 xl:grid-cols-[232px_minmax(0,1fr)]">
        <ReadinessRail preview={preview} strategy={strategy} />

        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-[1.15rem] font-bold leading-none" style={{ color: SURFACE.text }}>
              Run Builder
            </h1>
            <StrategyPicker
              strategies={strategies}
              strategyId={strategyId}
              onChange={handleStrategyChange}
              visual={preview.visual}
            />
            <div className="ml-auto flex flex-wrap items-center gap-2">
              {modeOptions.length > 1 ? (
                <ToggleGroup
                  value={runType}
                  onChange={handleRunTypeChange}
                  options={modeOptions.map(mode => ({
                    value: mode,
                    label: mode === 'paper_replay' ? 'Replay' : 'Historical',
                  }))}
                />
              ) : null}
              <ToggleGroup
                value={guidedMode ? 'guided' : 'advanced'}
                onChange={value => setGuidedMode(value === 'guided')}
                options={[
                  { value: 'guided', label: 'Guided' },
                  { value: 'advanced', label: 'Advanced' },
                ]}
              />
            </div>
          </div>

          <form onSubmit={handleSubmit} className="space-y-3">
            <div className="rounded-[10px] p-5" style={{ background: SURFACE.card, border: `1px solid ${SURFACE.border}` }}>
              <div className="grid gap-3 lg:grid-cols-3">
                {primaryLayout.map(item => (
                  <BuilderField
                    key={item.key}
                    label={item.label}
                    field={item.field}
                    value={item.field ? config[item.field.key] : undefined}
                    onChange={nextValue => item.field && setField(item.field.key, nextValue)}
                    displayValue={item.displayValue}
                  />
                ))}
              </div>

              <div className="my-4" style={{ borderTop: `0.5px solid ${SURFACE.border}` }} />

              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                {preview.visual.constraintFields.map(item => (
                  <BuilderField
                    key={item.label}
                    label={item.label}
                    displayValue={item.value}
                    hint={item.hint}
                  />
                ))}
              </div>

              {secondaryFields.length ? (
                <>
                  <div className="my-4" style={{ borderTop: `0.5px solid ${SURFACE.border}` }} />
                  <div className="mb-3 text-[11px] font-medium uppercase tracking-[0.1em]" style={{ color: SURFACE.muted }}>
                    {guidedMode ? 'Additional Fields' : 'Advanced Controls'}
                  </div>
                  <div className="grid gap-3 lg:grid-cols-3">
                    {secondaryFields.map(field => (
                      <BuilderField
                        key={field.key}
                        label={field.label}
                        field={field}
                        value={config[field.key]}
                        onChange={nextValue => setField(field.key, nextValue)}
                      />
                    ))}
                  </div>
                </>
              ) : null}

              <div className="my-4" style={{ borderTop: `0.5px solid ${SURFACE.border}` }} />

              <div>
                <div className="mb-3 text-[10px] font-medium uppercase tracking-[0.1em]" style={{ color: SURFACE.muted }}>
                  Legs
                </div>
                <div className="space-y-2">
                  {preview.visual.legs.map(leg => {
                    const side = sideTone(leg.side)
                    const option = optionTone(leg.optionType)
                    return (
                      <div
                        key={`${leg.side}-${leg.optionType}-${leg.strike}`}
                        className="flex flex-wrap items-center gap-2 rounded-[8px] px-3 py-3"
                        style={{ background: SURFACE.bg, border: `1px solid ${SURFACE.border}` }}
                      >
                        <span
                          className="rounded px-2 py-1 text-[11px] font-semibold uppercase tracking-[0.08em]"
                          style={{ color: side.color, border: `1px solid ${side.border}`, background: side.background }}
                        >
                          {leg.side}
                        </span>
                        <span
                          className="rounded px-2 py-1 text-[11px] font-semibold uppercase tracking-[0.08em]"
                          style={{ color: option.color, border: `1px solid ${option.border}`, background: option.background }}
                        >
                          {leg.optionType}
                        </span>
                        <span className="text-[12px] font-semibold" style={{ color: SURFACE.text }}>
                          Strike: {leg.strike}
                        </span>
                        <span className="text-[11px]" style={{ color: SURFACE.muted }}>
                          Expiry: {leg.expiry}
                        </span>
                        <span className="ml-auto text-[11px]" style={{ color: SURFACE.muted }}>
                          Premium: {leg.premium}
                        </span>
                      </div>
                    )
                  })}
                </div>
              </div>
            </div>

            {error ? <div className="wb-alert-error">{error}</div> : null}
            {!canSubmit ? (
              <div className="wb-alert-warning">
                This strategy is catalogued in the workbench, but the backend executor is not live yet. The shell is accurate, submission stays disabled.
              </div>
            ) : null}

            <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_260px]">
              <div className="rounded-[10px] p-4" style={{ background: SURFACE.card, border: `1px solid ${SURFACE.border}` }}>
                <div className="mb-3 text-[10px] font-medium uppercase tracking-[0.1em]" style={{ color: SURFACE.muted }}>
                  Payoff at Expiry
                </div>
                <div style={{ height: 208 }}>
                  <PayoffChart shape={preview.visual.shape} />
                </div>
                <div className="mt-2 flex items-center justify-between text-[11px]" style={{ color: SURFACE.muted }}>
                  <span>← Bearish move</span>
                  <span style={{ color: SURFACE.green }}>Profit zone</span>
                  <span>Bullish move →</span>
                </div>
                <div className="mt-3 text-[11px] leading-5" style={{ color: SURFACE.muted }}>
                  {preview.visual.payoffHint}
                </div>
              </div>

              <SummaryCard
                canSubmit={canSubmit}
                submitting={submitting}
                runType={runType}
                preview={preview}
                onSubmit={handleSubmit}
              />
            </div>

            {!guidedMode ? (
              <div className="rounded-[10px] p-5" style={{ background: SURFACE.card, border: `1px solid ${SURFACE.border}` }}>
                <div className="mb-3 text-[11px] font-medium uppercase tracking-[0.1em]" style={{ color: SURFACE.muted }}>
                  Payload Preview
                </div>
                <pre
                  className="overflow-auto rounded-[8px] p-4 text-xs"
                  style={{ background: SURFACE.bg, border: `1px solid ${SURFACE.border}`, color: '#cbd5e1' }}
                >
                  {JSON.stringify(payloadPreview, null, 2)}
                </pre>
              </div>
            ) : null}

            <div className="pt-2 text-center text-xs" style={{ color: '#475569' }}>
              For educational and backtesting purposes only · Not financial advice
            </div>
          </form>
        </div>
      </div>
    </div>
  )
}
