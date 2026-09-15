import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
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

import LivePaperMonitor, { PayoffTooltip, payoffYDomain, canPriceIntraday, visibleSeries, calcPayoffIntraday } from './LivePaperMonitor'

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


// ── Positions & MTM panel ────────────────────────────────────────────────────
// Fixture is the real 9:50 session from 8 Sep: straddle +12,285, four hedges
// -7,012, charges -498, net +4,775. The rows must reconcile to that.
function positionsSlot() {
  const sl = slot()
  sl.session = { id: 'sess-p', status: 'exited', atm_strike: 24050,
                 spot_latest: 24080, net_mtm_latest: null }
  sl.run = {
    lot_size: 75, approved_lots: 13,
    total_charges: 836.59, realized_net_pnl: -29782.84,
    legs: [
      { leg_index: 0, side: 'SELL', option_type: 'CE', strike: 24050, quantity: 975,
        entry_price: 74.95, exit_price: 109.90, gross_leg_pnl: -34076.25,
        entry_timestamp: '2026-08-31T09:50:03' },
      { leg_index: 1, side: 'SELL', option_type: 'PE', strike: 24050, quantity: 975,
        entry_price: 70.35, exit_price: 49.40, gross_leg_pnl: 20426.25,
        entry_timestamp: '2026-08-31T09:50:03' },
      { leg_index: 2, side: 'BUY', option_type: 'CE', strike: 24150, quantity: 975,
        entry_price: 66.85, exit_price: 53.65, gross_leg_pnl: -12870.00,
        entry_timestamp: '2026-08-31T11:56:15' },
      { leg_index: 3, side: 'BUY', option_type: 'PE', strike: 23950, quantity: 975,
        entry_price: 22.55, exit_price: 24.85, gross_leg_pnl: 2242.50,
        entry_timestamp: '2026-08-31T11:56:15' },
      { leg_index: 4, side: 'BUY', option_type: 'PE', strike: 23950, quantity: 375,
        entry_price: 37.60, exit_price: 24.85, gross_leg_pnl: -4781.25,
        entry_timestamp: '2026-08-31T10:20:11' },
      { leg_index: 5, side: 'BUY', option_type: 'CE', strike: 24150, quantity: 375,
        entry_price: 53.35, exit_price: 53.65, gross_leg_pnl: 112.50,
        entry_timestamp: '2026-08-31T11:54:36' },
    ],
  }
  return sl
}

