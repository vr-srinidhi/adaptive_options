import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  getResults: vi.fn(),
  getSummary: vi.fn(),
  clearResults: vi.fn(),
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
  }
})

vi.mock('../api', () => ({
  getResults: mocks.getResults,
  getSummary: mocks.getSummary,
  clearResults: mocks.clearResults,
}))

vi.mock('../components/PnlChart', () => ({
  CumulativePnlChart: ({ sessions }) => <div data-testid="cum-chart">Sessions: {sessions.length}</div>,
}))

import Dashboard from './Dashboard'

const sessions = [
  {
    id: 1,
    session_date: '2025-01-06',
    instrument: 'NIFTY',
    regime: 'BULLISH',
    strategy: 'BULL_PUT_SPREAD',
    lots: 2,
    spot_in: 22123,
    spot_out: 22230,
    pnl: 2000,
    pnl_pct: 0.4,
    exit_reason: 'PROFIT_TARGET',
    wl: 'WIN',
  },
  {
    id: 2,
    session_date: '2025-01-07',
    instrument: 'BANKNIFTY',
    regime: 'NO_TRADE',
    strategy: 'NO_TRADE',
    lots: null,
    spot_in: null,
    spot_out: null,
    pnl: 0,
    pnl_pct: 0,
    exit_reason: 'NO_SIGNAL',
    wl: 'NO_TRADE',
  },
]

const summary = {
  totalPnl: 2000,
  totalSessions: 2,
  totalTrades: 1,
  winRate: 100,
  bestDay: { id: 1, pnl: 2000 },
  worstDay: { id: 1, pnl: 2000 },
}

describe('Dashboard page', () => {
  it('renders results and navigates for actionable rows', async () => {
    mocks.getResults.mockResolvedValueOnce({ data: sessions })
    mocks.getSummary.mockResolvedValueOnce({ data: summary })

    render(<Dashboard />)

    await waitFor(() => {
      expect(screen.getByText('Backtest Dashboard')).toBeInTheDocument()
    })

    expect(screen.getByTestId('cum-chart')).toHaveTextContent('Sessions: 2')
    expect(screen.getAllByText('₹2,000')).toHaveLength(4)

    fireEvent.click(screen.getByText('2025-01-06'))
    expect(mocks.navigate).toHaveBeenCalledWith('/tradebook/1')
  })

  it('does not navigate for no-trade rows and skips clear when confirmation is cancelled', async () => {
    mocks.getResults.mockResolvedValueOnce({ data: sessions })
    mocks.getSummary.mockResolvedValueOnce({ data: summary })
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)

    render(<Dashboard />)

    await waitFor(() => {
      expect(screen.getByText('2025-01-07')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByText('2025-01-07'))
    fireEvent.click(screen.getByRole('button', { name: /clear all/i }))

    expect(mocks.navigate).not.toHaveBeenCalled()
    expect(mocks.clearResults).not.toHaveBeenCalled()
    expect(confirmSpy).toHaveBeenCalledWith('Delete all backtest results?')
  })

  it('shows the empty state and routes back to the backtest page', async () => {
    mocks.getResults.mockResolvedValueOnce({ data: [] })
    mocks.getSummary.mockResolvedValueOnce({
      data: {
        totalPnl: 0,
        totalSessions: 0,
        totalTrades: 0,
        winRate: 0,
        bestDay: null,
        worstDay: null,
      },
    })

    render(<Dashboard />)

    await waitFor(() => {
      expect(screen.getByText('No backtest results yet.')).toBeInTheDocument()
    })

    fireEvent.click(screen.getByRole('button', { name: /run your first backtest/i }))
    expect(mocks.navigate).toHaveBeenCalledWith('/backtest')
  })

  it('shows the API error state when loading fails', async () => {
    mocks.getResults.mockRejectedValueOnce(new Error('API unavailable'))
    mocks.getSummary.mockResolvedValueOnce({ data: summary })

    render(<Dashboard />)

    await waitFor(() => {
      expect(screen.getByText('API unavailable')).toBeInTheDocument()
    })
  })
})
