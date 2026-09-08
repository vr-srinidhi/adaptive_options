import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  accessToken: 'access-token',
  createLivePaperConfig: vi.fn(),
  updateLivePaperConfigSlot: vi.fn(),
  deleteLivePaperConfigSlot: vi.fn(),
  getLivePaperToday: vi.fn(),
  getLivePaperHistory: vi.fn(),
  getLiveDataSyncToday: vi.fn(),
  triggerLiveDataSyncToday: vi.fn(),
  startLivePaper: vi.fn(),
  stopLivePaper: vi.fn(),
  zerodhaSession: vi.fn(),
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
  }
})

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ accessToken: mocks.accessToken }),
}))

vi.mock('../api/index.js', () => ({
  createLivePaperConfig: mocks.createLivePaperConfig,
  updateLivePaperConfigSlot: mocks.updateLivePaperConfigSlot,
  deleteLivePaperConfigSlot: mocks.deleteLivePaperConfigSlot,
  getLivePaperToday: mocks.getLivePaperToday,
  getLivePaperHistory: mocks.getLivePaperHistory,
  getLiveDataSyncToday: mocks.getLiveDataSyncToday,
  triggerLiveDataSyncToday: mocks.triggerLiveDataSyncToday,
  startLivePaper: mocks.startLivePaper,
  stopLivePaper: mocks.stopLivePaper,
  zerodhaSession: mocks.zerodhaSession,
}))

import LivePaperMonitor from './LivePaperMonitor'

function slot() {
  return {
    config: {
      id: 'config-1',
      label: 'Default',
      strategy_id: 'short_straddle_dual_lock',
      instrument: 'NIFTY',
      capital: 2500000,
      entry_time: '10:15',
      params: {},
      enabled: true,
      execution_mode: 'paper',
    },
    session: null,
    mtm_series: [],
    events: [],
    run: null,
  }
}

describe('LivePaperMonitor data sync status', () => {
  beforeEach(() => {
    Object.values(mocks).forEach(mock => {
      if (typeof mock === 'function') mock.mockReset()
    })
    mocks.getLivePaperToday.mockResolvedValue({
      data: { slots: [slot()], token_status: 'valid' },
    })
    mocks.getLivePaperHistory.mockResolvedValue({ data: [] })
    mocks.triggerLiveDataSyncToday.mockResolvedValue({ data: { detail: 'started' } })
  })

  it('renders successful warehouse sync rows and expiries', async () => {
    mocks.getLiveDataSyncToday.mockResolvedValue({
      data: {
        trade_date: '2026-05-06',
        scheduled_time: '16:00 IST',
        status: 'SUCCESS',
        token_status: 'VALID',
        backtest_ready: true,
        last_attempt_at: '2026-05-06T16:00:12+05:30',
        completed_at: '2026-05-06T16:18:12+05:30',
        rows: { spot: 376, vix: 376, futures: 376, options: 12000 },
        option_contracts: 96,
        expiries: ['2026-05-07', '2026-05-14'],
        notes: null,
        error_message: null,
      },
    })

    render(<LivePaperMonitor />)

    await screen.findByText('Data Warehouse Sync')
    expect(screen.getByText('Success')).toBeInTheDocument()
    expect(screen.getByText(/S 376 .* V 376 .* F 376 .* O 12000/)).toBeInTheDocument()
    expect(screen.getByText(/2026-05-07, 2026-05-14/)).toBeInTheDocument()
    expect(screen.getByText('Yes')).toBeInTheDocument()
  })

  it('renders skipped token state without backtest readiness', async () => {
    mocks.getLiveDataSyncToday.mockResolvedValue({
      data: {
        trade_date: '2026-05-06',
        scheduled_time: '16:00 IST',
        status: 'SKIPPED_TOKEN_MISSING',
        token_status: 'MISSING',
        backtest_ready: false,
        last_attempt_at: null,
        completed_at: null,
        rows: { spot: 0, vix: 0, futures: 0, options: 0 },
        option_contracts: 0,
        expiries: [],
        notes: 'No Zerodha token is available for live data sync.',
        error_message: null,
      },
    })

    render(<LivePaperMonitor />)

    await waitFor(() => {
      expect(screen.getByText('Token missing')).toBeInTheDocument()
    })
    expect(screen.getByText('Missing')).toBeInTheDocument()
    expect(screen.getByText('No')).toBeInTheDocument()
    expect(screen.getByText(/No Zerodha token is available/)).toBeInTheDocument()
  })

  it('starts a manual missing-only warehouse sync from the status card', async () => {
    mocks.getLiveDataSyncToday.mockResolvedValue({
      data: {
        trade_date: '2026-05-06',
        scheduled_time: '16:00 IST',
        status: 'PARTIAL_SUCCESS',
        token_status: 'VALID',
        backtest_ready: true,
        last_attempt_at: '2026-05-06T16:00:12+05:30',
        completed_at: '2026-05-06T16:18:12+05:30',
        rows: { spot: 375, vix: 375, futures: 375, options: 151279 },
        option_contracts: 486,
        expiries: ['2026-05-12'],
        notes: '54/486 option contracts had no data',
        error_message: null,
      },
    })

    render(<LivePaperMonitor />)

    const button = await screen.findByRole('button', { name: 'Sync missing' })
    fireEvent.click(button)

    expect(mocks.triggerLiveDataSyncToday).toHaveBeenCalledTimes(1)
    expect(await screen.findByText('Syncing…')).toBeInTheDocument()
  })
})

