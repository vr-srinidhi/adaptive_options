import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import RunsLibrary from './RunsLibrary'

const { getWorkbenchRuns, exportStrategyRunsBundle } = vi.hoisted(() => ({
  getWorkbenchRuns: vi.fn(),
  exportStrategyRunsBundle: vi.fn(),
}))

vi.mock('../api', () => ({
  getWorkbenchRuns,
  exportStrategyRunsBundle,
}))

const sampleRuns = [
  {
    id: 'run-1',
    kind: 'strategy_run',
    title: 'Short Straddle',
    strategy_name: 'Short Straddle',
    subtitle: '2026-04-07',
    date_label: '2026-04-07',
    instrument: 'NIFTY',
    status: 'completed',
    created_at: '2026-04-07T10:30:00Z',
    pnl: 12450,
    route: '/workbench/replay/strategy_run/run-1',
  },
  {
    id: 'run-2',
    kind: 'strategy_run',
    title: 'Short Straddle',
    strategy_name: 'Short Straddle',
    subtitle: '2026-04-08',
    date_label: '2026-04-08',
    instrument: 'NIFTY',
    status: 'completed',
    created_at: '2026-04-08T10:30:00Z',
    pnl: 8200,
    route: '/workbench/replay/strategy_run/run-2',
  },
  {
    id: 'paper-1',
    kind: 'paper_session',
    title: 'NIFTY replay',
    strategy_name: 'Opening Range Spread',
    subtitle: '2026-04-07',
    date_label: '2026-04-07',
    instrument: 'NIFTY',
    status: 'COMPLETED',
    created_at: '2026-04-07T10:30:00Z',
    pnl: 5000,
    route: '/workbench/replay/paper_session/paper-1',
    legacy_route: '/paper/session/paper-1',
  },
]

function renderRunsLibrary() {
  return render(
    <MemoryRouter initialEntries={['/workbench/history']}>
      <Routes>
        <Route path="/workbench/history" element={<RunsLibrary />} />
      </Routes>
    </MemoryRouter>
  )
}

describe('RunsLibrary', () => {
  beforeEach(() => {
    getWorkbenchRuns.mockReset()
    exportStrategyRunsBundle.mockReset()
    getWorkbenchRuns.mockResolvedValue({ data: { runs: sampleRuns } })
  })

  it('renders the runs table from the API', async () => {
    renderRunsLibrary()

    expect(await screen.findByText('Runs Library')).toBeInTheDocument()
    expect(screen.getAllByText('Short Straddle').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Paper Replay').length).toBeGreaterThan(0)
  })

  it('checkboxes appear only on strategy_run rows; export button appears after selection', async () => {
    renderRunsLibrary()
    await screen.findByText('Runs Library')

    // select-all header + 2 strategy_run row checkboxes (paper_session has no checkbox)
    const checkboxes = screen.getAllByRole('checkbox')
    expect(checkboxes.length).toBe(3)

    // No export button before any selection (the footer hint doesn't count — it's a span, not a button)
    expect(screen.queryByRole('button', { name: /Export/i })).toBeNull()

    // Select the first strategy_run row
    await userEvent.click(checkboxes[1])

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Export 1 run/i })).toBeInTheDocument()
    })
  })
})
