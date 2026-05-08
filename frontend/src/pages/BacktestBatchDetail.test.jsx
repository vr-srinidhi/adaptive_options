import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  getBatch: vi.fn(),
  getBatchSessions: vi.fn(),
}))

vi.mock('react-router-dom', () => ({
  useNavigate: () => mocks.navigate,
  useParams: () => ({ batchId: 'batch-1' }),
}))

vi.mock('../api', () => ({
  getBatch: mocks.getBatch,
  getBatchSessions: mocks.getBatchSessions,
}))

import BacktestBatchDetail from './BacktestBatchDetail'

describe('BacktestBatchDetail', () => {
  beforeEach(() => {
    Object.values(mocks).forEach(mock => mock.mockReset())
  })

  it('renders aggregate stats and navigates to session detail', async () => {
    mocks.getBatch.mockResolvedValue({
      data: {
        id: 'batch-1',
        name: 'May replay',
        status: 'running',
        start_date: '2026-05-01',
        end_date: '2026-05-06',
        strategy_id: 'orb',
        completed_sessions: 1,
        failed_sessions: 1,
        skipped_sessions: 0,
        total_sessions: 3,
        total_pnl: 4500,
      },
    })
    mocks.getBatchSessions.mockResolvedValue({
      data: [
        { id: 's1', session_date: '2026-05-01', status: 'COMPLETED', final_session_state: 'CLOSED', summary_pnl: 5000, decision_count: 100 },
        { id: 's2', session_date: '2026-05-02', status: 'ERROR', final_session_state: 'CLOSED', summary_pnl: -500, decision_count: 50 },
      ],
    })

    render(<BacktestBatchDetail />)

    expect(await screen.findByText('May replay')).toBeInTheDocument()
    expect(screen.getByText('2 / 3')).toBeInTheDocument()
    expect(screen.getByText('50%')).toBeInTheDocument()
    fireEvent.click(screen.getByText('2026-05-01'))
    expect(mocks.navigate).toHaveBeenCalledWith('/backtests/sessions/s1')
  })

  it('renders load failures', async () => {
    mocks.getBatch.mockRejectedValue({ response: { data: { detail: 'Batch missing' } } })
    mocks.getBatchSessions.mockResolvedValue({ data: [] })

    render(<BacktestBatchDetail />)

    expect(await screen.findByText('Batch missing')).toBeInTheDocument()
  })
})
