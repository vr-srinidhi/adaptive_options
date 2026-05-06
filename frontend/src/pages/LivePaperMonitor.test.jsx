import { render, screen, waitFor } from '@testing-library/react'
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
})
