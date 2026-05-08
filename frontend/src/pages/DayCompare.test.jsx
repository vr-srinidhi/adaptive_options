import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  getDayCompare: vi.fn(),
  getStrategyDashboard: vi.fn(),
  open: vi.fn(),
}))

vi.mock('react-router-dom', () => ({
  useNavigate: () => mocks.navigate,
}))

vi.mock('../api/index.js', () => ({
  getDayCompare: mocks.getDayCompare,
  getStrategyDashboard: mocks.getStrategyDashboard,
}))

vi.mock('recharts', () => ({
  LineChart: ({ children }) => <svg data-testid="line-chart">{children}</svg>,
  Line: () => <g data-testid="line" />,
  XAxis: () => <g data-testid="x-axis" />,
  YAxis: () => <g data-testid="y-axis" />,
  Tooltip: () => <g data-testid="tooltip" />,
  ResponsiveContainer: ({ children }) => <div>{children}</div>,
  CartesianGrid: () => <g data-testid="grid" />,
  ReferenceLine: () => <g data-testid="reference" />,
}))

import DayCompare from './DayCompare'

const compareData = {
  date: '2026-05-06',
  strategies: [
    {
      strategy_id: 'plain',
      strategy_name: 'Plain',
      run_id: 'run-1',
      pnl: 50000,
      exit_reason: 'TIME_EXIT',
      entry_time: '09:30',
      exit_time: '15:25',
      lots: 2,
      lot_size: 75,
      entry_credit_total: 100000,
      total_charges: 250,
      wings_locked: false,
      mtm_series: [{ t: '09:30', net_mtm: 0 }, { t: '09:31', net_mtm: 50000 }],
    },
    {
      strategy_id: 'dual',
      strategy_name: 'Dual Lock',
      run_id: 'run-2',
      pnl: -10000,
      exit_reason: 'STOP_EXIT',
      entry_time: '09:30',
      exit_time: '10:00',
      lots: 2,
      lot_size: 75,
      entry_credit_total: 100000,
      total_charges: 250,
      wings_locked: true,
      lock_reason: 'loss',
      lock_time: '09:45',
      mtm_series: [{ t: '09:30', net_mtm: 0 }, { t: '09:31', net_mtm: -10000 }],
    },
  ],
}

describe('DayCompare', () => {
  beforeEach(() => {
    Object.values(mocks).forEach(mock => mock.mockReset())
    vi.stubGlobal('open', mocks.open)
    mocks.getStrategyDashboard.mockResolvedValue({
      data: {
        strategies: [
          { strategy_id: 'plain', strategy_name: 'Plain' },
          { strategy_id: 'dual', strategy_name: 'Dual Lock' },
        ],
      },
    })
  })

  it('loads dashboard strategies and compares selected strategies for a date', async () => {
    mocks.getDayCompare.mockResolvedValue({ data: compareData })

    render(<DayCompare />)

    expect(await screen.findByText('Plain')).toBeInTheDocument()
    fireEvent.change(screen.getByDisplayValue(''), { target: { value: '2026-05-06' } })
    fireEvent.click(screen.getByRole('button', { name: /compare/i }))

    await waitFor(() => {
      expect(mocks.getDayCompare).toHaveBeenCalledWith('2026-05-06', ['plain', 'dual'])
    })
    expect(await screen.findByText('Best:')).toBeInTheDocument()
    expect(screen.getByText(/Worst:/)).toBeInTheDocument()
    expect(screen.getByText('Net MTM — All Strategies')).toBeInTheDocument()
    fireEvent.click(screen.getAllByText('Dual Lock')[1])
    expect(mocks.open).toHaveBeenCalledWith('/workbench/replay/strategy_run/run-2', '_blank')
  })

  it('keeps at least one strategy selected and supports quick dates', async () => {
    mocks.getDayCompare.mockResolvedValue({ data: { date: '2026-02-20', strategies: [] } })

    render(<DayCompare />)

    const plainButton = await screen.findByRole('button', { name: /Plain/i })
    const dualButton = screen.getByRole('button', { name: /Dual Lock/i })
    fireEvent.click(plainButton)
    fireEvent.click(dualButton)
    fireEvent.click(screen.getByRole('button', { name: /20 Feb 2026/i }))

    await waitFor(() => {
      expect(mocks.getDayCompare).toHaveBeenCalledWith('2026-02-20', ['dual'])
    })
    expect(await screen.findByText(/No completed strategy runs found/)).toBeInTheDocument()
  })

  it('renders compare errors and dashboard link navigation', async () => {
    mocks.getDayCompare.mockRejectedValue({ response: { data: { detail: 'No data' } } })

    render(<DayCompare />)

    fireEvent.change(await screen.findByDisplayValue(''), { target: { value: '2026-05-06' } })
    fireEvent.click(screen.getByRole('button', { name: /compare/i }))

    expect(await screen.findByText('No data')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /dashboard/i }))
    expect(mocks.navigate).toHaveBeenCalledWith('/workbench')
  })
})
