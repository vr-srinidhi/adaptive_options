import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  getHistSession: vi.fn(),
  getHistDecisions: vi.fn(),
  getHistTrade: vi.fn(),
  getHistMarks: vi.fn(),
}))

vi.mock('react-router-dom', () => ({
  useNavigate: () => mocks.navigate,
  useParams: () => ({ sessionId: 'session-1' }),
}))

vi.mock('../api', () => ({
  getHistSession: mocks.getHistSession,
  getHistDecisions: mocks.getHistDecisions,
  getHistTrade: mocks.getHistTrade,
  getHistMarks: mocks.getHistMarks,
}))

vi.mock('../components/PnlChart', () => ({
  PnlProgressionChart: () => <div data-testid="pnl-chart" />,
}))

import HistoricalSessionDetail from './HistoricalSessionDetail'

const session = {
  id: 'session-1',
  batch_id: 'batch-1',
  instrument: 'NIFTY',
  session_date: '2026-05-06',
  source_mode: 'warehouse',
  status: 'COMPLETED',
  final_session_state: 'CLOSED',
  decision_count: 2,
  capital: 2500000,
  summary_pnl: 4500,
}

const trade = {
  bias: 'BULLISH',
  long_strike: 22400,
  short_strike: 22500,
  option_type: 'CE',
  approved_lots: 2,
  entry_debit: 12.5,
  realized_gross_pnl: 5000,
  realized_net_pnl: 4500,
  charges: 500,
  exit_reason: 'TIME_EXIT',
  entry_time: '2026-05-06T09:30:00',
  exit_time: '2026-05-06T15:20:00',
  total_max_loss: 10000,
  target_profit: 4000,
}

describe('HistoricalSessionDetail', () => {
  beforeEach(() => {
    Object.values(mocks).forEach(mock => mock.mockReset())
    mocks.getHistSession.mockResolvedValue({ data: session })
    mocks.getHistDecisions.mockResolvedValue({
      data: [
        { id: 'd1', timestamp: '2026-05-06T09:30:00', spot_close: 22500, action: 'ENTER', reason_code: 'ENTRY', session_state: 'OPEN' },
        { id: 'd2', timestamp: '2026-05-06T15:20:00', spot_close: 22600, action: 'EXIT_TIME', reason_code: 'TIME_EXIT', session_state: 'CLOSED' },
      ],
    })
    mocks.getHistTrade.mockResolvedValue({ data: { trade } })
    mocks.getHistMarks.mockResolvedValue({ data: [{ timestamp: '2026-05-06T09:31:00', estimated_net_mtm: 1500 }] })
  })

  it('renders summary, decisions, trade tabs and breadcrumbs', async () => {
    render(<HistoricalSessionDetail />)

    expect(await screen.findByText('NIFTY · 2026-05-06')).toBeInTheDocument()
    expect(screen.getByTestId('pnl-chart')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Decisions' }))
    expect(screen.getByText('ENTRY')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Trade' }))
    expect(screen.getByText('TRADE HEADER')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Batch' }))
    expect(mocks.navigate).toHaveBeenCalledWith('/backtests/batch-1')
  })

  it('renders no-trade sessions and load errors', async () => {
    mocks.getHistTrade.mockResolvedValueOnce({ data: { trade: null } })
    const { unmount } = render(<HistoricalSessionDetail />)

    expect(await screen.findByText('NIFTY · 2026-05-06')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Trade' }))
    expect(screen.getByText('No trade was taken this session.')).toBeInTheDocument()
    unmount()

    mocks.getHistSession.mockRejectedValueOnce({ response: { data: { detail: 'Session missing' } } })
    render(<HistoricalSessionDetail />)
    expect(await screen.findByText('Session missing')).toBeInTheDocument()
  })
})