describe('LivePaperMonitor positions panel', () => {
  let streams
  beforeEach(() => {
    streams = []
    // Keep the instances so a test can push a real MTM message through
    // onmessage and exercise the live-mark branch, not just the fallback.
    globalThis.EventSource = class {
      constructor() { this.readyState = 1; this.onmessage = null; streams.push(this) }
      addEventListener() {} removeEventListener() {}
      close() {}
    }
    Object.values(mocks).forEach(m => { if (typeof m === 'function') m.mockReset() })
    mocks.getLivePaperToday.mockResolvedValue({
      data: { slots: [positionsSlot()], token_status: 'valid' },
    })
    mocks.getLivePaperHistory.mockResolvedValue({ data: [] })
    mocks.getLiveDataSyncToday.mockResolvedValue({ data: null })
  })

  it('splits the P&L into straddle and adjustments with subtotals', async () => {
    render(<LivePaperMonitor />)
    expect(await screen.findByText('Positions & MTM')).toBeInTheDocument()
    const t = within(screen.getByText('Straddle subtotal').closest('table'))
    // −34,076.25 + 20,426.25 = −13,650
    expect(t.getByText('−₹13,650')).toBeInTheDocument()
    expect(t.getByText(/Adjustments · 4 legs/)).toBeInTheDocument()
    // −12,870 + 2,242.50 − 4,781.25 + 112.50 = −15,296.25
    expect(t.getByText('−₹15,296')).toBeInTheDocument()
  })

  it('reconciles gross, charges and net exactly', async () => {
    render(<LivePaperMonitor />)
    await screen.findByText('Positions & MTM')
    const t = within(screen.getByText('Straddle subtotal').closest('table'))
    // −13,650 + −15,296.25 = −28,946.25 ; −28,946.25 − 836.59 = −29,782.84
    expect(t.getByText('−₹28,946')).toBeInTheDocument()
    expect(t.getByText('−₹837')).toBeInTheDocument()
    expect(t.getByText('−₹29,783')).toBeInTheDocument()
  })

  it('shows each leg at its own quantity and entry time', async () => {
    render(<LivePaperMonitor />)
    await screen.findByText('Positions & MTM')
    const t = within(screen.getByText('Straddle subtotal').closest('table'))
    expect(t.getByText('10:20:11')).toBeInTheDocument()   // first hedge
    expect(t.getAllByText('375').length).toBe(2)          // hedges, not 975
    expect(t.getByText('−₹4,781')).toBeInTheDocument()
  })

  it('labels lock wings and hedges distinctly', async () => {
    render(<LivePaperMonitor />)
    await screen.findByText('Positions & MTM')
    const t = within(screen.getByText('Straddle subtotal').closest('table'))
    expect(t.getAllByText(/lock wing/).length).toBe(2)
    expect(t.getByText(/hedge 1/)).toBeInTheDocument()
    expect(t.getByText(/hedge 2/)).toBeInTheDocument()
  })

  it('omits the adjustments group entirely when there are none', async () => {
    const plain = positionsSlot()
    plain.run.legs = plain.run.legs.slice(0, 2)
    plain.run.total_charges = 200
    plain.run.realized_net_pnl = -13850
    mocks.getLivePaperToday.mockResolvedValue({
      data: { slots: [plain], token_status: 'valid' },
    })
    render(<LivePaperMonitor />)
    await screen.findByText('Positions & MTM')
    expect(screen.getByText('Straddle subtotal')).toBeInTheDocument()
    expect(screen.queryByText(/Adjustments ·/)).not.toBeInTheDocument()
    expect(screen.queryByText('Adjustments subtotal')).not.toBeInTheDocument()
  })

  it('uses live marks once streaming starts, keeping entry times and charges', async () => {
    // The stream only opens for an active session, and total_charges is not
    // written until finalisation — so this is the shape the panel really sees
    // while a session is running.
    const live = positionsSlot()
    live.session.status = 'entered'
    live.run.total_charges = null
    live.run.realized_net_pnl = null
    mocks.getLivePaperToday.mockResolvedValue({
      data: { slots: [live], token_status: 'valid' },
    })
    render(<LivePaperMonitor />)
    await screen.findByText('Positions & MTM')
    await waitFor(() => expect(streams.length).toBeGreaterThan(0))

    // Shape matches what the engine broadcasts: a mark per open leg plus the
    // charges accrued so far, since run.total_charges is null until finalised.
    await act(async () => {
      streams[0].onmessage({ data: JSON.stringify({
        timestamp: '2026-08-31T12:30:00', spot: 24020,
        net_mtm: -20500, gross_mtm: -19700, charges: 800,
        legs: [
          { leg_index: 0, side: 'SELL', option_type: 'CE', strike: 24050, quantity: 975,
            entry_price: 74.95, current_price: 90.00, pnl: -14673.75,
            entry_timestamp: '2026-08-31T09:50:03' },
          { leg_index: 1, side: 'SELL', option_type: 'PE', strike: 24050, quantity: 975,
            entry_price: 70.35, current_price: 65.00, pnl: 5216.25,
            entry_timestamp: '2026-08-31T09:50:03' },
          { leg_index: 4, side: 'BUY', option_type: 'PE', strike: 23950, quantity: 375,
            entry_price: 37.60, current_price: 30.00, pnl: -2850.00,
            entry_timestamp: '2026-08-31T10:20:11' },
        ],
        type: 'MTM',
      }) })
    })

    const t = within(screen.getByText('Straddle subtotal').closest('table'))
    // Live marks replaced the six persisted legs with three.
    expect(t.queryByText(/Adjustments · 4 legs/)).not.toBeInTheDocument()
    expect(t.getByText(/Adjustments · 1 leg/)).toBeInTheDocument()
    // Entry times survive the switch to live marks — the P2 regression.
    expect(t.getByText('10:20:11')).toBeInTheDocument()
    expect(t.getAllByText('09:50:03').length).toBe(2)   // both straddle legs
    // Charges come from the tick, not from the unfinalised run.
    expect(t.getByText('−₹800')).toBeInTheDocument()
    // −14,673.75 + 5,216.25 = −9,457.50 straddle ; −2,850 adjustments
    expect(t.getByText('−₹9,458')).toBeInTheDocument()
    // The lone adjustment leg and the adjustments subtotal agree.
    expect(t.getAllByText('−₹2,850').length).toBe(2)
  })
})

