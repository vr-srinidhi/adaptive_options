import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AuthProvider } from '../contexts/AuthContext'
import TopNav from './TopNav'

function renderInRouter(initialPath = '/backtest') {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={[initialPath]}>
        <TopNav />
      </MemoryRouter>
    </AuthProvider>
  )
}

describe('TopNav', () => {
  // ── Positive tests ───────────────────────────────────────────────────────
  it('renders the brand name', () => {
    renderInRouter()
    expect(screen.getByText('Adaptive')).toBeInTheDocument()
    expect(screen.getByText('Options')).toBeInTheDocument()
  })

  it('renders backtest nav links (Run and Dashboard)', () => {
    renderInRouter()
    expect(screen.getByRole('link', { name: /^run$/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /dashboard/i })).toBeInTheDocument()
  })

  it('renders paper trading nav links (Replay and Sessions)', () => {
    renderInRouter()
    expect(screen.getByRole('link', { name: /replay/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /sessions/i })).toBeInTheDocument()
  })

  it('shows the BACKTEST MODE indicator badge', () => {
    renderInRouter()
    expect(screen.getByText('BACKTEST MODE')).toBeInTheDocument()
  })

  it('Run link points to /backtest', () => {
    renderInRouter()
    expect(screen.getByRole('link', { name: /^run$/i })).toHaveAttribute('href', '/backtest')
  })

  it('Dashboard link points to /dashboard', () => {
    renderInRouter()
    expect(screen.getByRole('link', { name: /dashboard/i })).toHaveAttribute('href', '/dashboard')
  })

  it('active link gets the active class when on /backtest', () => {
    renderInRouter('/backtest')
    const runLink = screen.getByRole('link', { name: /^run$/i })
    const dashboard = screen.getByRole('link', { name: /dashboard/i })
    expect(runLink.className).toContain('text-blue-400')
    expect(dashboard.className).not.toContain('text-blue-400')
  })

  // ── Negative tests ───────────────────────────────────────────────────────
  it('renders nav links including Run, Dashboard, Replay, Sessions, and Zerodha', () => {
    renderInRouter()
    const links = screen.getAllByRole('link')
    // Run, Dashboard, Replay, Sessions, Zerodha
    expect(links.length).toBeGreaterThanOrEqual(5)
  })

  it('Dashboard link is not active when on /backtest', () => {
    renderInRouter('/backtest')
    const dashboard = screen.getByRole('link', { name: /dashboard/i })
    expect(dashboard.className).not.toContain('text-blue-400')
  })
})
