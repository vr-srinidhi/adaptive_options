const REGIME_STYLES = {
  BULLISH:  { bg: 'rgba(34,197,94,0.15)',   border: 'rgba(34,197,94,0.4)',   text: '#22c55e' },
  BEARISH:  { bg: 'rgba(239,68,68,0.15)',   border: 'rgba(239,68,68,0.4)',   text: '#ef4444' },
  NEUTRAL:  { bg: 'rgba(245,158,11,0.15)',  border: 'rgba(245,158,11,0.4)',  text: '#f59e0b' },
  NO_TRADE: { bg: 'rgba(100,116,139,0.15)', border: 'rgba(100,116,139,0.4)', text: '#64748b' },
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