// ── Intraday payoff line ─────────────────────────────────────────────────────
// Fixture is the real 10 Sep 9:50 position, snapshotted at 11:29: short 23,450
// straddle, 13 lots, 5.2 days to expiry. The IVs are the ones backed out of the
// live prices at that moment, which is what the engine now ships on the tick.
//
// Why this position: the two curves disagree enormously five days out. Today's
// break-evens sit 163 points apart, at expiry 460 -- so a test that asserts one
// set cannot accidentally pass while the code plots the other.
const INTRADAY_TICK = {
  type: 'MTM',
  timestamp: '2026-09-10T11:29:00',
  spot: 23429.05,
  // Reconciles: the two leg marks below sum to +9,798.75 gross; charges 480
  // leave 9,318.75 net. The Today curve at the current spot must reproduce the
  // gross figure, which is what keeps the Live reference on the same basis.
  gross_mtm: 9798.75,
  charges: 480,
  net_mtm: 9318.75,
  t_years: 0.01416,
  legs: [
    { leg_index: 0, side: 'SELL', option_type: 'CE', strike: 23450, quantity: 975,
      entry_price: 133.40, current_price: 123.55, pnl: 9603.75, iv: 0.1108,
      entry_timestamp: '2026-09-10T09:50:02' },
    { leg_index: 1, side: 'SELL', option_type: 'PE', strike: 23450, quantity: 975,
      entry_price: 96.20, current_price: 96.00, pnl: 195.00, iv: 0.0866,
      entry_timestamp: '2026-09-10T09:50:02' },
  ],
}

function intradaySlot() {
  const sl = slot()
  sl.session = { id: 'sess-i', status: 'entered', atm_strike: 23450,
                 spot_latest: 23429.05, net_mtm_latest: 8000 }
  sl.run = {
    lot_size: 75, approved_lots: 13, total_charges: null, realized_net_pnl: null,
    legs: [
      { leg_index: 0, side: 'SELL', option_type: 'CE', strike: 23450, quantity: 975, entry_price: 133.40 },
      { leg_index: 1, side: 'SELL', option_type: 'PE', strike: 23450, quantity: 975, entry_price: 96.20 },
    ],
  }
  return sl
}

