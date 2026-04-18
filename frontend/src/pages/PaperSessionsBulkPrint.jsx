import { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import PaperSessionReport from '../components/PaperSessionReport'
import { exportPaperSessionsBundle } from '../api'

export default function PaperSessionsBulkPrint() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const sessionIdParam = searchParams.get('ids') || ''
  const [bundles, setBundles] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const printedRef = useRef(false)

  const sessionIds = sessionIdParam
    .split(',')
    .map(value => value.trim())
    .filter(Boolean)

  useEffect(() => {
    printedRef.current = false
    if (sessionIds.length === 0) {
      setError('No sessions selected for bulk PDF export.')
      setLoading(false)
      return
    }

    setError(null)
    setLoading(true)
    setBundles([])
    exportPaperSessionsBundle(sessionIds)
      .then(res => setBundles(res.data.sessions || []))
      .catch(err => setError(err.response?.data?.detail || err.message))
      .finally(() => setLoading(false))
  }, [sessionIdParam])

  useEffect(() => {
    if (loading || error || bundles.length === 0 || printedRef.current) return
    printedRef.current = true
    const timer = setTimeout(() => window.print(), 150)
    return () => clearTimeout(timer)
  }, [loading, error, bundles])

  if (loading) return (
    <div className="flex items-center justify-center h-64 gap-2" style={{ color: 'var(--text-secondary)' }}>
      <span className="spinner" /> Preparing bulk PDF export…
    </div>
  )

  if (error) return (
    <div className="max-w-5xl mx-auto p-6">
      <div className="px-4 py-3 rounded text-sm"
        style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#ef4444' }}>
        {error}
      </div>
    </div>
  )

  return (
    <>
      <style>{`
        @media print {
          body * { visibility: hidden; }
          #bulk-print-region, #bulk-print-region * { visibility: visible; }
          #bulk-print-region { position: absolute; top: 0; left: 0; width: 100%; font-size: 11px; }
          .no-print { display: none !important; }
          table { border-collapse: collapse; width: 100%; }
          th, td { border: 1px solid #ccc; padding: 4px 6px; }
          thead { background: #f3f4f6 !important; print-color-adjust: exact; }
          .paper-session-page { page-break-after: always; break-after: page; }
          .paper-session-page:last-child { page-break-after: auto; break-after: auto; }
        }
      `}</style>

      <div id="bulk-print-region" className="max-w-6xl mx-auto p-6">
        <div className="flex items-center justify-between mb-4 no-print">
          <button onClick={() => navigate('/paper/sessions')}
            className="text-xs flex items-center gap-1"
            style={{ color: 'var(--text-secondary)', cursor: 'pointer', background: 'none', border: 'none', padding: 0 }}>
            ← Sessions
          </button>
          <div className="flex gap-2">
            <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
              {bundles.length} sessions selected
            </span>
            <button onClick={() => window.print()}
              className="px-3 py-1.5 rounded text-xs font-medium flex items-center gap-1.5"
              style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)', color: 'var(--text-secondary)', cursor: 'pointer' }}>
              ↓ PDF
            </button>
          </div>
        </div>

        {bundles.map(bundle => (
          <section key={bundle.session.id} className="paper-session-page mb-10">
            <PaperSessionReport
              session={bundle.session}
              trade={bundle.trade}
              decisions={bundle.decisions}
              marks={bundle.marks}
              candleSeries={bundle.candle_series}
            />
          </section>
        ))}
      </div>
    </>
  )
}
