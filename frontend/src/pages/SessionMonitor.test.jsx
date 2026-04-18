import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  getPaperSessions: vi.fn(),
  exportPaperSessionsBundle: vi.fn(),
  openWindow: vi.fn(),
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
  }
})

vi.mock('../api', () => ({
  getPaperSessions: mocks.getPaperSessions,
  exportPaperSessionsBundle: mocks.exportPaperSessionsBundle,
}))

import SessionMonitor from './SessionMonitor'

describe('SessionMonitor page', () => {
  beforeEach(() => {
    mocks.navigate.mockReset()
    mocks.getPaperSessions.mockReset()
    mocks.exportPaperSessionsBundle.mockReset()
    mocks.openWindow.mockReset()
    window.open = mocks.openWindow
  })

  it('shows session P/L values in the report table', async () => {
    mocks.getPaperSessions.mockResolvedValueOnce({
      data: [
        {
          id: 'session-1',
          session_date: '2026-04-11',
          instrument: 'NIFTY',
          capital: 2500000,
          status: 'COMPLETED',
          decision_count: 20,
          summary_pnl: 24400,
          created_at: '2026-04-11T10:00:00',
        },
        {
          id: 'session-2',
          session_date: '2026-04-10',
          instrument: 'NIFTY',
          capital: 2500000,
          status: 'COMPLETED',
          decision_count: 18,
          summary_pnl: null,
          created_at: '2026-04-10T10:00:00',
        },
      ],
    })

    render(<SessionMonitor />)

    await waitFor(() => {
      expect(screen.getByRole('columnheader', { name: 'P/L' })).toBeInTheDocument()
    })

    const rows = screen.getAllByRole('row')
    expect(within(rows[1]).getByText(/24,400/)).toBeInTheDocument()
    expect(within(rows[2]).getByText('—')).toBeInTheDocument()
  })

  it('opens the bulk PDF print route for selected sessions', async () => {
    mocks.getPaperSessions.mockResolvedValueOnce({
      data: [
        {
          id: 'session-1',
          session_date: '2026-04-11',
          instrument: 'NIFTY',
          capital: 2500000,
          status: 'COMPLETED',
          decision_count: 20,
          summary_pnl: 24400,
          created_at: '2026-04-11T10:00:00',
        },
      ],
    })

    render(<SessionMonitor />)

    const checkbox = await screen.findByLabelText('Select 2026-04-11 NIFTY')
    fireEvent.click(checkbox)
    fireEvent.click(screen.getByRole('button', { name: '↓ Bulk PDF' }))

    expect(mocks.openWindow).toHaveBeenCalledWith(
      '/paper/sessions/print?ids=session-1',
      '_blank',
      'noopener,noreferrer'
    )
  })
})
