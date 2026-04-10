const REGIME_STYLES = {
  BULLISH:  { bg: 'rgba(34,197,94,0.15)',   border: 'rgba(34,197,94,0.4)',   text: '#22c55e' },
  BEARISH:  { bg: 'rgba(239,68,68,0.15)',   border: 'rgba(239,68,68,0.4)',   text: '#ef4444' },
  NEUTRAL:  { bg: 'rgba(245,158,11,0.15)',  border: 'rgba(245,158,11,0.4)',  text: '#f59e0b' },
  NO_TRADE: { bg: 'rgba(100,116,139,0.15)', border: 'rgba(100,116,139,0.4)', text: '#64748b' },
}

const REGIME_DETAIL_STYLES = {
  BREAKOUT_UP:         { bg: 'rgba(34,197,94,0.15)',   border: 'rgba(34,197,94,0.4)',   text: '#22c55e' },
  TRENDING_UP:         { bg: 'rgba(74,222,128,0.15)',  border: 'rgba(74,222,128,0.4)',  text: '#4ade80' },
  BOTTOMING:           { bg: 'rgba(16,185,129,0.15)',  border: 'rgba(16,185,129,0.4)',  text: '#10b981' },
  OVERSOLD_REVERSAL:   { bg: 'rgba(20,184,166,0.15)',  border: 'rgba(20,184,166,0.4)',  text: '#14b8a6' },
  BREAKOUT_DOWN:       { bg: 'rgba(239,68,68,0.15)',   border: 'rgba(239,68,68,0.4)',   text: '#ef4444' },
  TRENDING_DOWN:       { bg: 'rgba(248,113,113,0.15)', border: 'rgba(248,113,113,0.4)', text: '#f87171' },
  PANIC_SELL:          { bg: 'rgba(220,38,38,0.2)',    border: 'rgba(220,38,38,0.5)',   text: '#dc2626' },
  OVERBOUGHT_REVERSAL: { bg: 'rgba(249,115,22,0.15)',  border: 'rgba(249,115,22,0.4)',  text: '#f97316' },
  CONSOLIDATION:       { bg: 'rgba(245,158,11,0.15)',  border: 'rgba(245,158,11,0.4)',  text: '#f59e0b' },
  CHOPPY:              { bg: 'rgba(100,116,139,0.15)', border: 'rgba(100,116,139,0.4)', text: '#64748b' },
  NEUTRAL:             { bg: 'rgba(100,116,139,0.15)', border: 'rgba(100,116,139,0.4)', text: '#64748b' },
  INITIALIZING:        { bg: 'rgba(100,116,139,0.15)', border: 'rgba(100,116,139,0.4)', text: '#64748b' },
}

const SIGNAL_STYLES = {
  BREAKOUT_LONG:             { bg: 'rgba(34,197,94,0.12)',  border: 'rgba(34,197,94,0.35)',  text: '#22c55e' },
  TREND_CONTINUATION_LONG:   { bg: 'rgba(74,222,128,0.12)', border: 'rgba(74,222,128,0.35)', text: '#4ade80' },
  REVERSAL_LONG:             { bg: 'rgba(16,185,129,0.12)', border: 'rgba(16,185,129,0.35)', text: '#10b981' },
  BREAKOUT_SHORT:            { bg: 'rgba(239,68,68,0.12)',  border: 'rgba(239,68,68,0.35)',  text: '#ef4444' },
  TREND_CONTINUATION_SHORT:  { bg: 'rgba(248,113,113,0.12)',border: 'rgba(248,113,113,0.35)',text: '#f87171' },
  REVERSAL_SHORT:            { bg: 'rgba(220,38,38,0.12)',  border: 'rgba(220,38,38,0.35)',  text: '#dc2626' },
  PREMIUM_SELL_BULLISH:      { bg: 'rgba(59,130,246,0.12)', border: 'rgba(59,130,246,0.35)', text: '#3b82f6' },
  PREMIUM_SELL_BEARISH:      { bg: 'rgba(139,92,246,0.12)', border: 'rgba(139,92,246,0.35)', text: '#8b5cf6' },
  PREMIUM_SELL_RANGE:        { bg: 'rgba(245,158,11,0.12)', border: 'rgba(245,158,11,0.35)', text: '#f59e0b' },
  NO_SIGNAL:                 { bg: 'rgba(100,116,139,0.12)',border: 'rgba(100,116,139,0.35)',text: '#64748b' },
}

