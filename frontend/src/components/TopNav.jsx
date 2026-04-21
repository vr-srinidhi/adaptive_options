import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import BrandLogo from './BrandLogo'

const PRIMARY_LINKS = [
  { to: '/workbench', label: 'Home', match: path => path === '/workbench' || path === '/' },
  { to: '/workbench/strategies', label: 'Strategies', match: path => path.startsWith('/workbench/strategies') },
  { to: '/workbench/run', label: 'Run', match: path => path.startsWith('/workbench/run') },
  { to: '/workbench/replay', label: 'Replay', match: path => path.startsWith('/workbench/replay') },
  { to: '/workbench/history', label: 'History', match: path => path.startsWith('/workbench/history') },
]

const LEGACY_LINKS = [
  { to: '/backtest', label: 'Backtest' },
  { to: '/paper', label: 'Paper' },
  { to: '/paper/sessions', label: 'Sessions' },
  { to: '/backtests', label: 'Backtests' },
]

export default function TopNav() {
  const location = useLocation()
  const navigate = useNavigate()
  const { user, logout } = useAuth()

  const activeLabel = PRIMARY_LINKS.find(link => link.match(location.pathname))?.label || 'Legacy'

  return (
    <nav className="sticky top-0 z-20 border-b" style={{ background: 'rgba(9, 14, 24, 0.92)', borderColor: 'rgba(39, 54, 75, 0.9)', backdropFilter: 'blur(10px)' }}>
      <div className="max-w-7xl mx-auto px-4 py-3 flex items-center gap-6">
        <BrandLogo size={30} subtitle="Research Workbench" className="shrink-0" />

        <div className="hidden md:flex items-center gap-1 flex-1">
          {PRIMARY_LINKS.map(link => {
            const active = link.match(location.pathname)
            return (
              <NavLink
                key={link.to}
                to={link.to}
                className="px-3 py-2 rounded-full text-sm transition-colors"
                style={active
                  ? { background: 'rgba(56, 189, 248, 0.12)', color: '#d8f4ff', border: '1px solid rgba(56, 189, 248, 0.2)' }
                  : { color: '#9db0c8' }}
              >
                {link.label}
              </NavLink>
            )
          })}
        </div>

        <div className="hidden xl:flex items-center gap-2">
          <span className="text-[11px] uppercase tracking-[0.24em]" style={{ color: '#6f849f' }}>Legacy</span>
          {LEGACY_LINKS.map(link => (
            <NavLink
              key={link.to}
              to={link.to}
              className="px-2.5 py-1.5 rounded-full text-xs transition-colors"
              style={{ color: '#9db0c8', border: '1px solid rgba(39, 54, 75, 0.7)' }}
            >
              {link.label}
            </NavLink>
          ))}
        </div>

        <div className="ml-auto flex items-center gap-3">
          <span className="wb-chip hidden sm:inline-flex">{activeLabel}</span>
          <NavLink to="/zerodha-connect" className="wb-secondary-button">
            Zerodha
          </NavLink>
          {user && (
            <div className="flex items-center gap-2">
              <span className="hidden lg:inline text-sm" style={{ color: '#9db0c8' }}>{user.email}</span>
              <button
                onClick={async () => { await logout(); navigate('/login') }}
                className="wb-secondary-button"
                style={{ color: '#ff8f73', borderColor: 'rgba(255, 143, 115, 0.25)' }}
              >
                Sign out
              </button>
            </div>
          )}
        </div>
      </div>
    </nav>
  )
}
