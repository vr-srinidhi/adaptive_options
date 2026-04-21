export const fmtINR = value => {
  if (value == null || Number.isNaN(Number(value))) return '—'
  return new Intl.NumberFormat('en-IN', {
    style: 'currency',
    currency: 'INR',
    maximumFractionDigits: 0,
  }).format(Number(value))
}

export const fmtNumber = (value, digits = 0) => {
  if (value == null || Number.isNaN(Number(value))) return '—'
  return Number(value).toLocaleString('en-IN', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export const fmtDateTime = value => {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

export const fmtShortDate = value => {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString('en-IN', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })
}

export const groupStrategiesByBias = (strategies = []) => {
  const order = ['adaptive', 'bullish', 'bearish', 'neutral']
  const groups = order.map(key => ({
    key,
    label: key === 'adaptive' ? 'Adaptive' : key.charAt(0).toUpperCase() + key.slice(1),
    items: strategies.filter(item => item.bias === key),
  }))
  const remainder = strategies.filter(item => !order.includes(item.bias))
  if (remainder.length) {
    groups.push({ key: 'other', label: 'Other', items: remainder })
  }
  return groups.filter(group => group.items.length > 0)
}

export const runKindLabel = kind => ({
  paper_session: 'Paper Replay',
  historical_batch: 'Historical Batch',
  historical_session: 'Historical Session',
}[kind] || kind)

export const strategyStatusTone = status => ({
  available: { background: 'rgba(54,179,126,0.14)', border: 'rgba(54,179,126,0.35)', color: '#36b37e', label: 'Live' },
  planned: { background: 'rgba(255,196,0,0.12)', border: 'rgba(255,196,0,0.35)', color: '#ffc400', label: 'Planned' },
  research: { background: 'rgba(0,184,217,0.12)', border: 'rgba(0,184,217,0.35)', color: '#00b8d9', label: 'Research' },
}[status] || { background: 'rgba(148,163,184,0.12)', border: 'rgba(148,163,184,0.25)', color: '#94a3b8', label: status || 'Unknown' })

export const runStatusTone = status => {
  const tones = {
    COMPLETED: { background: 'rgba(54,179,126,0.14)', border: 'rgba(54,179,126,0.35)', color: '#36b37e' },
    completed: { background: 'rgba(54,179,126,0.14)', border: 'rgba(54,179,126,0.35)', color: '#36b37e' },
    RUNNING: { background: 'rgba(0,184,217,0.12)', border: 'rgba(0,184,217,0.35)', color: '#00b8d9' },
    running: { background: 'rgba(0,184,217,0.12)', border: 'rgba(0,184,217,0.35)', color: '#00b8d9' },
    queued: { background: 'rgba(94,108,132,0.14)', border: 'rgba(94,108,132,0.35)', color: '#94a3b8' },
    draft: { background: 'rgba(94,108,132,0.14)', border: 'rgba(94,108,132,0.35)', color: '#94a3b8' },
    ERROR: { background: 'rgba(255,86,48,0.12)', border: 'rgba(255,86,48,0.35)', color: '#ff5630' },
    failed: { background: 'rgba(255,86,48,0.12)', border: 'rgba(255,86,48,0.35)', color: '#ff5630' },
    completed_with_warnings: { background: 'rgba(255,196,0,0.12)', border: 'rgba(255,196,0,0.35)', color: '#ffc400' },
  }
  return tones[status] || tones.draft
}

export const todayISO = () => new Date().toISOString().split('T')[0]
