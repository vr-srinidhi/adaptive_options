import { BRAND_LOGO_PATH, BRAND_NAME, PAPER_SESSION_REPORT_NAME } from '../constants/brand'

export const fmtINR = v => v == null ? '—'
  : new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 }).format(v)

export const fmtNum = (v, dp = 2) =>
  v == null ? '—' : v.toLocaleString('en-IN', { minimumFractionDigits: dp, maximumFractionDigits: dp })

export const fmtDate = d => {
  if (!d) return '—'
  const dt = new Date(d)
  return dt.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' })
}

export function extractSelectionAudit(decisions, trade) {
  const decisionWithRanking =
    decisions.find(d => d.action === 'ENTER' && d.candidate_ranking_json) ||
    [...decisions].reverse().find(d => d.candidate_ranking_json)

  if (!decisionWithRanking?.candidate_ranking_json) return null

  const ranking = decisionWithRanking.candidate_ranking_json
  const rankedCandidates = (ranking.candidates || [])
    .filter(c => c.rank != null)
    .sort((a, b) => a.rank - b.rank)

  const selected = rankedCandidates.find(c => c.rank === 1) || rankedCandidates[0] || null
  const chosenSpread = trade
    ? `${trade.long_strike}/${trade.short_strike} ${trade.option_type}`
    : selected
      ? `${selected.long_strike}/${selected.short_strike} ${selected.option_type || selected.opt_type}`
      : '—'

  return {
    selectionMethod: trade?.selection_method || ranking.selection_method || '—',
    signalDirection: trade?.bias || ranking.signal_direction || selected?.direction || '—',
    evaluatedCount: ranking.evaluated_candidates ?? ranking.evaluatedCount ?? 0,
    validCount: ranking.valid_candidates ?? ranking.validCount ?? 0,
    chosenSpread,
    chosenRank: trade?.selected_candidate_rank ?? ranking.selected_candidate_rank ?? selected?.rank ?? null,
    chosenScore: trade?.selected_candidate_score ?? ranking.selected_candidate_score ?? selected?.score ?? null,
    topCandidates: rankedCandidates.slice(0, 3),
  }
}

