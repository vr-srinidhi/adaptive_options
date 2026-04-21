import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import StrategyCatalog from './StrategyCatalog'

const { getWorkbenchStrategies } = vi.hoisted(() => ({
  getWorkbenchStrategies: vi.fn(),
}))

vi.mock('../api', () => ({
  getWorkbenchStrategies,
}))

const sampleStrategies = [
  {
    id: 'orb_intraday_spread',
    name: 'Opening Range Spread',
    bias: 'adaptive',
    status: 'available',
    playbook: 'Current live strategy',
    description: 'Replayable right now.',
    chips: ['Live'],
    notes: ['Fully executable'],
  },
  {
    id: 'buy_call',
    name: 'Buy Call',
    bias: 'bullish',
    status: 'planned',
    playbook: 'Long delta',
    description: 'Coming next.',
    chips: ['Planned'],
    notes: [],
  },
]

function renderCatalog() {
  return render(
    <MemoryRouter initialEntries={['/workbench/strategies']}>
      <Routes>
        <Route path="/workbench/strategies" element={<StrategyCatalog />} />
        <Route path="/workbench/run" element={<div>Run builder route</div>} />
      </Routes>
    </MemoryRouter>
  )
}

describe('StrategyCatalog', () => {
  beforeEach(() => {
    getWorkbenchStrategies.mockReset()
    getWorkbenchStrategies.mockResolvedValue({ data: { strategies: sampleStrategies } })
  })

  it('renders grouped strategies from the API', async () => {
    renderCatalog()
    expect(await screen.findByText('Opening Range Spread')).toBeInTheDocument()
    expect(screen.getByText('Buy Call')).toBeInTheDocument()
    expect(screen.getByText('Adaptive setups')).toBeInTheDocument()
    expect(screen.getByText('Bullish setups')).toBeInTheDocument()
  })

  it('filters the catalog by search text', async () => {
    renderCatalog()
    await screen.findByText('Opening Range Spread')
    await userEvent.type(screen.getByPlaceholderText(/search by strategy/i), 'buy call')

    await waitFor(() => {
      expect(screen.queryByText('Opening Range Spread')).not.toBeInTheDocument()
    })
    expect(screen.getByText('Buy Call')).toBeInTheDocument()
  })
})