// ── Payoff chart ─────────────────────────────────────────────────────────────
// The break-even and peak chips render as plain DOM outside the SVG, so they
// pin the computed numbers without depending on Recharts laying out in jsdom.
// Fixture is the real 7 Sep 9:50 position: three delta hedges bought 975 of the
// 23750 PE in total, exactly matching the 975 short puts, so the payoff goes
// flat below that strike.
function payoffSlot() {
  const s = slot()
  s.session = { id: 'sess-1', status: 'entered', atm_strike: 23850, spot_latest: 23779 }
  s.run = {
    lot_size: 75,
    approved_lots: 13,
    legs: [
      { side: 'SELL', option_type: 'CE', strike: 23850, quantity: 975, entry_price: 71.70 },
      { side: 'SELL', option_type: 'PE', strike: 23850, quantity: 975, entry_price: 71.00 },
      { side: 'BUY',  option_type: 'PE', strike: 23750, quantity: 375, entry_price: 37.15 },
      { side: 'BUY',  option_type: 'PE', strike: 23750, quantity: 300, entry_price: 47.45 },
      { side: 'BUY',  option_type: 'PE', strike: 23750, quantity: 300, entry_price: 46.05 },
    ],
  }
  return s
}

describe('LivePaperMonitor payoff chart', () => {
  beforeEach(() => {
    // An entered session opens an SSE stream on mount, and jsdom has no
    // EventSource. Stub it so the payoff panel can be rendered at all.
    globalThis.EventSource = class {
      constructor() { this.readyState = 0 }
      addEventListener() {}
      removeEventListener() {}
      close() {}
    }
    Object.values(mocks).forEach(mock => {
      if (typeof mock === 'function') mock.mockReset()
    })
    mocks.getLivePaperToday.mockResolvedValue({
      data: { slots: [payoffSlot()], token_status: 'valid' },
    })
    mocks.getLivePaperHistory.mockResolvedValue({ data: [] })
    mocks.getLiveDataSyncToday.mockResolvedValue({ data: null })
  })

  it('shows break-evens as full numbers with distance from spot, not rounded to 1k', async () => {
    render(<LivePaperMonitor />)
    // 23,750.36 and 23,949.6 -> the two strikes that bound the profitable zone.
    expect(await screen.findByText(/BE 23,750/)).toBeInTheDocument()
    expect(screen.getByText(/BE 23,950/)).toBeInTheDocument()
    // Distance from the live spot of 23,779 is what a trader acts on.
    expect(screen.getByText(/BE 23,750 \(−29 from now\)/)).toBeInTheDocument()
    expect(screen.getByText(/BE 23,950 \(\+171 from now\)/)).toBeInTheDocument()
    // The old formatter collapsed both of these to "24k".
    expect(screen.queryByText(/BE 24k/)).not.toBeInTheDocument()
  })

  it('marks the peak with its spot and full-precision P&L', async () => {
    render(<LivePaperMonitor />)
    expect(await screen.findByText(/Peak 23,850 · \+₹97,151/)).toBeInTheDocument()
  })

  it('offers range presets and defaults to the mid window', async () => {
    render(<LivePaperMonitor />)
    const mid = await screen.findByRole('button', { name: '±750' })
    expect(mid).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '±250' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByRole('button', { name: 'Wide' })).toHaveAttribute('aria-pressed', 'false')
  })

  it('actually applies the selected range, not just the button state', async () => {
    // A wide straddle: 400 points of credit puts the break-evens at 23,450 and
    // 24,250 -- inside the +/-750 window but outside +/-250. If the selected
    // range were ignored they would stay on screen after zooming in.
    const wide = payoffSlot()
    wide.run.legs = [
      { side: 'SELL', option_type: 'CE', strike: 23850, quantity: 975, entry_price: 200 },
      { side: 'SELL', option_type: 'PE', strike: 23850, quantity: 975, entry_price: 200 },
    ]
    mocks.getLivePaperToday.mockResolvedValue({
      data: { slots: [wide], token_status: 'valid' },
    })
    render(<LivePaperMonitor />)
    expect(await screen.findByText(/BE 23,450/)).toBeInTheDocument()
    expect(screen.getByText(/BE 24,250/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '±250' }))
    expect(screen.queryByText(/BE 23,450/)).not.toBeInTheDocument()
    expect(screen.queryByText(/BE 24,250/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Wide' }))
    expect(screen.getByText(/BE 23,450/)).toBeInTheDocument()
  })

  it('keeps a break-even that falls exactly on the right edge of the window', async () => {
    // ₹125 per side at 23,850 puts the break-evens exactly on the ±250
    // boundaries. Scanning sample pairs and testing only the left one never
    // examined the final sample, so the upper break-even vanished.
    const edge = payoffSlot()
    edge.run.legs = [
      { side: 'SELL', option_type: 'CE', strike: 23850, quantity: 975, entry_price: 125 },
      { side: 'SELL', option_type: 'PE', strike: 23850, quantity: 975, entry_price: 125 },
    ]
    mocks.getLivePaperToday.mockResolvedValue({
      data: { slots: [edge], token_status: 'valid' },
    })
    render(<LivePaperMonitor />)
    fireEvent.click(await screen.findByRole('button', { name: '±250' }))
    expect(screen.getByText(/BE 23,600/)).toBeInTheDocument()   // left edge
    expect(screen.getByText(/BE 24,100/)).toBeInTheDocument()   // right edge
  })

  it('keeps the break-evens visible after zooming in', async () => {
    render(<LivePaperMonitor />)
    fireEvent.click(await screen.findByRole('button', { name: '±250' }))
    expect(screen.getByRole('button', { name: '±250' })).toHaveAttribute('aria-pressed', 'true')
    // ±250 still spans both break-evens, so neither should disappear.
    expect(screen.getByText(/BE 23,750/)).toBeInTheDocument()
    expect(screen.getByText(/BE 23,950/)).toBeInTheDocument()
  })
})
