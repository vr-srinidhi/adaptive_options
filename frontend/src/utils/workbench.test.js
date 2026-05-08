import { describe, expect, it, vi } from 'vitest'
import {
  fmtDateTime,
  fmtINR,
  fmtNumber,
  fmtShortDate,
  groupStrategiesByBias,
  runKindLabel,
  runStatusTone,
  strategyStatusTone,
  todayISO,
} from './workbench'

describe('workbench formatters', () => {
  it('formats valid values and guards invalid values', () => {
    expect(fmtINR(null)).toBe('—')
    expect(fmtINR('not-a-number')).toBe('—')
    expect(fmtINR(125000)).toBe('₹1,25,000')
    expect(fmtNumber(undefined)).toBe('—')
    expect(fmtNumber(1234.567, 2)).toBe('1,234.57')
    expect(fmtDateTime('not-a-date')).toBe('not-a-date')
    expect(fmtShortDate('not-a-date')).toBe('not-a-date')
    expect(fmtDateTime('2026-05-06T09:15:00')).toMatch(/2026/)
    expect(fmtShortDate('2026-05-06')).toMatch(/2026/)
  })

  it('returns the current ISO date', () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-08T04:30:00.000Z'))

    expect(todayISO()).toBe('2026-05-08')
  })
})

describe('workbench grouping and labels', () => {
  it('groups strategies in dashboard order and preserves unknown biases as Other', () => {
    const groups = groupStrategiesByBias([
      { id: 1, bias: 'neutral' },
      { id: 2, bias: 'bearish' },
      { id: 3, bias: 'custom' },
      { id: 4, bias: 'adaptive' },
    ])

    expect(groups.map(group => group.key)).toEqual(['adaptive', 'bearish', 'neutral', 'other'])
    expect(groups.at(-1).items).toEqual([{ id: 3, bias: 'custom' }])
  })

  it('maps known and unknown run kind labels', () => {
    expect(runKindLabel('paper_session')).toBe('Paper Replay')
    expect(runKindLabel('historical_batch')).toBe('Historical Batch')
    expect(runKindLabel('custom_kind')).toBe('custom_kind')
  })
})

describe('workbench tone helpers', () => {
  it('returns explicit strategy tones and unknown fallback', () => {
    expect(strategyStatusTone('available').label).toBe('Live')
    expect(strategyStatusTone('planned').label).toBe('Planned')
    expect(strategyStatusTone('unknown').label).toBe('unknown')
    expect(strategyStatusTone(null).label).toBe('Unknown')
  })

  it('returns run status tones with draft fallback', () => {
    expect(runStatusTone('COMPLETED').color).toBe('#36b37e')
    expect(runStatusTone('failed').color).toBe('#ff5630')
    expect(runStatusTone('completed_with_warnings').color).toBe('#ffc400')
    expect(runStatusTone('unexpected').color).toBe('#94a3b8')
  })
})
