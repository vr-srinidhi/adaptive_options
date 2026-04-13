import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  getPaperSession: vi.fn(),
  getPaperDecisions: vi.fn(),
  getPaperTrade: vi.fn(),
  getPaperMarks: vi.fn(),
  getPaperCandles: vi.fn(),
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
    useParams: () => ({ id: 'session-1' }),
  }
})

vi.mock('../api', () => ({
  getPaperSession: mocks.getPaperSession,
  getPaperDecisions: mocks.getPaperDecisions,
  getPaperTrade: mocks.getPaperTrade,
  getPaperMarks: mocks.getPaperMarks,
  getPaperCandles: mocks.getPaperCandles,
}))

vi.mock('../components/PnlChart', () => ({
  PnlProgressionChart: ({ data }) => <div data-testid="pnl-chart">Points: {data.length}</div>,
}))

import PaperTradeBook from './PaperTradeBook'

const baseSession = {
  id: 'session-1',
  session_date: '2026-04-11',
  instrument: 'NIFTY',
  capital: 2500000,
  status: 'COMPLETED',
  decision_count: 20,
  action_summary: { ENTER: 1, HOLD: 2, EXIT_TARGET: 1 },
}

const rankedCandidates = [
  {
    rank: 1,
    long_strike: 22100,
    short_strike: 22150,
    option_type: 'CE',
    spread_debit: 28.3,
    total_max_loss: 46695,
    max_gain_total: 35955,
    combined_volume: 122000,
    combined_oi: 1200000,
    score: 0.8421,
  },
  {
    rank: 2,
    long_strike: 22050,
    short_strike: 22100,
    option_type: 'CE',
    spread_debit: 31.2,
    total_max_loss: 49920,
    max_gain_total: 29750,
    combined_volume: 140000,
    combined_oi: 1600000,
    score: 0.791,
  },
]

const baseTrade = {
  id: 'trade-1',
  bias: 'BULLISH',
  option_type: 'CE',
  long_strike: 22100,
  short_strike: 22150,
  expiry: '2026-04-16',
  lot_size: 75,
  approved_lots: 22,
  entry_debit: 28.3,
  total_max_loss: 46695,
  target_profit: 12500,
  realized_gross_pnl: 24750,
  realized_net_pnl: 24400,
  exit_reason: 'EXIT_TARGET',
  entry_time: '2026-04-11T09:31:00',
  exit_time: '2026-04-11T09:33:00',
  selection_method: 'ranked_candidate_selection_v1',
  selected_candidate_rank: 1,
  selected_candidate_score: 0.8421,
  legs: [
    { leg_side: 'LONG', option_type: 'CE', strike: 22100, expiry: '2026-04-16', entry_price: 60, exit_price: 70 },
    { leg_side: 'SHORT', option_type: 'CE', strike: 22150, expiry: '2026-04-16', entry_price: 31.7, exit_price: 25 },
  ],
}

const baseDecisions = [
  {
    id: 'decision-enter',
    timestamp: '2026-04-11T09:31:00',
    spot_close: 22130,
    opening_range_high: 22100,
    opening_range_low: 21900,
    trade_state: 'NO_OPEN_TRADE',
    signal_state: 'EVALUATE',
    action: 'ENTER',
    reason_code: 'ENTER_TRADE',
    reason_text: 'Ranked spread selection chose 22100CE/22150CE.',
    candidate_structure: {
      bias: 'BULLISH',
      long_strike: 22100,
      short_strike: 22150,
      opt_type: 'CE',
      approved_lots: 22,
      lot_size: 75,
      spread_debit: 28.3,
    },
    candidate_ranking_json: {
      selection_method: 'ranked_candidate_selection_v1',
      evaluated_candidates: 5,
      valid_candidates: 2,
      selected_candidate_rank: 1,
      selected_candidate_score: 0.8421,
      candidates: rankedCandidates,
    },
  },
]

describe('PaperTradeBook page', () => {
  it('renders the spread selection explanation for ranked candidate selection', async () => {
    mocks.getPaperSession.mockResolvedValueOnce({ data: baseSession })
    mocks.getPaperDecisions.mockResolvedValueOnce({ data: baseDecisions })
    mocks.getPaperTrade.mockResolvedValueOnce({ data: { trade: baseTrade } })
    mocks.getPaperMarks.mockResolvedValueOnce({ data: [{ timestamp: '2026-04-11T09:32:00', total_mtm: 12000 }] })
    mocks.getPaperCandles.mockResolvedValueOnce({ data: [] })

    render(<PaperTradeBook />)

    await waitFor(() => {
      expect(screen.getByText('Spread Selection')).toBeInTheDocument()
    })

    expect(screen.getByAltText('Adaptive Options logo')).toBeInTheDocument()
    expect(screen.getByText('Paper Session Detail Report')).toBeInTheDocument()
    expect(screen.getByText('ranked_candidate_selection_v1')).toBeInTheDocument()
    expect(screen.getAllByText('22100/22150 CE').length).toBeGreaterThan(0)
    expect(screen.getAllByText('#1').length).toBeGreaterThan(0)
    expect(screen.getAllByText('0.8421').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Selected').length).toBeGreaterThan(0)
  })

  it('hides the selection section when no ranking audit is available', async () => {
    mocks.getPaperSession.mockResolvedValueOnce({ data: baseSession })
    mocks.getPaperDecisions.mockResolvedValueOnce({
      data: [{ ...baseDecisions[0], candidate_ranking_json: null }],
    })
    mocks.getPaperTrade.mockResolvedValueOnce({ data: { trade: { ...baseTrade, selection_method: null } } })
    mocks.getPaperMarks.mockResolvedValueOnce({ data: [] })
    mocks.getPaperCandles.mockResolvedValueOnce({ data: [] })

    render(<PaperTradeBook />)

    await waitFor(() => {
      expect(screen.getByText(/ORB Replay/i)).toBeInTheDocument()
    })

    expect(screen.queryByText('Spread Selection')).not.toBeInTheDocument()
  })
})
