import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import ReplayDesk from './ReplayDesk'

const { getWorkbenchRuns } = vi.hoisted(() => ({
  getWorkbenchRuns: vi.fn(),
}))

vi.mock('../api', () => ({
  getWorkbenchRuns,
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
    metrics: { decision_count: 18 },
    route: '/workbench/replay/paper_session/paper-1',
    legacy_route: '/paper/session/paper-1',
  },
  {
    id: 'paper-2',
    kind: 'paper_session',
    title: 'BANKNIFTY replay',
    strategy_name: 'Opening Range Spread',
    subtitle: '2026-04-04',
    date_label: '2026-04-04',
    instrument: 'BANKNIFTY',
    status: 'RUNNING',
    created_at: '2026-04-04T10:30:00Z',
    pnl: -3200,
    metrics: { decision_count: 10 },
    route: '/workbench/replay/paper_session/paper-2',
  },
]

function renderReplayDesk() {
  return render(
    <MemoryRouter initialEntries={['/workbench/replay']}>
      <Routes>
        <Route path="/workbench/replay" element={<ReplayDesk />} />
        <Route path="/workbench/replay/paper_session/:id" element={<div>Replay analyzer route</div>} />
        <Route path="/workbench/history" element={<div>History route</div>} />
      </Routes>
    </MemoryRouter>
  )
}

describe('ReplayDesk', () => {
  beforeEach(() => {
    getWorkbenchRuns.mockReset()
    getWorkbenchRuns.mockResolvedValue({ data: { runs: sampleRuns } })
  })

  it('renders replay sessions from the API', async () => {
    renderReplayDesk()

    expect(await screen.findByText('Replay Desk')).toBeInTheDocument()
    expect(screen.getAllByText('Opening Range Spread').length).toBeGreaterThan(0)
    expect(screen.getByText('Replayable Sessions')).toBeInTheDocument()
  })

  it('opens the analyzer from the latest session card', async () => {
    renderReplayDesk()
    await screen.findByText('Replay Desk')

    await userEvent.click(screen.getByRole('button', { name: /open analyzer/i }))

    await waitFor(() => {
      expect(screen.getByText('Replay analyzer route')).toBeInTheDocument()
    })
  })
})
