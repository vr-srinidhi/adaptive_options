import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import TopNav from './TopNav'

function renderInRouter(initialPath = '/backtest') {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <TopNav />
    </MemoryRouter>
  )
}

describe('TopNav', () => {
  // ── Positive tests ───────────────────────────────────────────────────────
  it('renders the brand name', () => {
    renderInRouter()
    expect(screen.getByText('Adaptive')).toBeInTheDocument()
    expect(screen.getByText('Options')).toBeInTheDocument()
  })

  it('renders Backtest and Dashboard nav links', () => {
    renderInRouter()
    expect(screen.getByRole('link', { name: /backtest/i })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /dashboard/i })).toBeInTheDocument()
  })

  it('shows the BACKTEST MODE indicator badge', () => {
    renderInRouter()
    expect(screen.getByText('BACKTEST MODE')).toBeInTheDocument()
  })

  it('Backtest link points to /backtest', () => {
    renderInRouter()
    expect(screen.getByRole('link', { name: /backtest/i })).toHaveAttribute('href', '/backtest')
  })

  it('Dashboard link points to /dashboard', () => {
    renderInRouter()
    expect(screen.getByRole('link', { name: /dashboard/i })).toHaveAttribute('href', '/dashboard')
  })

  it('active link gets the active class when on /backtest', () => {
    renderInRouter('/backtest')
    const backtest = screen.getByRole('link', { name: /backtest/i })
    const dashboard = screen.getByRole('link', { name: /dashboard/i })
    expect(backtest.className).toContain('text-blue-400')
    expect(dashboard.className).not.toContain('text-blue-400')
  })

  // ── Negative tests ───────────────────────────────────────────────────────
  it('does not render extra nav links beyond Backtest and Dashboard', () => {
    renderInRouter()
    const links = screen.getAllByRole('link')
    expect(links).toHaveLength(2)
  })

  it('Dashboard link is not active when on /backtest', () => {
    renderInRouter('/backtest')
    const dashboard = screen.getByRole('link', { name: /dashboard/i })
    expect(dashboard.className).not.toContain('text-blue-400')
  })
})
