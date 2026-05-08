import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  login: vi.fn(),
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
  }
})

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ login: mocks.login }),
}))

vi.mock('../components/BrandLogo', () => ({
  default: () => <div>Adaptive Options</div>,
}))

import Login from './Login'

describe('Login', () => {
  beforeEach(() => {
    mocks.navigate.mockReset()
    mocks.login.mockReset()
  })

  it('submits credentials and redirects after successful authentication', async () => {
    mocks.login.mockResolvedValue({})

    const { container } = render(<Login />)

    fireEvent.change(screen.getByPlaceholderText('you@example.com'), { target: { value: 'user@example.com' } })
    fireEvent.change(container.querySelector('input[type="password"]'), { target: { value: 'secret' } })
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }))

    await waitFor(() => {
      expect(mocks.login).toHaveBeenCalledWith('user@example.com', 'secret')
    })
    expect(mocks.navigate).toHaveBeenCalledWith('/backtest', { replace: true })
  })

  it('shows the API error detail and re-enables submit on failure', async () => {
    mocks.login.mockRejectedValue({
      response: { data: { detail: 'Invalid email or password' } },
    })

    const { container } = render(<Login />)

    fireEvent.change(screen.getByPlaceholderText('you@example.com'), { target: { value: 'bad@example.com' } })
    fireEvent.change(container.querySelector('input[type="password"]'), { target: { value: 'wrong' } })
    fireEvent.click(screen.getByRole('button', { name: /sign in/i }))

    expect(await screen.findByText('Invalid email or password')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /sign in/i })).not.toBeDisabled()
    expect(mocks.navigate).not.toHaveBeenCalled()
  })
})