describe('LivePaperMonitor intraday payoff line', () => {
  let streams
  const push = async (tick) => {
    await waitFor(() => expect(streams.length).toBeGreaterThan(0))
    await act(async () => { streams[0].onmessage({ data: JSON.stringify(tick) }) })
  }

  beforeEach(() => {
    streams = []
    globalThis.EventSource = class {
      constructor() { this.readyState = 1; this.onmessage = null; streams.push(this) }
      addEventListener() {} removeEventListener() {}
      close() {}
    }
    Object.values(mocks).forEach(m => { if (typeof m === 'function') m.mockReset() })
    mocks.getLivePaperToday.mockResolvedValue({
      data: { slots: [intradaySlot()], token_status: 'valid' },
    })
    mocks.getLivePaperHistory.mockResolvedValue({ data: [] })
    mocks.getLiveDataSyncToday.mockResolvedValue({ data: null })
  })

  it('quotes today’s break-evens, not the far wider ones at expiry', async () => {
    render(<LivePaperMonitor />)
    await screen.findByText('Positions & MTM')
    await push(INTRADAY_TICK)

    // Priced off each leg's own IV with 5.2 days left: 163 points apart.
    expect(screen.getByText(/BE 23,344 \(−85 from now\)/)).toBeInTheDocument()
    expect(screen.getByText(/BE 23,510 \(\+81 from now\)/)).toBeInTheDocument()
    // The expiry numbers are nearly three times wider. Seeing them here would
    // mean the chart is still quoting the curve the trader is not trading.
    expect(screen.queryByText(/BE 23,220/)).not.toBeInTheDocument()
    expect(screen.queryByText(/BE 23,680/)).not.toBeInTheDocument()
  })

  it('puts the Live reference on the curve\u2019s basis \u2014 gross, not net', async () => {
    // The curves subtract no charges, so a Live line drawn from net_mtm sits
    // below a curve it can never touch. The fixture separates the two by the
    // 480 of charges precisely so this cannot pass on the wrong one.
    render(<LivePaperMonitor />)
    await screen.findByText('Positions & MTM')
    await push(INTRADAY_TICK)

    expect(screen.getByText(/Live \+\u20b99,799 gross/)).toBeInTheDocument()
    expect(screen.queryByText(/Live \+\u20b99,319/)).not.toBeInTheDocument()
    expect(screen.getByText('gross of charges')).toBeInTheDocument()
  })

  it('scales the peak to what the position is worth today, not the full premium', async () => {
    render(<LivePaperMonitor />)
    await screen.findByText('Positions & MTM')
    await push(INTRADAY_TICK)

    // At the strike today: +₹9,035. Held to expiry: +₹2,23,860 -- the entire
    // premium, and 25x larger, which is what flattens the intraday line if the
    // two ever share a y-axis.
    expect(screen.getByText(/Peak 23,430 · \+₹9,789/)).toBeInTheDocument()
    expect(screen.queryByText(/₹2,23,860/)).not.toBeInTheDocument()
    expect(screen.getByText(/5\.2d left · IV held flat/)).toBeInTheDocument()
  })

  it('switches the whole computation back to expiry when Today is turned off', async () => {
    render(<LivePaperMonitor />)
    await screen.findByText('Positions & MTM')
    await push(INTRADAY_TICK)

    fireEvent.click(screen.getByRole('button', { name: 'Today' }))

    // Not just the button state: the chips are recomputed from the expiry
    // series, so the break-evens widen and the peak becomes the full premium.
    expect(screen.getByRole('button', { name: 'Today' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.getByText(/BE 23,220 \(−209 from now\)/)).toBeInTheDocument()
    expect(screen.getByText(/BE 23,680 \(\+251 from now\)/)).toBeInTheDocument()
    expect(screen.getByText(/Peak 23,450 · \+₹2,23,860/)).toBeInTheDocument()
    expect(screen.queryByText(/BE 23,344/)).not.toBeInTheDocument()
  })

  it('shows the expiry break-evens alongside today’s once that line is added', async () => {
    render(<LivePaperMonitor />)
    await screen.findByText('Positions & MTM')
    await push(INTRADAY_TICK)

    expect(screen.queryByText(/At expiry BE/)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'At expiry' }))

    // Both sets now, and today's stay the ones marked as the live break-evens.
    expect(screen.getByText('At expiry BE 23,220 / 23,680')).toBeInTheDocument()
    expect(screen.getByText(/BE 23,344 \(−85 from now\)/)).toBeInTheDocument()
    expect(screen.getByText(/Peak 23,430 · \+₹9,789/)).toBeInTheDocument()
  })

  it('drops the intraday line rather than draw a book missing one leg’s IV', async () => {
    // A hedge whose IV could not be backed out. Pricing the straddle alone
    // would draw unlimited downside that the hedge in fact caps.
    render(<LivePaperMonitor />)
    await screen.findByText('Positions & MTM')
    await push({ ...INTRADAY_TICK, legs: [...INTRADAY_TICK.legs,
      { leg_index: 4, side: 'BUY', option_type: 'PE', strike: 23350, quantity: 375,
        entry_price: 40, current_price: 38, pnl: -750, iv: null,
        entry_timestamp: '2026-09-10T10:40:00' }] })

    expect(screen.getByText('Payoff at Expiry')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Today' })).not.toBeInTheDocument()
    expect(screen.queryByText(/BE 23,344/)).not.toBeInTheDocument()
    // The expiry curve still prices the whole book, hedge included -- 23,164 /
    // 23,664 rather than the 23,220 / 23,680 of the straddle alone.
    expect(screen.getByText(/BE 23,164/)).toBeInTheDocument()
    expect(screen.getByText(/BE 23,664/)).toBeInTheDocument()
    expect(screen.getByText(/Peak 23,450 · \+₹2,08,860/)).toBeInTheDocument()
  })

  it('prices each leg at its own size, not the full straddle quantity', async () => {
    // A half-size delta hedge: 375 long puts against 975 short. Priced at its
    // own size the downside stays open, so the curve has two break-evens; blown
    // up to the full 975 the hedge would fully cover the short puts and the
    // downside would go flat, leaving only one. The two are impossible to
    // confuse, so this cannot pass while the quantity is being ignored.
    render(<LivePaperMonitor />)
    await screen.findByText('Positions & MTM')
    await push({ ...INTRADAY_TICK, legs: [...INTRADAY_TICK.legs,
      { leg_index: 4, side: 'BUY', option_type: 'PE', strike: 23350, quantity: 375,
        entry_price: 52.30, current_price: 50.10, pnl: -825, iv: 0.1240,
        entry_timestamp: '2026-09-10T10:40:00' }] })

    expect(screen.getByText(/BE 23,202 \(−227 from now\)/)).toBeInTheDocument()
    expect(screen.getByText(/BE 23,524 \(\+95 from now\)/)).toBeInTheDocument()
    expect(screen.getByText(/Peak 23,370 · \+₹29,708/)).toBeInTheDocument()
    // What the full-size misreading would have produced.
    expect(screen.queryByText(/BE 23,534/)).not.toBeInTheDocument()
    expect(screen.queryByText(/₹95,559/)).not.toBeInTheDocument()

    // Both curves must describe the SAME position. The hedge arrives only on
    // the marks, so if At expiry still read the event-built legs it would draw
    // the bare straddle (23,220 / 23,680) against a Today line that includes
    // the hedge -- two different books on one chart.
    expect(screen.getByRole('button', { name: 'With adjustments' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'At expiry' }))
    expect(screen.getByText('At expiry BE 23,172 / 23,659')).toBeInTheDocument()
    expect(screen.queryByText(/23,220/)).not.toBeInTheDocument()
    expect(screen.queryByText(/23,680/)).not.toBeInTheDocument()
  })

  it('keeps the expiry-only chart when no live mark carries an IV', async () => {
    // A finished session, or a reload before the stream reconnects. There is
    // nothing to reprice from, so the chart must stay exactly as it was rather
    // than invent a volatility.
    render(<LivePaperMonitor />)
    await screen.findByText('Positions & MTM')
    await push({ ...INTRADAY_TICK, t_years: null,
                 legs: INTRADAY_TICK.legs.map(l => ({ ...l, iv: null })) })

    expect(screen.getByText('Payoff at Expiry')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Today' })).not.toBeInTheDocument()
    expect(screen.getByText(/BE 23,220 \(−209 from now\)/)).toBeInTheDocument()
    expect(screen.getByText(/Peak 23,450 · \+₹2,23,860/)).toBeInTheDocument()
  })
})

describe('PayoffTooltip', () => {
  const row = (dataKey, name, value, color) => ({ dataKey, name, value, color })

  it('reads out every visible series at the hovered spot', () => {
    render(<PayoffTooltip active label={23500} currentSpot={23429.05} payload={[
      row('today', 'Today', -4820.5, '#22d3ee'),
      row('pnl', 'At expiry', 198765, '#818cf8'),
    ]} />)

    expect(screen.getByText('23,500')).toBeInTheDocument()
    // The distance is the point of sweeping across: how far NIFTY can run.
    expect(screen.getByText('+71 from now')).toBeInTheDocument()
    expect(screen.getByText('−₹4,821')).toBeInTheDocument()
    expect(screen.getByText('+₹1,98,765')).toBeInTheDocument()
    expect(screen.getByText(/Today/)).toBeInTheDocument()
    expect(screen.getByText(/At expiry/)).toBeInTheDocument()
  })

  it('shows the distance below the current spot as negative', () => {
    render(<PayoffTooltip active label={23300} currentSpot={23429.05} payload={[
      row('today', 'Today', -12000, '#22d3ee'),
    ]} />)
    expect(screen.getByText('−129 from now')).toBeInTheDocument()
  })

  it('renders nothing when inactive, empty, or carrying only null values', () => {
    const { container: a } = render(<PayoffTooltip active={false} label={23500} payload={[row('today', 'Today', 1, '#000')]} />)
    expect(a).toBeEmptyDOMElement()
    const { container: b } = render(<PayoffTooltip active label={23500} payload={[]} />)
    expect(b).toBeEmptyDOMElement()
    // A hover past the end of a series hands Recharts a null value; a row
    // reading "+₹0" there would be a lie about the position.
    const { container: c } = render(<PayoffTooltip active label={23500} payload={[row('today', 'Today', null, '#000')]} />)
    expect(c).toBeEmptyDOMElement()
  })

  it('omits the distance when there is no live spot to compare against', () => {
    render(<PayoffTooltip active label={23500} currentSpot={null} payload={[
      row('pnl', 'At expiry', 5000, '#818cf8'),
    ]} />)
    expect(screen.getByText('23,500')).toBeInTheDocument()
    expect(screen.queryByText(/from now/)).not.toBeInTheDocument()
  })
})

describe('payoffYDomain', () => {
  // The real 10 Sep shape, sampled coarsely: today peaks at +9,035 while the
  // expiry curve peaks at +2,23,860 on the same spots.
  const data = [
    { spot: 23200, today: -88480, pnl: -20140 },
    { spot: 23450, today: 9035,   pnl: 223860 },
    { spot: 23700, today: -85000, pnl: -20140 },
  ]

  it('scales to the series on screen, not the ones toggled off', () => {
    const [lo, hi] = payoffYDomain(data, { todayOn: true, expiryOn: false })
    // The ceiling tracks today's own peak (+9,035) plus padding, nowhere near
    // the 2.24L the expiry curve reaches on the same spot.
    expect(hi).toBeLessThan(9035 * 3)
    expect(lo).toBeLessThanOrEqual(-88480)

    // Adding the expiry curve lifts the ceiling more than tenfold. That is the
    // whole reason it is opt-in rather than always drawn.
    const [, bothHi] = payoffYDomain(data, { todayOn: true, expiryOn: true })
    expect(bothHi).toBeGreaterThan(hi * 10)
  })

  it('keeps today’s profitable zone readable when it is the only series', () => {
    const height = ([lo, hi]) => hi - lo
    const fraction = toggles => 9035 / height(payoffYDomain(data, toggles))

    // Alone, today's entire profit region is a usable slice of the chart.
    expect(fraction({ todayOn: true, expiryOn: false })).toBeGreaterThan(0.05)
    // Folded in with expiry it collapses to a sliver -- visually flat, which is
    // exactly what a trader must not be shown as "safe".
    expect(fraction({ todayOn: true, expiryOn: true })).toBeLessThan(0.03)
  })

  it('ignores null samples and still returns a usable range when all are null', () => {
    expect(payoffYDomain([{ spot: 1, today: null }], { todayOn: true, expiryOn: false })).toEqual([-5000, 5000])
    const [lo, hi] = payoffYDomain([{ spot: 1, today: null }, { spot: 2, today: 4000 }], { todayOn: true, expiryOn: false })
    expect(hi).toBeGreaterThanOrEqual(4000)
    expect(Number.isFinite(lo)).toBe(true)
  })
})

describe('canPriceIntraday', () => {
  const straddle = [
    { side: 'SELL', option_type: 'CE', strike: 23450, entry_price: 133.40, iv: 0.1108 },
    { side: 'SELL', option_type: 'PE', strike: 23450, entry_price: 96.20,  iv: 0.0866 },
  ]

  it('accepts a book where every leg can be priced', () => {
    expect(canPriceIntraday(straddle, 0.01416)).toBe(true)
  })

  it('refuses a book where only some legs carry an IV', () => {
    // The dangerous case: the straddle prices but the hedge protecting it does
    // not. Dropping that leg would draw a curve with more downside than the
    // trader is actually carrying.
    const withHedge = [...straddle,
      { side: 'BUY', option_type: 'PE', strike: 23350, entry_price: 40, iv: null }]
    expect(canPriceIntraday(withHedge, 0.01416)).toBe(false)
  })

  it('refuses an empty book or a missing time to expiry', () => {
    expect(canPriceIntraday([], 0.01416)).toBe(false)
    expect(canPriceIntraday(straddle, null)).toBe(false)
    expect(canPriceIntraday(straddle, 0)).toBe(false)
  })
})

describe('visibleSeries', () => {
  it('leads with today when it can be priced', () => {
    expect(visibleSeries({ canShowToday: true, showToday: true, showExpiry: false }))
      .toEqual({ todayOn: true, expiryOn: false })
  })

  it('never leaves the chart blank', () => {
    // Both switched off, and today unavailable — in every case something is
    // still drawn, because a payoff panel with no curve tells a trader nothing.
    expect(visibleSeries({ canShowToday: true, showToday: false, showExpiry: false }))
      .toEqual({ todayOn: false, expiryOn: true })
    expect(visibleSeries({ canShowToday: false, showToday: true, showExpiry: false }))
      .toEqual({ todayOn: false, expiryOn: true })
    expect(visibleSeries({ canShowToday: false, showToday: false, showExpiry: false }))
      .toEqual({ todayOn: false, expiryOn: true })
  })

  it('shows both when expiry is added to today', () => {
    expect(visibleSeries({ canShowToday: true, showToday: true, showExpiry: true }))
      .toEqual({ todayOn: true, expiryOn: true })
  })
})

describe('payoff basis', () => {
  // The curves sum gross per-leg P&L and subtract nothing, so the chart's Live
  // reference has to be gross_mtm. Pointing it at net_mtm put the line below a
  // curve it could never touch, and quoted break-evens on a basis the curve did
  // not use.
  const legs = [
    { side: 'SELL', option_type: 'CE', strike: 23450, quantity: 975, entry_price: 133.40, iv: 0.1108 },
    { side: 'SELL', option_type: 'PE', strike: 23450, quantity: 975, entry_price: 96.20,  iv: 0.0866 },
  ]

  it('reproduces gross MTM at the current spot', () => {
    // Each leg's IV was inverted from its own live price, so Black-Scholes at
    // that IV returns that price and the curve collapses to the gross sum.
    // Tolerance covers the 6-decimal rounding the engine applies to iv.
    const v = calcPayoffIntraday(legs, 23429.05, 975, 0.01416)
    expect(v).toBeCloseTo(9798.75, -1)
    expect(Math.abs(v - 9798.75)).toBeLessThan(10)
  })

  it('is gross, not net — it must not silently carry charges', () => {
    // The 11 Sep fixture books 480 of charges. If the curve ever starts
    // subtracting them, this pins the change rather than letting the Live line
    // and the curve drift onto different bases again.
    const v = calcPayoffIntraday(legs, 23429.05, 975, 0.01416)
    expect(v).toBeGreaterThan(9318.75 + 400)
  })
})
