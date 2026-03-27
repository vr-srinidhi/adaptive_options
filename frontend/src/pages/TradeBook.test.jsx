import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  getSession: vi.fn(),
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
    useParams: () => ({ id: '42' }),
  }
})

vi.mock('../api', () => ({
  getSession: mocks.getSession,
}))

vi.mock('../components/PnlChart', () => ({
  PnlProgressionChart: ({ data }) => <div data-testid="pnl-chart">Candles: {data.length}</div>,
}))

import TradeBook from './TradeBook'

const session = {
  id: 42,
  session_date: '2025-01-06',
  instrument: 'NIFTY',
  regime: 'BULLISH',
  strategy: 'BULL_PUT_SPREAD',
  wl: 'WIN',
  exit_reason: 'PROFIT_TARGET',
  pnl_pct: 0.42,
  pnl: 2100,
  ema5: 22110.12,
  ema20: 22095.67,
  rsi14: 55.44,
  iv_rank: 32,
  spot_in: 22123.5,
  spot_out: 22200.7,
  lots: 2,
  capital: 500000,
  max_profit: 4000,
  max_loss: -2500,
  entry_time: '09:30',
  exit_time: '11:05',
  legs: [
    { id: '1', act: 'SELL', typ: 'PE', strike: 22000, delta: -0.28, ep: 120.55, ep2: 88.25, lots: 2, legPnl: 1615 },
    { id: '2', act: 'BUY', typ: 'PE', strike: 21900, delta: -0.12, ep: 52.2, ep2: 44.45, lots: 2, legPnl: 485 },
  ],
  min_data: [
    { time: '09:30', pnl: 0 },
    { time: '09:31', pnl: 100 },
  ],
}

describe('TradeBook page', () => {
  it('renders the fetched trade details and supports navigation back to dashboard', async () => {
    mocks.getSession.mockResolvedValueOnce({ data: session })

    render(<TradeBook />)

    await waitFor(() => {
      expect(screen.getByText(/2025-01-06/i)).toBeInTheDocument()
    })

    expect(screen.getByText('Option Legs (Trade Book)')).toBeInTheDocument()
    expect(screen.getByText('Profit target hit')).toBeInTheDocument()
    expect(screen.getByTestId('pnl-chart')).toHaveTextContent('Candles: 2')

    fireEvent.click(screen.getByRole('button', { name: /dashboard/i }))
    expect(mocks.navigate).toHaveBeenCalledWith('/dashboard')
  })

  it('hides optional sections when the session has no legs or minute data', async () => {
    mocks.getSession.mockResolvedValueOnce({
      data: {
        ...session,
        legs: [],
        min_data: [],
      },
    })

    render(<TradeBook />)

    await waitFor(() => {
      expect(screen.getByText(/2025-01-06/i)).toBeInTheDocument()
    })

    expect(screen.queryByText('Option Legs (Trade Book)')).not.toBeInTheDocument()
    expect(screen.queryByTestId('pnl-chart')).not.toBeInTheDocument()
  })

  it('shows the API error state when the session cannot be loaded', async () => {
    mocks.getSession.mockRejectedValueOnce({
      response: {
        data: {
          detail: 'Session not found',
        },
      },
    })

    render(<TradeBook />)

    await waitFor(() => {
      expect(screen.getByText('Session not found')).toBeInTheDocument()
    })
  })
})
