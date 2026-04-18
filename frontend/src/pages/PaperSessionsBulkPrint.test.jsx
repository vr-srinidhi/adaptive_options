import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  exportPaperSessionsBundle: vi.fn(),
  print: vi.fn(),
}))

vi.mock('../api', () => ({
  exportPaperSessionsBundle: mocks.exportPaperSessionsBundle,
}))

vi.mock('../components/PnlChart', () => ({
  PnlProgressionChart: ({ data }) => <div data-testid="pnl-chart">Points: {data.length}</div>,
}))

import PaperSessionsBulkPrint from './PaperSessionsBulkPrint'

describe('PaperSessionsBulkPrint page', () => {
  beforeEach(() => {
    mocks.exportPaperSessionsBundle.mockReset()
    mocks.print.mockReset()
    window.print = mocks.print
  })

  it('loads the selected bundles and triggers print', async () => {
    mocks.exportPaperSessionsBundle.mockResolvedValueOnce({
      data: {
        sessions: [
          {
            session: {
              id: 'session-1',
              session_date: '2026-04-11',
              instrument: 'NIFTY',
              capital: 2500000,
              status: 'COMPLETED',
              decision_count: 20,
              action_summary: { ENTER: 1 },
            },
            trade: {
              id: 'trade-1',
              bias: 'BULLISH',
              option_type: 'CE',
              long_strike: 22100,
              short_strike: 22150,
              expiry: '2026-04-16',
              lot_size: 75,
              approved_lots: 22,
              entry_debit: 28.3,
              total_max_loss: 46695,
              target_profit: 12500,
              realized_gross_pnl: 24750,
              realized_net_pnl: 24400,
              exit_reason: 'EXIT_TARGET',
              entry_time: '2026-04-11T09:31:00',
              exit_time: '2026-04-11T09:33:00',
              selection_method: 'ranked_candidate_selection_v1',
              selected_candidate_rank: 1,
              selected_candidate_score: 0.8421,
              legs: [],
            },
            decisions: [
              {
                id: 'decision-enter',
                timestamp: '2026-04-11T09:31:00',
                spot_close: 22130,
                opening_range_high: 22100,
                opening_range_low: 21900,
                trade_state: 'NO_OPEN_TRADE',
                action: 'ENTER',
                reason_code: 'ENTER_TRADE',
                reason_text: 'Entered trade',
                candidate_structure: null,
              },
            ],
            marks: [{ timestamp: '2026-04-11T09:32:00', total_mtm: 12000 }],
            candle_series: [],
          },
        ],
      },
    })

    render(
      <MemoryRouter initialEntries={['/paper/sessions/print?ids=session-1']}>
        <Routes>
          <Route path="/paper/sessions/print" element={<PaperSessionsBulkPrint />} />
        </Routes>
      </MemoryRouter>
    )

    await waitFor(() => {
      expect(screen.getByText(/2026-04-11 — Nifty 50 · ORB Replay/i)).toBeInTheDocument()
    })

    await waitFor(() => {
      expect(mocks.print).toHaveBeenCalled()
    })
  })
})
