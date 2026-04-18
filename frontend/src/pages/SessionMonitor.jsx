import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import JSZip from 'jszip'
import { exportPaperSessionsBundle, getPaperSessions } from '../api'
import {
  buildPaperSessionCSV,
  buildPaperSessionsSummaryCSV,
  downloadBlob,
  fmtINR,
} from '../utils/paperSessionExport'

const STATUS_STYLES = {
  COMPLETED: { bg: 'rgba(34,197,94,0.12)', border: 'rgba(34,197,94,0.35)', text: '#22c55e' },
  RUNNING:   { bg: 'rgba(59,130,246,0.12)', border: 'rgba(59,130,246,0.35)', text: '#3b82f6' },
  ERROR:     { bg: 'rgba(239,68,68,0.12)', border: 'rgba(239,68,68,0.35)', text: '#ef4444' },
}

function StatusBadge({ status }) {
  const s = STATUS_STYLES[status] || STATUS_STYLES.RUNNING
  return (
    <span className="px-2 py-0.5 rounded text-xs font-semibold"
      style={{ background: s.bg, border: `1px solid ${s.border}`, color: s.text }}>
      {status}
    </span>
  )
}

function safeCsvName(bundle) {
  return `${bundle.session.session_date}_${bundle.session.instrument}_${bundle.session.id.slice(0, 8)}.csv`
}