const WL_STYLES = {
  WIN:        { bg: 'rgba(34,197,94,0.15)',   border: 'rgba(34,197,94,0.4)',   text: '#22c55e' },
  LOSS:       { bg: 'rgba(239,68,68,0.15)',   border: 'rgba(239,68,68,0.4)',   text: '#ef4444' },
  BREAK_EVEN: { bg: 'rgba(245,158,11,0.15)',  border: 'rgba(245,158,11,0.4)',  text: '#f59e0b' },
  NO_TRADE:   { bg: 'rgba(100,116,139,0.15)', border: 'rgba(100,116,139,0.4)', text: '#64748b' },
}

export function RegimeBadge({ regime }) {
  const s = REGIME_STYLES[regime] || REGIME_STYLES.NEUTRAL
  return (
    <span className="px-2 py-0.5 rounded text-xs font-semibold"
      style={{ background: s.bg, border: `1px solid ${s.border}`, color: s.text }}>
      {regime}
    </span>
  )
}

export function RegimeDetailBadge({ regime }) {
  if (!regime) return null
  const s = REGIME_DETAIL_STYLES[regime] || REGIME_DETAIL_STYLES.NEUTRAL
  return (
    <span className="px-2 py-0.5 rounded text-xs font-semibold"
      style={{ background: s.bg, border: `1px solid ${s.border}`, color: s.text }}>
      {regime.replace(/_/g, ' ')}
    </span>
  )
}

export function SignalBadge({ signal }) {
  if (!signal || signal === 'NO_SIGNAL') return (
    <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>—</span>
  )
  const s = SIGNAL_STYLES[signal] || SIGNAL_STYLES.NO_SIGNAL
  const label = signal.replace(/_/g, ' ').replace('LONG', 'L').replace('SHORT', 'S')
  return (
    <span className="px-2 py-0.5 rounded text-xs font-semibold"
      style={{ background: s.bg, border: `1px solid ${s.border}`, color: s.text }}>
      {label}
    </span>
  )
}

export function ScoreBadge({ score }) {
  if (score == null) return null
  const color = score >= 80 ? '#22c55e' : score >= 65 ? '#f59e0b' : '#64748b'
  return (
    <span className="text-xs font-bold" style={{ color }}>{score}</span>
  )
}

export function WLBadge({ wl }) {
  const s = WL_STYLES[wl] || WL_STYLES.NO_TRADE
  return (
    <span className="px-2 py-0.5 rounded text-xs font-semibold"
      style={{ background: s.bg, border: `1px solid ${s.border}`, color: s.text }}>
      {wl === 'BREAK_EVEN' ? 'EVEN' : wl}
    </span>
  )
}

export function ActionBadge({ act }) {
  const isSell = act === 'SELL'
  return (
    <span className="px-2 py-0.5 rounded text-xs font-semibold"
      style={{
        background: isSell ? 'rgba(239,68,68,0.15)' : 'rgba(34,197,94,0.15)',
        border: isSell ? '1px solid rgba(239,68,68,0.4)' : '1px solid rgba(34,197,94,0.4)',
        color: isSell ? '#ef4444' : '#22c55e',
      }}>
      {act}
    </span>
  )
}

export function TypeBadge({ typ }) {
  const isCE = typ === 'CE'
  return (
    <span className="px-2 py-0.5 rounded text-xs font-semibold"
      style={{
        background: isCE ? 'rgba(59,130,246,0.15)' : 'rgba(245,158,11,0.15)',
        border: isCE ? '1px solid rgba(59,130,246,0.4)' : '1px solid rgba(245,158,11,0.4)',
        color: isCE ? '#3b82f6' : '#f59e0b',
      }}>
      {typ}
    </span>
  )
}
