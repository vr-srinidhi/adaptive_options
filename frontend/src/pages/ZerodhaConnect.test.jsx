import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  api: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
  }
})

vi.mock('../api', () => ({
  default: mocks.api,
}))

import ZerodhaConnect from './ZerodhaConnect'

function renderPage(initialEntry = '/zerodha-connect') {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <ZerodhaConnect />
    </MemoryRouter>
  )
}

describe('ZerodhaConnect', () => {
  beforeEach(() => {
    mocks.navigate.mockReset()
    mocks.api.get.mockReset()
    mocks.api.post.mockReset()
  })

  it('renders connected status when Zerodha session is already authenticated', async () => {
    mocks.api.get.mockResolvedValueOnce({
      data: { authenticated: true, profile: { user_name: 'Trader One' } },
    })

    renderPage()

    expect(await screen.findByText(/Connected as Trader One/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /go to paper trading/i }))
    expect(mocks.navigate).toHaveBeenCalledWith('/paper')
  })

  it('falls back to login URL when status check fails', async () => {
    mocks.api.get
      .mockRejectedValueOnce(new Error('status failed'))
      .mockResolvedValueOnce({ data: { login_url: 'https://kite.example/login' } })

    renderPage()

    const link = await screen.findByRole('link', { name: /connect zerodha account/i })
    expect(link).toHaveAttribute('href', 'https://kite.example/login')
  })

  it('shows login-url errors when a reconnect URL cannot be fetched', async () => {
    mocks.api.get
      .mockResolvedValueOnce({
        data: { authenticated: true, profile: { user_name: 'Trader One' } },
      })
      .mockRejectedValueOnce({
        response: { data: { detail: 'Kite credentials missing' } },
      })

    renderPage()

    await screen.findByText(/Connected as Trader One/)
    fireEvent.click(screen.getByRole('button', { name: /reconnect/i }))

    expect(await screen.findByText('Kite credentials missing')).toBeInTheDocument()
  })

  it('exchanges callback request token and clears the URL on success', async () => {
    mocks.api.post.mockResolvedValueOnce({ data: { user_name: 'Callback User' } })

    renderPage('/zerodha-connect?request_token=req-123')

    expect(await screen.findByText(/Connected as Callback User/)).toBeInTheDocument()
    expect(mocks.api.post).toHaveBeenCalledWith('/auth/zerodha/session', {
      request_token: 'req-123',
    })
    expect(mocks.navigate).toHaveBeenCalledWith('/zerodha-connect', { replace: true })
  })

  it('renders callback exchange failures', async () => {
    mocks.api.post.mockRejectedValueOnce({
      response: { data: { detail: 'Token exchange failed hard' } },
    })

    renderPage('/zerodha-connect?request_token=req-123')

    expect(await screen.findByText('Token exchange failed hard')).toBeInTheDocument()
    expect(mocks.navigate).not.toHaveBeenCalled()
  })
})
