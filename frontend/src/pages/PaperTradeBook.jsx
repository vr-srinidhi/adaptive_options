import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { getPaperSession, getPaperDecisions, getPaperTrade, getPaperMarks, getPaperCandles } from '../api'
import PaperSessionReport from '../components/PaperSessionReport'
import { buildPaperSessionCSV, downloadBlob } from '../utils/paperSessionExport'

function downloadCSV(session, trade, decisions, marks, candleSeries) {
  const csv = buildPaperSessionCSV(session, trade, decisions, marks, candleSeries)
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' })
  downloadBlob(blob, `ORB_${session.instrument}_${session.session_date}.csv`)
}

export default function PaperTradeBook() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [session, setSession] = useState(null)
  const [decisions, setDecisions] = useState([])
  const [trade, setTrade] = useState(null)
  const [marks, setMarks] = useState([])
  const [candleSeries, setCandleSeries] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('ALL')
  const [error, setError] = useState(null)

  useEffect(() => {
    Promise.all([
      getPaperSession(id),
      getPaperDecisions(id),
      getPaperTrade(id),
      getPaperMarks(id),
      getPaperCandles(id),
    ])
      .then(([sRes, dRes, tRes, mRes, cRes]) => {
        setSession(sRes.data)
        setDecisions(dRes.data)
        setTrade(tRes.data.trade)
        setMarks(mRes.data)
        setCandleSeries(cRes.data)
      })
      .catch(err => setError(err.response?.data?.detail || err.message))
      .finally(() => setLoading(false))
  }, [id])

  if (loading) return (
    <div className="flex items-center justify-center h-64 gap-2" style={{ color: 'var(--text-secondary)' }}>
      <span className="spinner" /> Loading audit log…
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

  if (!session) return null

  return (
    <>
      <style>{`
        @media print {
          body * { visibility: hidden; }
          #print-region, #print-region * { visibility: visible; }
          #print-region { position: absolute; top: 0; left: 0; width: 100%; font-size: 11px; }
          .no-print { display: none !important; }
          table { border-collapse: collapse; width: 100%; }
          th, td { border: 1px solid #ccc; padding: 4px 6px; }
          thead { background: #f3f4f6 !important; print-color-adjust: exact; }
        }
      `}</style>

      <div id="print-region" className="max-w-6xl mx-auto p-6">
        <div className="flex items-center justify-between mb-4 no-print">
          <button onClick={() => navigate('/paper/sessions')}
            className="text-xs flex items-center gap-1"
            style={{ color: 'var(--text-secondary)', cursor: 'pointer', background: 'none', border: 'none', padding: 0 }}>
            ← Sessions
          </button>
          <div className="flex gap-2">
            <button onClick={() => downloadCSV(session, trade, decisions, marks, candleSeries)}
              className="px-3 py-1.5 rounded text-xs font-medium flex items-center gap-1.5"
              style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)', color: 'var(--text-secondary)', cursor: 'pointer' }}>
              ↓ CSV
            </button>
            <button onClick={() => window.print()}
              className="px-3 py-1.5 rounded text-xs font-medium flex items-center gap-1.5"
              style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)', color: 'var(--text-secondary)', cursor: 'pointer' }}>
              ↓ PDF
            </button>
          </div>
        </div>

        <PaperSessionReport
          session={session}
          trade={trade}
          decisions={decisions}
          marks={marks}
          candleSeries={candleSeries}
          filter={filter}
          onFilterChange={setFilter}
          showFilters
        />
      </div>
    </>
  )
}
