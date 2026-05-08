import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  getBatches: vi.fn(),
  createBatch: vi.fn(),
  deleteBatch: vi.fn(),
  triggerBatch: vi.fn(),
}))

vi.mock('react-router-dom', () => ({
  useNavigate: () => mocks.navigate,
}))

vi.mock('../api', () => ({
  getBatches: mocks.getBatches,
  createBatch: mocks.createBatch,
  deleteBatch: mocks.deleteBatch,
  triggerBatch: mocks.triggerBatch,
}))

import Backtests from './Backtests'

const batch = {
  id: 'batch-1',
  name: 'May replay',
  status: 'completed_with_warnings',
  start_date: '2026-05-01',
  end_date: '2026-05-06',
  completed_sessions: 3,
  failed_sessions: 1,
  skipped_sessions: 0,
  total_sessions: 4,
  total_pnl: 125000,
}

describe('Backtests', () => {
  beforeEach(() => {
    Object.values(mocks).forEach(mock => mock.mockReset?.())
    vi.stubGlobal('confirm', vi.fn(() => true))
    vi.stubGlobal('alert', vi.fn())
  })

  it('loads batches, navigates to detail, reruns and deletes a batch', async () => {
    mocks.getBatches.mockResolvedValue({ data: [batch] })
    mocks.triggerBatch.mockResolvedValue({})
    mocks.deleteBatch.mockResolvedValue({})

    render(<Backtests />)

    const row = await screen.findByText('May replay')
    expect(screen.getByText('completed_with_warnings')).toBeInTheDocument()
    expect(document.body.textContent).toContain('4/4')
    fireEvent.click(row)
    expect(mocks.navigate).toHaveBeenCalledWith('/backtests/batch-1')

    fireEvent.click(screen.getByRole('button', { name: /re-run/i }))
    await waitFor(() => expect(mocks.triggerBatch).toHaveBeenCalledWith('batch-1'))
    expect(mocks.getBatches).toHaveBeenCalledTimes(2)

    fireEvent.click(screen.getByRole('button', { name: /delete/i }))
    await waitFor(() => expect(mocks.deleteBatch).toHaveBeenCalledWith('batch-1'))
    expect(screen.queryByText('May replay')).not.toBeInTheDocument()
  })

  it('creates a batch and prepends it to the table', async () => {
    mocks.getBatches.mockResolvedValue({ data: [] })
    mocks.createBatch.mockResolvedValue({ data: { ...batch, id: 'batch-new', name: 'New batch' } })

    render(<Backtests />)

    fireEvent.click(await screen.findByRole('button', { name: /new backtest/i }))
    fireEvent.change(screen.getByPlaceholderText(/NIFTY Jan-Mar/i), { target: { value: 'New batch' } })
    fireEvent.click(screen.getByRole('button', { name: /create & run/i }))

    await waitFor(() => {
      expect(mocks.createBatch).toHaveBeenCalledWith(expect.objectContaining({
        name: 'New batch',
        capital: expect.any(Number),
      }))
    })
    expect(await screen.findByText('New batch')).toBeInTheDocument()
  })

  it('shows API and form errors', async () => {
    mocks.getBatches.mockRejectedValueOnce({ response: { data: { detail: 'Cannot load batches' } } })
    mocks.createBatch.mockRejectedValueOnce({ response: { data: { detail: 'Bad date range' } } })

    render(<Backtests />)

    expect(await screen.findByText('Cannot load batches')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /new backtest/i }))
    fireEvent.change(screen.getByPlaceholderText(/NIFTY Jan-Mar/i), { target: { value: 'Broken batch' } })
    fireEvent.click(screen.getByRole('button', { name: /create & run/i }))

    expect(await screen.findByText('Bad date range')).toBeInTheDocument()
  })
})
