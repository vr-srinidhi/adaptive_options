import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { AuthProvider, useAuth } from './AuthContext'

const mocks = vi.hoisted(() => ({
  api: {
    post: vi.fn(),
    get: vi.fn(),
  },
  setToken: vi.fn(),
  setAuthHandlers: vi.fn(),
}))

vi.mock('../api', () => ({
  default: mocks.api,
  setToken: mocks.setToken,
  setAuthHandlers: mocks.setAuthHandlers,
}))

function Probe() {
  const { accessToken, user, authReady } = useAuth()
  return (
    <div>
      <div data-testid="ready">{String(authReady)}</div>
      <div data-testid="token">{accessToken || ''}</div>
      <div data-testid="user">{user?.email || ''}</div>
    </div>
  )
}

describe('AuthProvider', () => {
  beforeEach(() => {
    mocks.api.post.mockReset()
    mocks.api.get.mockReset()
    mocks.setToken.mockReset()
    mocks.setAuthHandlers.mockReset()
  })

  it('restores the session from the refresh cookie on mount', async () => {
    mocks.api.post.mockImplementation(async (url) => {
      if (url === '/users/refresh') {
        return { data: { access_token: 'fresh-token' } }
      }
      throw new Error(`unexpected POST ${url}`)
    })
    mocks.api.get.mockImplementation(async (url) => {
      if (url === '/users/me') {
        return { data: { email: 'user@example.com' } }
      }
      throw new Error(`unexpected GET ${url}`)
    })

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    )

    await waitFor(() => expect(screen.getByTestId('ready')).toHaveTextContent('true'))
    expect(screen.getByTestId('token')).toHaveTextContent('fresh-token')
    expect(screen.getByTestId('user')).toHaveTextContent('user@example.com')
    expect(mocks.setToken).toHaveBeenCalledWith('fresh-token')
    expect(mocks.setAuthHandlers).toHaveBeenCalled()
  })

  it('marks auth ready even when refresh fails and clears auth state', async () => {
    mocks.api.post.mockRejectedValue(new Error('refresh failed'))

    render(
      <AuthProvider>
        <Probe />
      </AuthProvider>
    )

    await waitFor(() => expect(screen.getByTestId('ready')).toHaveTextContent('true'))
    expect(screen.getByTestId('token')).toHaveTextContent('')
    expect(screen.getByTestId('user')).toHaveTextContent('')
    expect(mocks.setToken).toHaveBeenCalledWith(null)
  })
})
