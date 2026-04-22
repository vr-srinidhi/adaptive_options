import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import RunsLibrary from './RunsLibrary'

const { getWorkbenchRuns, compareWorkbenchRuns } = vi.hoisted(() => ({
  getWorkbenchRuns: vi.fn(),
  compareWorkbenchRuns: vi.fn(),
}))

vi.mock('../api', () => ({
  getWorkbenchRuns,
  compareWorkbenchRuns,
}))

const sampleRuns = [
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
    pnl: 12450,
    summary: 'Completed session',
    route: '/workbench/replay/paper_session/paper-1',
    legacy_route: '/paper/session/paper-1',
  },
  {
    id: 'batch-1',
    kind: 'historical_batch',
    title: 'ORB historical replay',
    strategy_name: 'Opening Range Spread',
    subtitle: '2026-04-01 → 2026-04-07',
    date_label: '2026-04-01 → 2026-04-07',
    instrument: 'NIFTY',
    status: 'completed',
    created_at: '2026-04-08T08:00:00Z',
    pnl: -3200,
    summary: '5/5 sessions complete',
    route: '/workbench/history/historical_batch/batch-1',
  },
]

function renderRunsLibrary() {
  return render(
    <MemoryRouter initialEntries={['/workbench/history']}>
      <Routes>
        <Route path="/workbench/history" element={<RunsLibrary />} />
        <Route path="/workbench/history/historical_batch/:id" element={<div>History detail route</div>} />
      </Routes>
    </MemoryRouter>
  )
}

describe('RunsLibrary', () => {
  beforeEach(() => {
    getWorkbenchRuns.mockReset()
    compareWorkbenchRuns.mockReset()
    getWorkbenchRuns.mockResolvedValue({ data: { runs: sampleRuns } })
    compareWorkbenchRuns.mockResolvedValue({ data: { items: sampleRuns } })
  })

  it('renders the runs table from the API', async () => {
    renderRunsLibrary()

    expect(await screen.findByText('Runs Library')).toBeInTheDocument()
    expect(screen.getAllByText('Opening Range Spread').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Paper Replay').length).toBeGreaterThan(0)
  })

  it('loads compare data when two runs are selected', async () => {
    renderRunsLibrary()
    await screen.findByText('Runs Library')

    const compareBoxes = screen.getAllByRole('checkbox')
    await userEvent.click(compareBoxes[0])
    await userEvent.click(compareBoxes[1])

    await waitFor(() => {
      expect(compareWorkbenchRuns).toHaveBeenCalledWith('paper_session:paper-1,historical_batch:batch-1')
    })

    expect(await screen.findByText(/compare: p&l snapshot/i)).toBeInTheDocument()
  })
})
