import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { CumulativePnlChart, PnlProgressionChart } from './PnlChart'

const chartState = vi.hoisted(() => ({
  areaCharts: [],
  yAxes: [],
}))

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }) => <svg data-testid="responsive-container">{children}</svg>,
  AreaChart: (props) => {
    chartState.areaCharts.push(props)
    return <g data-testid="area-chart">{props.children}</g>
  },
  Area: () => <g data-testid="area-series" />,
  XAxis: () => <g data-testid="x-axis" />,
  YAxis: (props) => {
    chartState.yAxes.push(props)
    return <g data-testid="y-axis" />
  },
  CartesianGrid: () => <g data-testid="grid" />,
  Tooltip: () => <g data-testid="tooltip" />,
  ReferenceLine: () => <g data-testid="reference-line" />,
  LineChart: ({ children }) => <g data-testid="line-chart">{children}</g>,
  Line: () => <g data-testid="line-series" />,
}))

describe('PnlChart', () => {
  beforeEach(() => {
    chartState.areaCharts.length = 0
    chartState.yAxes.length = 0
  })

  it('returns nothing when progression data is missing', () => {
    const { container } = render(<PnlProgressionChart data={[]} />)

    expect(container).toBeEmptyDOMElement()
  })

  it('sets progression chart bounds from the largest absolute pnl move', () => {
    render(
      <PnlProgressionChart
        data={[
          { time: '09:30', pnl: -100, spot: 22100 },
          { time: '09:31', pnl: 40, spot: 22110 },
        ]}
      />
    )

    expect(screen.getByTestId('responsive-container')).toBeInTheDocument()
    expect(chartState.yAxes[0].domain).toEqual([-110.00000000000001, 110.00000000000001])
  })

  it('builds cumulative pnl data in session date order', () => {
    render(
      <CumulativePnlChart
        sessions={[
          { session_date: '2025-01-08', pnl: 200 },
          { session_date: '2025-01-06', pnl: 100 },
          { session_date: '2025-01-07', pnl: -50 },
        ]}
      />
    )

    expect(chartState.areaCharts[0].data).toEqual([
      { date: '01-06', cumPnl: 100 },
      { date: '01-07', cumPnl: 50 },
      { date: '01-08', cumPnl: 250 },
    ])
  })
})