export default function SessionMonitor() {
  const navigate = useNavigate()
  const [sessions, setSessions] = useState([])
  const [selectedIds, setSelectedIds] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [exportingCsv, setExportingCsv] = useState(false)

  useEffect(() => {
    getPaperSessions()
      .then(r => {
        setSessions(r.data)
        setSelectedIds(prev => prev.filter(id => r.data.some(session => session.id === id)))
      })
      .catch(err => setError(err.response?.data?.detail || err.message))
      .finally(() => setLoading(false))
  }, [])

  const allVisibleSelected = sessions.length > 0 && sessions.every(session => selectedIds.includes(session.id))

  const toggleSelected = (sessionId) => {
    setSelectedIds(prev =>
      prev.includes(sessionId)
        ? prev.filter(id => id !== sessionId)
        : [...prev, sessionId]
    )
  }

  const toggleAllVisible = () => {
    setSelectedIds(allVisibleSelected ? [] : sessions.map(session => session.id))
  }

  const handleBulkCSV = async () => {
    if (selectedIds.length === 0) return
    setActionError(null)
    setExportingCsv(true)
    try {
      const res = await exportPaperSessionsBundle(selectedIds)
      const bundles = res.data.sessions || []
      const zip = new JSZip()
      zip.file('paper_sessions_summary.csv', buildPaperSessionsSummaryCSV(bundles))
      bundles.forEach(bundle => {
        zip.file(
          safeCsvName(bundle),
          buildPaperSessionCSV(
            bundle.session,
            bundle.trade,
            bundle.decisions,
            bundle.marks,
            bundle.candle_series
          )
        )
      })
      const blob = await zip.generateAsync({ type: 'blob' })
      downloadBlob(blob, `paper_sessions_${new Date().toISOString().slice(0, 10)}.zip`)
    } catch (err) {
      setActionError(err.response?.data?.detail || err.message)
    } finally {
      setExportingCsv(false)
    }
  }

  const handleBulkPDF = () => {
    if (selectedIds.length === 0) return
    setActionError(null)
    const params = new URLSearchParams({ ids: selectedIds.join(',') })
    window.open(`/paper/sessions/print?${params.toString()}`, '_blank', 'noopener,noreferrer')
  }

  if (loading) return (
    <div className="flex items-center justify-center h-64 gap-2" style={{ color: 'var(--text-secondary)' }}>
      <span className="spinner" /> Loading sessions…
    </div>
  )

  if (error) return (
    <div className="max-w-4xl mx-auto p-6">
      <div className="px-4 py-3 rounded text-sm"
        style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#ef4444' }}>
        {error}
      </div>
    </div>
  )

  return (
    <div className="max-w-6xl mx-auto p-6">
      <div className="flex items-start justify-between mb-6 gap-4">
        <div>
          <h1 className="text-lg font-bold text-slate-100">Paper Trading Sessions</h1>
          <p className="text-xs mt-0.5" style={{ color: 'var(--text-secondary)' }}>
            ORB replay sessions · select rows for bulk CSV/PDF export or click a row to view full minute audit log
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap justify-end">
          <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
            {selectedIds.length} selected
          </span>
          <button
            onClick={toggleAllVisible}
            disabled={sessions.length === 0}
            className="px-3 py-1.5 rounded text-xs font-medium"
            style={{
              background: 'var(--surface-secondary)',
              border: '1px solid var(--border)',
              color: 'var(--text-secondary)',
              cursor: sessions.length === 0 ? 'not-allowed' : 'pointer',
              opacity: sessions.length === 0 ? 0.6 : 1,
            }}>
            {allVisibleSelected ? 'Clear selection' : 'Select all visible'}
          </button>
          <button
            onClick={handleBulkCSV}
            disabled={selectedIds.length === 0 || exportingCsv}
            className="px-3 py-1.5 rounded text-xs font-medium"
            style={{
              background: 'var(--surface-secondary)',
              border: '1px solid var(--border)',
              color: 'var(--text-secondary)',
              cursor: selectedIds.length === 0 || exportingCsv ? 'not-allowed' : 'pointer',
              opacity: selectedIds.length === 0 || exportingCsv ? 0.6 : 1,
            }}>
            {exportingCsv ? 'Preparing ZIP…' : '↓ Bulk CSV'}
          </button>
          <button
            onClick={handleBulkPDF}
            disabled={selectedIds.length === 0}
            className="px-3 py-1.5 rounded text-xs font-medium"
            style={{
              background: 'var(--surface-secondary)',
              border: '1px solid var(--border)',
              color: 'var(--text-secondary)',
              cursor: selectedIds.length === 0 ? 'not-allowed' : 'pointer',
              opacity: selectedIds.length === 0 ? 0.6 : 1,
            }}>
            ↓ Bulk PDF
          </button>
          <button
            onClick={() => navigate('/paper')}
            className="px-4 py-1.5 rounded text-xs font-medium"
            style={{ background: '#f59e0b', color: '#0f172a', cursor: 'pointer' }}>
            + New Replay
          </button>
        </div>
      </div>

      {actionError && (
        <div className="mb-4 px-4 py-3 rounded text-sm"
          style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#ef4444' }}>
          {actionError}
        </div>
      )}

      {sessions.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-24 gap-4">
          <div className="text-4xl">📋</div>
          <div className="text-sm" style={{ color: 'var(--text-secondary)' }}>No paper trading sessions yet.</div>
          <button onClick={() => navigate('/paper')}
            className="px-4 py-2 rounded text-sm font-medium"
            style={{ background: '#f59e0b', color: '#0f172a', cursor: 'pointer' }}>
            Replay your first day →
          </button>
        </div>
      ) : (
        <div className="rounded-xl overflow-hidden"
          style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>
          <table className="w-full text-xs">
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', background: 'var(--surface-tertiary)' }}>
                {['', 'Date', 'Instrument', 'Capital', 'Status', 'Decisions', 'P/L', 'Created At'].map(h => (
                  <th key={h || 'select'} className="text-left px-3 py-2.5 font-medium uppercase tracking-wider"
                    style={{ color: 'var(--text-secondary)' }}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sessions.map(s => {
                const pnlColor = s.summary_pnl == null
                  ? 'var(--text-secondary)'
                  : s.summary_pnl >= 0 ? '#22c55e' : '#ef4444'
                const checked = selectedIds.includes(s.id)

                return (
                  <tr
                    key={s.id}
                    className="table-row-hover"
                    style={{ borderBottom: '0.5px solid var(--border)', cursor: 'pointer' }}
                    onClick={() => navigate(`/paper/session/${s.id}`)}
                  >
                    <td className="px-3 py-2.5" onClick={e => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() => toggleSelected(s.id)}
                        aria-label={`Select ${s.session_date} ${s.instrument}`}
                      />
                    </td>
                    <td className="px-3 py-2.5 font-medium" style={{ color: 'var(--text-primary)' }}>
                      {s.session_date}
                    </td>
                    <td className="px-3 py-2.5" style={{ color: 'var(--text-secondary)' }}>
                      {s.instrument}
                    </td>
                    <td className="px-3 py-2.5" style={{ color: 'var(--text-secondary)' }}>
                      {fmtINR(s.capital)}
                    </td>
                    <td className="px-3 py-2.5">
                      <StatusBadge status={s.status} />
                    </td>
                    <td className="px-3 py-2.5" style={{ color: 'var(--text-secondary)' }}>
                      {s.decision_count ?? '—'}
                    </td>
                    <td className="px-3 py-2.5 font-mono" style={{ color: pnlColor }}>
                      {fmtINR(s.summary_pnl)}
                    </td>
                    <td className="px-3 py-2.5" style={{ color: 'var(--text-secondary)' }}>
                      {s.created_at ? s.created_at.slice(0, 16).replace('T', ' ') : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
