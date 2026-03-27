import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  runBacktest: vi.fn(),
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
  }
})

vi.mock('../api', () => ({
  runBacktest: mocks.runBacktest,
}))

import Backtest from './Backtest'

function deferred() {
  let resolve
  let reject

  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })

  return { promise, resolve, reject }
}

describe('Backtest page', () => {
  it('submits the form and redirects after a successful run', async () => {
    const pending = deferred()
    mocks.runBacktest.mockReturnValueOnce(pending.promise)

    const { container } = render(<Backtest />)
    const instrumentSelect = container.querySelector('select')
    const capitalInput = container.querySelector('input[type="number"]')
    const [startDateInput, endDateInput] = container.querySelectorAll('input[type="date"]')

    fireEvent.change(instrumentSelect, { target: { value: 'BANKNIFTY' } })
    fireEvent.change(capitalInput, { target: { value: '750000' } })
    fireEvent.change(startDateInput, { target: { value: '2025-02-03' } })
    fireEvent.change(endDateInput, { target: { value: '2025-02-07' } })
    fireEvent.click(screen.getByRole('button', { name: /run backtest/i }))

    expect(mocks.runBacktest).toHaveBeenCalledWith({
      instrument: 'BANKNIFTY',
      capital: 750000,
      startDate: '2025-02-03',
      endDate: '2025-02-07',
    })
    expect(screen.getByText('Starting…')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /running/i })).toBeDisabled()

    pending.resolve({ data: [{ id: 1 }, { id: 2 }] })

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /view dashboard/i })).toBeInTheDocument()
    })

    await waitFor(() => {
      expect(mocks.navigate).toHaveBeenCalledWith('/dashboard')
    })
  })

  it('shows the backend error and stops progress on failure', async () => {
    mocks.runBacktest.mockRejectedValueOnce({
      response: {
        data: {
          detail: 'Start date must be before end date.',
        },
      },
    })

    render(<Backtest />)
    fireEvent.click(screen.getByRole('button', { name: /run backtest/i }))

    await waitFor(() => {
      expect(screen.getByText('Start date must be before end date.')).toBeInTheDocument()
    })

    expect(screen.queryByText('Starting…')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /view dashboard/i })).not.toBeInTheDocument()
    expect(mocks.navigate).not.toHaveBeenCalled()
  })
})
