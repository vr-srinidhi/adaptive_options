import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import RunBuilder from './RunBuilder'

const { getWorkbenchStrategies, getTradingDays, createWorkbenchRun } = vi.hoisted(() => ({
  getWorkbenchStrategies: vi.fn(),
  getTradingDays: vi.fn(),
  createWorkbenchRun: vi.fn(),
}))

vi.mock('../api', () => ({
  getWorkbenchStrategies,
  getTradingDays,
  createWorkbenchRun,
}))

const strategies = [
  {
    id: 'orb_intraday_spread',
    name: 'Opening Range Spread',
    status: 'available',
    modes: ['paper_replay', 'historical_backtest'],
    description: 'Current live strategy.',
    chips: ['Live'],
    defaults: {
      paper_replay: { instrument: 'NIFTY', capital: 2500000, date: '2026-04-07', request_token: '' },
      historical_backtest: {
        name: 'ORB historical replay',
        instrument: 'NIFTY',
        capital: 2500000,
        start_date: '2026-03-01',
        end_date: '2026-04-01',
        execution_order: 'latest_first',
        autorun: true,
      },
    },
    params_schema: [
      { key: 'instrument', label: 'Instrument', type: 'select', options: ['NIFTY', 'BANKNIFTY'] },
      { key: 'capital', label: 'Capital', type: 'number' },
      { key: 'date', label: 'Trade date', type: 'date', modes: ['paper_replay'] },
      { key: 'request_token', label: 'Zerodha request token', type: 'text', modes: ['paper_replay'] },
      { key: 'name', label: 'Backtest name', type: 'text', modes: ['historical_backtest'] },
      { key: 'start_date', label: 'Start date', type: 'date', modes: ['historical_backtest'] },
      { key: 'end_date', label: 'End date', type: 'date', modes: ['historical_backtest'] },
      { key: 'execution_order', label: 'Execution order', type: 'select', modes: ['historical_backtest'], options: ['latest_first', 'oldest_first'] },
      { key: 'autorun', label: 'Auto run', type: 'boolean', modes: ['historical_backtest'] },
    ],
    notes: ['Fully executable'],
  },
]

function renderBuilder(initialEntry = '/workbench/run') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route path="/workbench/run" element={<RunBuilder />} />
        <Route path="/workbench/replay/paper_session/:id" element={<div>Replay detail</div>} />
      </Routes>
    </MemoryRouter>
  )
}

describe('RunBuilder', () => {
  beforeEach(() => {
    getWorkbenchStrategies.mockReset()
    getTradingDays.mockReset()
    createWorkbenchRun.mockReset()

    getWorkbenchStrategies.mockResolvedValue({ data: { strategies } })
    getTradingDays.mockResolvedValue({ data: [{ trade_date: '2026-04-07', backtest_ready: true }] })
    createWorkbenchRun.mockResolvedValue({
      data: { navigate_to: '/workbench/replay/paper_session/test-session' },
    })
  })

  it('loads default strategy config into the form', async () => {
    renderBuilder()
    expect(await screen.findByDisplayValue('NIFTY')).toBeInTheDocument()
    expect(screen.getByDisplayValue('2500000')).toBeInTheDocument()
    expect(screen.getByDisplayValue('2026-04-07')).toBeInTheDocument()
  })

  it('submits the current builder payload to the v2 endpoint', async () => {
    renderBuilder()
    await screen.findByDisplayValue('NIFTY')

    await userEvent.clear(screen.getByDisplayValue('2500000'))
    await userEvent.type(screen.getByLabelText(/capital/i), '3000000')
    await userEvent.click(screen.getByRole('button', { name: /launch replay/i }))

    await waitFor(() => {
      expect(createWorkbenchRun).toHaveBeenCalledWith({
        run_type: 'paper_replay',
        strategy_id: 'orb_intraday_spread',
        config: expect.objectContaining({
          instrument: 'NIFTY',
          capital: '3000000',
          date: '2026-04-07',
          request_token: '',
        }),
      })
    })
  })

  it('does not mark historical spot readiness green when the selected range is not ready', async () => {
    renderBuilder()
    await screen.findByDisplayValue('NIFTY')

    await userEvent.click(screen.getByRole('button', { name: /historical/i }))

    expect(await screen.findByText(/0 \/ 23 warehouse-ready sessions in selected range/i)).toBeInTheDocument()
    expect(screen.getByText(/requires validated session date/i)).toBeInTheDocument()
  })
})