function csvEscape(value) {
  if (value == null) return ''
  const stringValue = String(value)
  if (/[",\n]/.test(stringValue)) {
    return `"${stringValue.replace(/"/g, '""')}"`
  }
  return stringValue
}

function rowsToCSV(rows) {
  return rows.map(row => row.map(csvEscape).join(',')).join('\n')
}

function getLogoUrl() {
  return typeof window !== 'undefined'
    ? `${window.location.origin}${BRAND_LOGO_PATH}`
    : BRAND_LOGO_PATH
}

export function buildPaperSessionCSV(session, trade, decisions, marks, candleSeries) {
  const rows = []
  const selectionAudit = extractSelectionAudit(decisions, trade)

  rows.push(['REPORT BRANDING'])
  rows.push(['Brand', BRAND_NAME])
  rows.push(['Report', PAPER_SESSION_REPORT_NAME])
  rows.push(['Logo Asset', getLogoUrl()])
  rows.push(['Generated At', new Date().toISOString()])
  rows.push([])

  rows.push(['SESSION SUMMARY'])
  rows.push(['Date', session.session_date])
  rows.push(['Instrument', session.instrument])
  rows.push(['Capital', session.capital])
  rows.push(['Status', session.status])
  rows.push(['Minutes Audited', session.decision_count])
  rows.push([])

  if (trade) {
    rows.push(['TRADE DETAIL'])
    rows.push(['Bias', trade.bias])
    rows.push(['Option Type', trade.option_type])
    rows.push(['Long Strike', trade.long_strike])
    rows.push(['Short Strike', trade.short_strike])
    rows.push(['Expiry', trade.expiry])
    rows.push(['Lot Size', trade.lot_size])
    rows.push(['Approved Lots', trade.approved_lots])
    rows.push(['Total Qty', trade.lot_size * trade.approved_lots])
    rows.push(['Entry Debit / unit', trade.entry_debit])
    rows.push(['Entry Time', trade.entry_time?.slice(0, 16)])
    rows.push(['Exit Time', trade.exit_time?.slice(0, 16)])
    rows.push(['Exit Reason', trade.exit_reason])
    rows.push(['Max Loss', trade.total_max_loss])
    rows.push(['Target', trade.target_profit])
    rows.push(['Gross P&L', trade.realized_gross_pnl])
    rows.push(['Net P&L', trade.realized_net_pnl])
    rows.push([])

    if (trade.legs?.length) {
      rows.push(['LEGS'])
      rows.push(['Side', 'Contract', 'Lots', 'Lot Size', 'Total Qty', 'Entry Price', 'Exit Price'])
      trade.legs.forEach(l => {
        rows.push([
          l.leg_side === 'LONG' ? 'BUY' : 'SELL',
          `${session.instrument} ${l.strike} ${l.option_type} ${l.expiry}`,
          trade.approved_lots,
          trade.lot_size,
          trade.approved_lots * trade.lot_size,
          l.entry_price ?? '',
          l.exit_price ?? '',
        ])
      })
      rows.push([])
    }
  }

  if (selectionAudit) {
    rows.push(['SPREAD SELECTION'])
    rows.push(['Signal Direction', selectionAudit.signalDirection])
    rows.push(['Selection Method', selectionAudit.selectionMethod])
    rows.push(['Candidates Evaluated', selectionAudit.evaluatedCount])
    rows.push(['Valid Candidates', selectionAudit.validCount])
    rows.push(['Chosen Spread', selectionAudit.chosenSpread])
    rows.push(['Chosen Rank', selectionAudit.chosenRank ?? ''])
    rows.push(['Chosen Score', selectionAudit.chosenScore ?? ''])
    rows.push([])

    if (selectionAudit.topCandidates.length) {
      rows.push(['TOP 3 CANDIDATES'])
      rows.push(['Rank', 'Spread', 'Debit', 'Max Loss', 'Max Gain', 'Volume', 'OI', 'Score', 'Status'])
      selectionAudit.topCandidates.forEach(c => {
        rows.push([
          c.rank,
          `${c.long_strike}/${c.short_strike} ${c.option_type || c.opt_type}`,
          c.spread_debit,
          c.total_max_loss,
          c.max_gain_total,
          c.combined_volume,
          c.combined_oi,
          c.score,
          c.status,
        ])
      })
      rows.push([])
    }
  }

  rows.push(['MINUTE AUDIT LOG'])
  rows.push(['Time', 'Spot Close', 'OR High', 'OR Low', 'State', 'Action', 'Reason Code', 'Reason Text',
    'Max Loss', 'Target/MTM', 'Candidate Structure'])
  decisions.forEach(d => {
    const mark = marks.find(m => m.timestamp === d.timestamp)
    const mtm = mark?.total_mtm ?? ''
    rows.push([
      d.timestamp?.slice(11, 16),
      d.spot_close,
      d.opening_range_high,
      d.opening_range_low,
      d.trade_state === 'OPEN_TRADE' ? 'OPEN' : 'WATCH',
      d.action,
      d.reason_code,
      d.reason_text || '',
      d.computed_max_loss ?? '',
      mtm !== '' ? mtm : (d.computed_target ?? ''),
      d.candidate_structure ? JSON.stringify(d.candidate_structure) : '',
    ])
  })
  rows.push([])

  if (candleSeries?.length) {
    candleSeries.forEach(cs => {
      rows.push([`CANDLES: ${cs.series_type}`])
      rows.push(['Time', 'Open', 'High', 'Low', 'Close', 'Volume'])
      ;(cs.candles || []).forEach(c => {
        rows.push([c.time?.slice(11, 16), c.open, c.high, c.low, c.close, c.volume])
      })
      rows.push([])
    })
  }

  return rowsToCSV(rows)
}

export function buildPaperSessionsSummaryCSV(bundles) {
  const rows = []

  rows.push(['REPORT BRANDING'])
  rows.push(['Brand', BRAND_NAME])
  rows.push(['Report', 'Paper Sessions Bulk Export'])
  rows.push(['Logo Asset', getLogoUrl()])
  rows.push(['Generated At', new Date().toISOString()])
  rows.push(['Session Count', bundles.length])
  rows.push([])

  rows.push(['SESSIONS'])
  rows.push(['Session ID', 'Date', 'Instrument', 'Capital', 'Status', 'Decisions', 'Final State', 'Gross P&L', 'Net P&L'])
  bundles.forEach(bundle => {
    const { session, trade } = bundle
    rows.push([
      session.id,
      session.session_date,
      session.instrument,
      session.capital,
      session.status,
      session.decision_count,
      session.final_session_state ?? '',
      trade?.realized_gross_pnl ?? '',
      trade?.realized_net_pnl ?? session.summary_pnl ?? '',
    ])
  })

  return rowsToCSV(rows)
}

export function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}
