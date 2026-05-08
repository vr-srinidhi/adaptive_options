import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  getStrategyDashboard: vi.fn(),
}))

vi.mock('react-router-dom', () => ({
  useNavigate: () => mocks.navigate,
}))

vi.mock('../api/index.js', () => ({
  getStrategyDashboard: mocks.getStrategyDashboard,
}))

vi.mock('recharts', () => ({
  AreaChart: ({ children }) => <svg data-testid="area-chart">{children}</svg>,
  Area: () => <div data-testid="area" />,
  XAxis: () => <div data-testid="x-axis" />,
  YAxis: () => <div data-testid="y-axis" />,
  Tooltip: () => <div data-testid="tooltip" />,
  ResponsiveContainer: ({ children }) => <div>{children}</div>,
}))

import WorkspaceHome from './WorkspaceHome'

describe('WorkspaceHome', () => {
  beforeEach(() => {
    mocks.navigate.mockReset()
    mocks.getStrategyDashboard.mockReset()
  })

  it('renders loading and then an empty state with navigation to run builder', async () => {
    mocks.getStrategyDashboard.mockResolvedValueOnce({ data: { strategies: [] } })

    render(<WorkspaceHome />)

    expect(screen.getByText('Loading…')).toBeInTheDocument()
    const runBuilder = await screen.findByText('Run Builder')
    fireEvent.click(runBuilder)
    expect(mocks.navigate).toHaveBeenCalledWith('/workbench/run')
  })

  it('renders strategy cards and navigates to reports', async () => {
    mocks.getStrategyDashboard.mockResolvedValueOnce({
      data: {
        strategies: [{
          strategy_id: 'short_straddle',
          strategy_name: 'Short Straddle',
          date_range: { from: '2026-05-01', to: '2026-05-06' },
          total_runs: 3,
          net_pnl: 125000,
          win_rate: 66.7,
          wins: 2,
          losses: 1,
          avg_win: 80000,
          avg_loss: -35000,
          daily_pnl: [{ date: '2026-05-06', cumulative_pnl: 125000 }],
        }],
      },
    })

    render(<WorkspaceHome />)

    const card = await screen.findByText('Short Straddle')
    expect(screen.getByText('+₹1.25L')).toBeInTheDocument()
    fireEvent.click(card.closest('div[style]'))
    expect(mocks.navigate).toHaveBeenCalledWith('/workbench/report/short_straddle')
  })

  it('renders API errors', async () => {
    mocks.getStrategyDashboard.mockRejectedValueOnce({
      response: { data: { detail: 'dashboard unavailable' } },
    })

    render(<WorkspaceHome />)

    await waitFor(() => {
      expect(screen.getByText('Error: dashboard unavailable')).toBeInTheDocument()
    })
  })

  it('navigates to day compare from the toolbar', async () => {
    mocks.getStrategyDashboard.mockResolvedValueOnce({ data: { strategies: [] } })

    render(<WorkspaceHome />)

    await screen.findByText('Run Builder')
    fireEvent.click(screen.getByRole('button', { name: /day compare/i }))
    expect(mocks.navigate).toHaveBeenCalledWith('/workbench/compare')
  })
})
