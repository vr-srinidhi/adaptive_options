import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  runPaperSession: vi.fn(),
}))

vi.mock('react-router-dom', () => ({
  useNavigate: () => mocks.navigate,
}))

vi.mock('../api', () => ({
  runPaperSession: mocks.runPaperSession,
}))

import PaperTrading from './PaperTrading'

describe('PaperTrading', () => {
  beforeEach(() => {
    mocks.navigate.mockReset()
    mocks.runPaperSession.mockReset()
  })

  it('runs a replay with trimmed request token and navigates to the session', async () => {
    mocks.runPaperSession.mockResolvedValue({ data: { session_id: 'session-1' } })

    render(<PaperTrading />)

    fireEvent.change(screen.getByPlaceholderText(/request token/i), { target: { value: '  req-token  ' } })
    fireEvent.click(screen.getByRole('button', { name: /replay day/i }))

    await waitFor(() => {
      expect(mocks.runPaperSession).toHaveBeenCalledWith(expect.objectContaining({
        instrument: 'NIFTY',
        capital: 2500000,
        date: '2026-04-07',
        request_token: 'req-token',
      }))
    })
    expect(mocks.navigate).toHaveBeenCalledWith('/paper/session/session-1')
  })

  it('omits blank request token and renders engine errors', async () => {
    mocks.runPaperSession.mockRejectedValue({ response: { data: { detail: 'No token' } } })

    render(<PaperTrading />)

    fireEvent.click(screen.getByRole('button', { name: /replay day/i }))

    await waitFor(() => {
      expect(mocks.runPaperSession).toHaveBeenCalledWith(expect.not.objectContaining({
        request_token: expect.anything(),
      }))
    })
    expect(await screen.findByText('No token')).toBeInTheDocument()
  })

  it('opens past sessions', () => {
    render(<PaperTrading />)

    fireEvent.click(screen.getByRole('button', { name: /view past sessions/i }))
    expect(mocks.navigate).toHaveBeenCalledWith('/paper/sessions')
  })
})
