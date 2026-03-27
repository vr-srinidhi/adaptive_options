import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ActionBadge, RegimeBadge, TypeBadge, WLBadge } from './RegimeBadge'

describe('RegimeBadge family', () => {
  it('renders known badge variants', () => {
    render(
      <>
        <RegimeBadge regime="BULLISH" />
        <WLBadge wl="BREAK_EVEN" />
        <ActionBadge act="SELL" />
        <TypeBadge typ="CE" />
      </>
    )

    expect(screen.getByText('BULLISH')).toBeInTheDocument()
    expect(screen.getByText('EVEN')).toBeInTheDocument()
    expect(screen.getByText('SELL')).toBeInTheDocument()
    expect(screen.getByText('CE')).toBeInTheDocument()
  })

  it('falls back safely for unknown badge values', () => {
    render(
      <>
        <RegimeBadge regime="SIDEWAYS" />
        <WLBadge wl="PENDING" />
        <ActionBadge act="BUY" />
        <TypeBadge typ="PE" />
      </>
    )

    expect(screen.getByText('SIDEWAYS')).toHaveStyle({ color: 'rgb(245, 158, 11)' })
    expect(screen.getByText('PENDING')).toHaveStyle({ color: 'rgb(100, 116, 139)' })
    expect(screen.getByText('BUY')).toHaveStyle({ color: 'rgb(34, 197, 94)' })
    expect(screen.getByText('PE')).toHaveStyle({ color: 'rgb(245, 158, 11)' })
  })
})
