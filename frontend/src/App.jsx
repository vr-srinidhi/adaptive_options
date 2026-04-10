import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import TopNav from './components/TopNav'
import Backtest from './pages/Backtest'
import Dashboard from './pages/Dashboard'
import TradeBook from './pages/TradeBook'
import PaperTrading from './pages/PaperTrading'
import SessionMonitor from './pages/SessionMonitor'
import PaperTradeBook from './pages/PaperTradeBook'

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen flex flex-col" style={{ background: 'var(--surface)' }}>
        <TopNav />
        <main className="flex-1 overflow-auto">
          <Routes>
            <Route path="/" element={<Navigate to="/backtest" replace />} />
            {/* Backtest module */}
            <Route path="/backtest" element={<Backtest />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/tradebook/:id" element={<TradeBook />} />
            {/* Paper Trading module */}
            <Route path="/paper" element={<PaperTrading />} />
            <Route path="/paper/sessions" element={<SessionMonitor />} />
            <Route path="/paper/session/:id" element={<PaperTradeBook />} />
          </Routes>
        </main>
        <footer className="text-center py-3 text-xs" style={{ color: 'var(--text-secondary)', borderTop: '0.5px solid var(--border)' }}>
          For educational and backtesting purposes only. Not financial advice.
        </footer>
      </div>
    </BrowserRouter>
  )
}
