import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import MetricCard from './MetricCard'

describe('MetricCard', () => {
  it('renders values and handles clicks when interactive', () => {
    const handleClick = vi.fn()

    render(
      <MetricCard
        label="Total P&L"
        value="Rs. 1,500"
        subtext="2 trades"
        color="#22c55e"
        onClick={handleClick}
      />
    )

    expect(screen.getByText('Total P&L')).toBeInTheDocument()
    expect(screen.getByText('Rs. 1,500')).toBeInTheDocument()
    expect(screen.getByText('2 trades')).toBeInTheDocument()

    fireEvent.click(screen.getByText('Rs. 1,500').parentElement)
    expect(handleClick).toHaveBeenCalledTimes(1)
  })

  it('renders safely without optional props', () => {
    render(<MetricCard label="Win Rate" value="60%" />)

    expect(screen.getByText('Win Rate')).toBeInTheDocument()
    expect(screen.getByText('60%').parentElement).not.toHaveClass('cursor-pointer')
    expect(screen.queryByText(/trades/i)).not.toBeInTheDocument()
  })
})
