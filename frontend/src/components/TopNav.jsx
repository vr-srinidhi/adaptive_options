import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import BrandLogo from './BrandLogo'

export default function TopNav() {
  const location = useLocation()
  const navigate = useNavigate()
  const { user, logout } = useAuth()
  const isPaper = location.pathname.startsWith('/paper')

  const linkClass = ({ isActive }) =>
    [
      'px-4 py-0 text-xs font-medium transition-colors relative flex items-center h-full',
      isActive
        ? 'text-blue-400 after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:bg-blue-400'
        : 'text-slate-400 hover:text-slate-200',
    ].join(' ')

  const paperLinkClass = ({ isActive }) =>
    [
      'px-4 py-0 text-xs font-medium transition-colors relative flex items-center h-full',
      isActive
        ? 'text-amber-400 after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:bg-amber-400'
        : 'text-slate-400 hover:text-slate-200',
    ].join(' ')

  return (
    <nav
      className="flex items-center px-4 shrink-0"
      style={{
        height: 48,
        background: 'var(--surface-secondary)',
        borderBottom: '0.5px solid var(--border)',
      }}
    >
      <BrandLogo size={28} className="mr-6" />

      {/* Divider label */}
      <span className="text-xs mr-2 px-1.5 py-0.5 rounded"
        style={{ color: 'var(--text-secondary)', background: 'var(--surface-tertiary)', border: '1px solid var(--border)' }}>
        BACKTEST
      </span>

      <div className="flex h-full">
        <NavLink to="/backtest" className={linkClass}>Run</NavLink>
        <NavLink to="/dashboard" className={linkClass}>Dashboard</NavLink>
      </div>

      {/* Paper trading section */}
      <div className="mx-4 h-5 w-px" style={{ background: 'var(--border)' }} />

      <span className="text-xs mr-2 px-1.5 py-0.5 rounded"
        style={{ color: '#f59e0b', background: 'rgba(245,158,11,0.12)', border: '1px solid rgba(245,158,11,0.3)' }}>
        PAPER
      </span>

      <div className="flex h-full">
        <NavLink to="/paper" end className={paperLinkClass}>Replay</NavLink>
        <NavLink to="/paper/sessions" className={paperLinkClass}>Sessions</NavLink>
      </div>

      {/* Right side: mode tag + zerodha + user */}
      <div className="ml-auto flex items-center gap-3">
        <span className="text-xs px-2 py-0.5 rounded font-medium"
          style={isPaper
            ? { background: 'rgba(245,158,11,0.15)', color: '#f59e0b', border: '1px solid rgba(245,158,11,0.3)' }
            : { background: 'rgba(34,197,94,0.15)', color: '#22c55e', border: '1px solid rgba(34,197,94,0.3)' }
          }>
          {isPaper ? 'PAPER MODE' : 'BACKTEST MODE'}
        </span>

        <NavLink to="/zerodha-connect"
          className="text-xs px-2 py-0.5 rounded transition"
          style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)', textDecoration: 'none' }}>
          Zerodha
        </NavLink>

        {user && (
          <div className="flex items-center gap-2">
            <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{user.email}</span>
            <button
              onClick={async () => { await logout(); navigate('/login') }}
              className="text-xs px-2 py-0.5 rounded transition"
              style={{ color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)', background: 'none', cursor: 'pointer' }}>
              Sign out
            </button>
          </div>
        )}
      </div>
    </nav>
  )
}
