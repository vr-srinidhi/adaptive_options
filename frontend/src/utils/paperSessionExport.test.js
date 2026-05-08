import { describe, expect, it, vi } from 'vitest'
import {
  buildPaperSessionCSV,
  buildPaperSessionsSummaryCSV,
  extractSelectionAudit,
  fmtDate,
  fmtINR,
  fmtNum,
} from './paperSessionExport'

describe('paperSessionExport formatters', () => {
  it('formats missing and numeric values for reports', () => {
    expect(fmtINR(null)).toBe('—')
    expect(fmtINR(125000)).toBe('₹1,25,000')
    expect(fmtNum(undefined)).toBe('—')
    expect(fmtNum(1234.5, 1)).toBe('1,234.5')
    expect(fmtDate(null)).toBe('—')
    expect(fmtDate('2026-05-06')).toMatch(/2026/)
  })
})

describe('extractSelectionAudit', () => {
  it('prefers selected trade fields and sorts top candidates by rank', () => {
    const trade = {
      selection_method: 'score',
      bias: 'BULLISH',
      long_strike: 22400,
      short_strike: 22500,
      option_type: 'CE',
      selected_candidate_rank: 1,
      selected_candidate_score: 98,
    }
    const audit = extractSelectionAudit([
      {
        action: 'ENTER',
        candidate_ranking_json: {
          evaluated_candidates: 4,
          valid_candidates: 2,
          candidates: [
            { rank: 2, long_strike: 22300, short_strike: 22400, option_type: 'CE', score: 80 },
            { rank: 1, long_strike: 22400, short_strike: 22500, option_type: 'CE', score: 98 },
          ],
        },
      },
    ], trade)

    expect(audit.chosenSpread).toBe('22400/22500 CE')
    expect(audit.chosenRank).toBe(1)
    expect(audit.topCandidates.map(c => c.rank)).toEqual([1, 2])
  })

  it('returns null when no ranking data is present', () => {
    expect(extractSelectionAudit([{ action: 'HOLD' }], null)).toBeNull()
  })
})

describe('buildPaperSessionCSV', () => {
  it('exports session, trade, selection, decisions, and candles as CSV', () => {
    vi.setSystemTime(new Date('2026-05-06T12:00:00.000Z'))
    const csv = buildPaperSessionCSV(
      {
        session_date: '2026-05-06',
        instrument: 'NIFTY',
        capital: 250000,
        status: 'completed',
        decision_count: 1,
      },
      {
        bias: 'BULLISH',
        option_type: 'CE',
        long_strike: 22400,
        short_strike: 22500,
        expiry: '2026-05-12',
        lot_size: 75,
        approved_lots: 2,
        entry_debit: 12.5,
        entry_time: '2026-05-06T09:30:00',
        exit_time: '2026-05-06T15:20:00',
        exit_reason: 'TIME_EXIT',
        total_max_loss: 10000,
        target_profit: 4000,
        realized_gross_pnl: 5000,
        realized_net_pnl: 4500,
        legs: [{ leg_side: 'LONG', strike: 22400, option_type: 'CE', expiry: '2026-05-12', entry_price: 10, exit_price: 20 }],
      },
      [{
        timestamp: '2026-05-06T09:30:00',
        action: 'ENTER',
        reason_code: 'ENTRY',
        candidate_ranking_json: {
          candidates: [{ rank: 1, long_strike: 22400, short_strike: 22500, option_type: 'CE', score: 99 }],
        },
      }],
      [{ timestamp: '2026-05-06T09:30:00', total_mtm: 1500 }],
      [{ series_type: 'spot', candles: [{ time: '2026-05-06T09:30:00', open: 1, high: 2, low: 1, close: 2, volume: 10 }] }]
    )

    expect(csv).toContain('SESSION SUMMARY')
    expect(csv).toContain('TRADE DETAIL')
    expect(csv).toContain('SPREAD SELECTION')
    expect(csv).toContain('CANDLES: spot')
    expect(csv).toContain('NIFTY 22400 CE 2026-05-12')
  })

  it('escapes commas, quotes, and newlines in CSV values', () => {
    const csv = buildPaperSessionCSV(
      { session_date: '2026-05-06', instrument: 'NIFTY', capital: 1, status: 'completed', decision_count: 1 },
      null,
      [{ timestamp: '2026-05-06T09:30:00', action: 'HOLD', reason_code: 'WAIT', reason_text: 'quoted "text", with comma\nand line' }],
      [],
      []
    )

    expect(csv).toContain('"quoted ""text"", with comma\nand line"')
  })
})

describe('buildPaperSessionsSummaryCSV', () => {
  it('exports summary rows with trade pnl fallback to session pnl', () => {
    const csv = buildPaperSessionsSummaryCSV([
      {
        session: {
          id: 's1',
          session_date: '2026-05-06',
          instrument: 'NIFTY',
          capital: 250000,
          status: 'completed',
          decision_count: 10,
          final_session_state: 'CLOSED',
          summary_pnl: 1234,
        },
        trade: null,
      },
    ])

    expect(csv).toContain('Session Count,1')
    expect(csv).toContain('s1,2026-05-06,NIFTY,250000,completed,10,CLOSED,,1234')
  })
})
