import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  getStrategyDashboard: vi.fn(),
  params: { strategyId: 'short_straddle' },
}))

vi.mock('react-router-dom', () => ({
  useNavigate: () => mocks.navigate,
  useParams: () => mocks.params,
}))

vi.mock('../api/index.js', () => ({
  getStrategyDashboard: mocks.getStrategyDashboard,
}))

vi.mock('recharts', () => ({
  Bar: ({ children }) => <g data-testid="bar">{children}</g>,
  BarChart: ({ children }) => <svg data-testid="bar-chart">{children}</svg>,
  CartesianGrid: () => <g data-testid="grid" />,
  Cell: ({ fill }) => <g data-testid="cell" data-fill={fill} />,
  Line: () => <g data-testid="line" />,
  LineChart: ({ children }) => <svg data-testid="line-chart">{children}</svg>,
  ReferenceLine: () => <g data-testid="reference-line" />,
  ResponsiveContainer: ({ children }) => <div>{children}</div>,
  Tooltip: () => <g data-testid="tooltip" />,
  XAxis: () => <g data-testid="x-axis" />,
  YAxis: () => <g data-testid="y-axis" />,
}))

import StrategyReport from './StrategyReport'

const strategy = {
  strategy_id: 'short_straddle',
  strategy_name: 'Short Straddle',
  date_range: { from: '2026-05-01', to: '2026-05-06' },
  total_runs: 4,
  wins: 3,
  losses: 1,
  win_rate: 75,
  net_pnl: 180000,
  avg_win: 70000,
  avg_loss: -30000,
  best_day: { date: '2026-05-04', pnl: 90000 },
  worst_day: { date: '2026-05-05', pnl: -30000 },
  lock_stats: {
    count: 2,
    pct: 50,
    wins: 1,
    losses: 1,
    avg_pnl: 25000,
    avg_pnl_no_lock: 40000,
    profit_lock_count: 1,
    profit_lock_avg_pnl: 45000,
    loss_lock_count: 1,
    loss_lock_avg_pnl: -10000,
  },
  daily_pnl: [
    { date: '2026-05-01', pnl: 50000, cumulative_pnl: 50000, exit_reason: 'TARGET', wings_locked: false },
    { date: '2026-05-04', pnl: 90000, cumulative_pnl: 140000, exit_reason: 'TIME_EXIT', wings_locked: true, lock_reason: 'profit', lock_time: '10:15' },
    { date: '2026-05-05', pnl: -30000, cumulative_pnl: 110000, exit_reason: 'STOP', wings_locked: true, lock_reason: 'loss', lock_time: '11:00' },
    { date: '2026-05-06', pnl: 70000, cumulative_pnl: 180000, exit_reason: 'TIME_EXIT', wings_locked: false },
  ],
  monthly_pnl: [
    { month: '2026-05', pnl: 180000, wins: 3, losses: 1, runs: 4 },
  ],
}

describe('StrategyReport', () => {
  beforeEach(() => {
    mocks.navigate.mockReset()
    mocks.getStrategyDashboard.mockReset()
    mocks.params = { strategyId: 'short_straddle' }
  })

  it('renders strategy metrics, lock stats, charts, and monthly totals', async () => {
    mocks.getStrategyDashboard.mockResolvedValue({ data: { strategies: [strategy] } })

    render(<StrategyReport />)

    expect(screen.getByText(/Loading/)).toBeInTheDocument()
    expect(await screen.findByText('Short Straddle')).toBeInTheDocument()
    expect(screen.getByText('Total Sessions')).toBeInTheDocument()
    expect(screen.getByText('Profit Lock Stats')).toBeInTheDocument()
    expect(screen.getByText('Cumulative P&L')).toBeInTheDocument()
    expect(screen.getByText('Monthly P&L')).toBeInTheDocument()
    expect(screen.getByText('Daily P&L')).toBeInTheDocument()
    expect(screen.getByText('Monthly Breakdown')).toBeInTheDocument()
    expect(screen.getByText('Lock Fired')).toBeInTheDocument()
    expect(screen.getByText('Win Rate (Locked)')).toBeInTheDocument()
    expect(screen.getByText('2026-05')).toBeInTheDocument()
    expect(screen.getAllByTestId('cell').length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: /dashboard/i }))
    expect(mocks.navigate).toHaveBeenCalledWith('/workbench')
  })

  it('renders not-found state and navigates back', async () => {
    mocks.params = { strategyId: 'missing_strategy' }
    mocks.getStrategyDashboard.mockResolvedValue({ data: { strategies: [strategy] } })

    render(<StrategyReport />)

    expect(await screen.findByText('Strategy not found or no runs yet.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /back to dashboard/i }))
    expect(mocks.navigate).toHaveBeenCalledWith('/workbench')
  })

  it('renders API errors', async () => {
    mocks.getStrategyDashboard.mockRejectedValue({ response: { data: { detail: 'Dashboard failed' } } })

    render(<StrategyReport />)

    await waitFor(() => {
      expect(screen.getByText('Dashboard failed')).toBeInTheDocument()
    })
  })
})
