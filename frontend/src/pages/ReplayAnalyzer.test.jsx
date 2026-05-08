import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  getWorkbenchReplay: vi.fn(),
  getStrategyRunReplayCsv: vi.fn(),
  params: { kind: 'strategy_run', id: 'run-1' },
}))

vi.mock('react-router-dom', () => ({
  Link: ({ to, children, ...props }) => <a href={to} {...props}>{children}</a>,
  useNavigate: () => mocks.navigate,
  useParams: () => mocks.params,
}))

vi.mock('../api', () => ({
  getWorkbenchReplay: mocks.getWorkbenchReplay,
  getStrategyRunReplayCsv: mocks.getStrategyRunReplayCsv,
}))

vi.mock('recharts', () => ({
  CartesianGrid: () => <g data-testid="grid" />,
  Label: () => <g data-testid="label" />,
  Line: () => <g data-testid="line" />,
  LineChart: ({ children }) => <svg data-testid="line-chart">{children}</svg>,
  ReferenceLine: () => <g data-testid="reference-line" />,
  ResponsiveContainer: ({ children }) => <div>{children}</div>,
  Tooltip: () => <g data-testid="tooltip" />,
  XAxis: () => <g data-testid="x-axis" />,
  YAxis: () => <g data-testid="y-axis" />,
}))

import ReplayAnalyzer from './ReplayAnalyzer'

const strategyPayload = {
  run: {
    id: 'run-1',
    strategy_id: 'short_straddle',
    instrument: 'NIFTY',
    trade_date: '2026-05-06',
    status: 'completed',
    exit_reason: 'TIME_EXIT',
    realized_net_pnl: 12500,
    capital: 2500000,
    lots: 2,
    lot_size: 75,
    entry_time: '09:30',
    exit_time: '15:25',
    mfe: 20000,
    mae: -5000,
    total_charges: 500,
    entry_credit_total: 15000,
  },
  legs: [
    { leg_index: 0, side: 'SELL', option_type: 'CE', strike: 22500, expiry_date: '2026-05-12', lots: 2, quantity: 150, entry_price: 100, exit_price: 80, gross_leg_pnl: 3000 },
    { leg_index: 1, side: 'SELL', option_type: 'PE', strike: 22500, expiry_date: '2026-05-12', lots: 2, quantity: 150, entry_price: 90, exit_price: 70, gross_leg_pnl: 3000 },
  ],
  events: [
    { timestamp: '2026-05-06T09:30:00', event_type: 'ENTRY', reason_code: 'ENTRY_SCHEDULED', reason_text: 'Entered' },
    { timestamp: '2026-05-06T15:25:00', event_type: 'TIME_EXIT', reason_code: 'TIME_EXIT', reason_text: 'Exit' },
  ],
  mtm_series: [
    { timestamp: '2026-05-06T09:31:00', spot: 22500, net_mtm: 1000, gross_mtm: 1200, ce_price: 100, pe_price: 90, trail_stop_level: 500 },
    { timestamp: '2026-05-06T09:32:00', spot: 22510, net_mtm: 1500, gross_mtm: 1700, ce_price: 95, pe_price: 85, event_code: 'ENTRY' },
  ],
  shadow_mtm_series: [{ timestamp: '2026-05-06T09:31:00', net_mtm: 900 }],
  spot_series_full: [{ timestamp: '2026-05-06T09:31:00', close: 22500 }],
  vix_series_full: [{ timestamp: '2026-05-06T09:31:00', vix_close: 12.5 }],
  leg_candles: {
    0: [{ timestamp: '2026-05-06T09:31:00', close: 100 }],
    1: [{ timestamp: '2026-05-06T09:31:00', close: 90 }],
  },
  data_quality: [{ message: 'Used shadow MTM fallback' }],
}

const legacyPayload = {
  session: {
    id: 'session-1',
    instrument: 'NIFTY',
    session_date: '2026-05-06',
    status: 'COMPLETED',
    final_session_state: 'TRADE_CLOSED',
    summary_pnl: 4500,
    capital: 2500000,
    session_type: 'historical_session',
    source_mode: 'warehouse',
    created_at: '2026-05-06T16:00:00',
    batch_id: 'batch-1',
    strategy_config_snapshot: { entry_time: '09:30' },
  },
  trade: {
    status: 'CLOSED',
    bias: 'BULLISH',
    entry_debit: 12.5,
    exit_reason: 'TIME_EXIT',
    entry_reason_code: 'ENTRY',
    strategy_params_json: { target: 4000 },
    legs: [{ leg_side: 'LONG', option_type: 'CE', strike: 22400, expiry: '2026-05-12', entry_price: 10, exit_price: 20 }],
  },
  decisions: [
    { id: 'd1', timestamp: '2026-05-06T09:30:00', action: 'HOLD', spot_close: 22500, reason_code: 'WAIT', session_state: 'OBSERVING' },
    { id: 'd2', timestamp: '2026-05-06T09:31:00', action: 'ENTER', spot_close: 22510, reason_code: 'ENTRY', session_state: 'OPEN' },
  ],
  marks: [{ timestamp: '2026-05-06T09:31:00', estimated_net_mtm: 1000 }],
  explainability: {
    exit_reason: 'TIME_EXIT',
    no_trade_reason: null,
    action_counts: { ENTER: 1 },
  },
}

describe('ReplayAnalyzer', () => {
  beforeEach(() => {
    mocks.navigate.mockReset()
    mocks.getWorkbenchReplay.mockReset()
    mocks.getStrategyRunReplayCsv.mockReset()
    mocks.params = { kind: 'strategy_run', id: 'run-1' }
  })

  it('renders the v2 strategy replay analyzer with charts, legs, warnings, and timeline', async () => {
    mocks.getWorkbenchReplay.mockResolvedValue({ data: strategyPayload })

    render(<ReplayAnalyzer />)

    expect(await screen.findByText('NIFTY · 2026-05-06')).toBeInTheDocument()
    expect(screen.getByText('Data warnings:')).toBeInTheDocument()
    expect(screen.getByText('Execution summary')).toBeInTheDocument()
    expect(screen.getByText(/SELL NIFTY 22500 CE/)).toBeInTheDocument()
    expect(screen.getByText('NIFTY Spot + India VIX')).toBeInTheDocument()
    expect(screen.getByText('Decision timeline')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /back/i }))
    expect(mocks.navigate).toHaveBeenCalledWith('/workbench/history')
  })

  it('renders legacy analyzer and toggles event-only/full minute log', async () => {
    mocks.params = { kind: 'historical_session', id: 'session-1' }
    mocks.getWorkbenchReplay.mockResolvedValue({ data: legacyPayload })

    render(<ReplayAnalyzer />)

    expect(await screen.findByText('NIFTY · 2026-05-06')).toBeInTheDocument()
    expect(screen.getByText('Historical Session')).toBeInTheDocument()
    expect(screen.queryByText('WAIT')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /show full minute log/i }))
    expect(screen.getByText('WAIT')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /open detailed report/i })).toHaveAttribute('href', '/backtests/sessions/session-1')
    fireEvent.click(screen.getByRole('button', { name: /back/i }))
    expect(mocks.navigate).toHaveBeenCalledWith('/workbench/history/historical_batch/batch-1')
  })

  it('renders replay load failures', async () => {
    mocks.getWorkbenchReplay.mockRejectedValue({ response: { data: { detail: 'Replay unavailable' } } })

    render(<ReplayAnalyzer />)

    expect(await screen.findByText('Replay unavailable')).toBeInTheDocument()
  })
})
