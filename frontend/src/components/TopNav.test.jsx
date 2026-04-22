import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { AuthProvider } from '../contexts/AuthContext'
import TopNav from './TopNav'

vi.mock('../api', async () => {
  const actual = await vi.importActual('../api')
  return {
    ...actual,
    setAuthHandlers: vi.fn(),
    setToken: vi.fn(),
    default: {
      post: vi.fn(() => Promise.reject(new Error('skip auth bootstrap'))),
      get: vi.fn(() => Promise.reject(new Error('skip auth bootstrap'))),
    },
  }
})

function renderInRouter(initialPath = '/workbench') {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={[initialPath]}>
        <TopNav />
      </MemoryRouter>
    </AuthProvider>
  )
}

describe('TopNav', () => {
  it('renders the brand name', () => {
    renderInRouter()
    expect(screen.getByAltText('Adaptive Options logo')).toBeInTheDocument()
    expect(screen.getByText('Adaptive')).toBeInTheDocument()
    expect(screen.getByText('Options')).toBeInTheDocument()
  })

  it('renders the workbench navigation links', () => {
    renderInRouter()
    expect(screen.getByRole('link', { name: /^home$/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /^strategies$/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /^run$/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /^replay$/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /^history$/i })).toBeInTheDocument()
  })

  it('renders secondary legacy links', () => {
    renderInRouter('/backtest')
    expect(screen.getByRole('link', { name: /^backtest$/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /^paper$/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /^sessions$/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /^backtests$/i })).toBeInTheDocument()
  })

  it('marks the active workbench section label', () => {
    renderInRouter('/workbench/strategies')
    expect(screen.getByText('STRATEGIES', { selector: 'span' })).toBeInTheDocument()
  })

  it('home link points to /workbench', () => {
    renderInRouter()
    expect(screen.getByRole('link', { name: /^home$/i })).toHaveAttribute('href', '/workbench')
  })

  it('run link points to /workbench/run', () => {
    renderInRouter()
    expect(screen.getByRole('link', { name: /^run$/i })).toHaveAttribute('href', '/workbench/run')
  })

  it('zerodha link is still present', () => {
    renderInRouter()
    expect(screen.getByRole('link', { name: /zerodha/i })).toHaveAttribute('href', '/zerodha-connect')
  })
})
