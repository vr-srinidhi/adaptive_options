import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import ProtectedRoute from './ProtectedRoute'

const mocks = vi.hoisted(() => ({
  useAuth: vi.fn(),
}))

vi.mock('../contexts/AuthContext', () => ({
  useAuth: mocks.useAuth,
}))

describe('ProtectedRoute', () => {
  it('waits for auth bootstrap before rendering or redirecting', () => {
    mocks.useAuth.mockReturnValue({ authReady: false, accessToken: null })

    const { container } = render(
      <MemoryRouter>
        <ProtectedRoute>
          <div>Private</div>
        </ProtectedRoute>
      </MemoryRouter>
    )

    expect(container).toBeEmptyDOMElement()
  })

  it('redirects to login after auth bootstrap when there is no token', () => {
    mocks.useAuth.mockReturnValue({ authReady: true, accessToken: null })

    render(
      <MemoryRouter initialEntries={['/paper']}>
        <ProtectedRoute>
          <div>Private</div>
        </ProtectedRoute>
      </MemoryRouter>
    )

    expect(screen.queryByText('Private')).not.toBeInTheDocument()
  })

  it('renders protected content when the session is active', () => {
    mocks.useAuth.mockReturnValue({ authReady: true, accessToken: 'token' })

    render(
      <MemoryRouter>
        <ProtectedRoute>
          <div>Private</div>
        </ProtectedRoute>
      </MemoryRouter>
    )

    expect(screen.getByText('Private')).toBeInTheDocument()
  })
})
