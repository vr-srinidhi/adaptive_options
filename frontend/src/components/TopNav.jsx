import { useEffect, useRef, useState } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'
import BrandLogo from './BrandLogo'

const PRIMARY_LINKS = [
  { to: '/workbench', label: 'Home', icon: '⌂', match: path => path === '/workbench' || path === '/' },
  { to: '/workbench/strategies', label: 'Strategies', icon: '◈', match: path => path.startsWith('/workbench/strategies') },
  { to: '/workbench/run', label: 'Run', icon: '▶', match: path => path.startsWith('/workbench/run') },
  { to: '/workbench/replay', label: 'Replay', icon: '⏪', match: path => path.startsWith('/workbench/replay') },
  { to: '/workbench/live', label: 'Live', icon: '●', match: path => path.startsWith('/workbench/live'), dot: true },
  { to: '/workbench/history', label: 'History', icon: '☰', match: path => path.startsWith('/workbench/history') },
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
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef(null)
  const onWorkbench = location.pathname === '/' || location.pathname.startsWith('/workbench')

  // Close menu on route change
  useEffect(() => { setMenuOpen(false) }, [location.pathname])

  // Close menu on outside click
  useEffect(() => {
    if (!menuOpen) return
    function handle(e) {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', handle)
    document.addEventListener('touchstart', handle)
    return () => {
      document.removeEventListener('mousedown', handle)
      document.removeEventListener('touchstart', handle)
    }
  }, [menuOpen])

  const activeLabel = PRIMARY_LINKS.find(link => link.match(location.pathname))?.label || 'Legacy'
  const primaryLinkClass = active =>
    [
      'px-4 py-0 text-xs font-medium transition-colors relative flex items-center h-full',
      active
        ? 'text-blue-400 after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:bg-blue-400'
        : 'text-slate-400 hover:text-slate-200',
    ].join(' ')

  return (
    <div ref={menuRef} className="sticky top-0 z-20">
      {/* ── Main bar ──────────────────────────────────────────────── */}
      <nav
        className="flex items-center px-4 shrink-0"
        style={{
          height: 48,
          background: 'var(--surface-secondary)',
          borderBottom: '0.5px solid var(--border)',
        }}
      >
        {/* Hamburger — mobile only */}
        <button
          className="md:hidden mr-3 flex flex-col justify-center gap-1 shrink-0"
          style={{ width: 24, height: 24, background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
          onClick={() => setMenuOpen(o => !o)}
          aria-label="Toggle menu"
        >
          <span style={{ display: 'block', height: 2, borderRadius: 1, background: menuOpen ? '#6366f1' : 'var(--text-secondary)', transition: 'transform 0.2s, background 0.2s', transform: menuOpen ? 'translateY(5px) rotate(45deg)' : 'none' }} />
          <span style={{ display: 'block', height: 2, borderRadius: 1, background: menuOpen ? 'transparent' : 'var(--text-secondary)', transition: 'opacity 0.15s', opacity: menuOpen ? 0 : 1 }} />
          <span style={{ display: 'block', height: 2, borderRadius: 1, background: menuOpen ? '#6366f1' : 'var(--text-secondary)', transition: 'transform 0.2s, background 0.2s', transform: menuOpen ? 'translateY(-5px) rotate(-45deg)' : 'none' }} />
        </button>

        <BrandLogo size={28} className="mr-6 shrink-0" />

        {/* Desktop primary nav */}
        <div className="hidden md:flex h-full">
          {PRIMARY_LINKS.map(link => {
            const active = link.match(location.pathname)
            return (
              <NavLink
                key={link.to}
                to={link.to}
                className={primaryLinkClass(active)}
              >
                {link.label}
                {link.dot && (
                  <span style={{
                    display: 'inline-block', width: 6, height: 6, borderRadius: '50%',
                    background: '#4ade80', marginLeft: 4, flexShrink: 0,
                    opacity: 0.8,
                  }} />
                )}
              </NavLink>
            )
          })}
          {onWorkbench ? (
            <span
              className="ml-2 inline-flex h-full items-center px-3 text-xs font-medium"
              style={{ color: 'var(--text-secondary)' }}
            >
              Lab
              <span
                className="ml-1 rounded px-1 py-0.5 text-[10px]"
                style={{ border: '1px solid var(--border)', background: 'var(--surface)', color: 'var(--text-secondary)' }}
              >
                P2
              </span>
            </span>
          ) : null}
        </div>

        {!onWorkbench ? (
          <>
            <div className="mx-4 h-5 w-px hidden xl:block" style={{ background: 'var(--border)' }} />
            <div className="hidden xl:flex items-center gap-2">
              <span
                className="text-xs px-1.5 py-0.5 rounded"
                style={{ color: 'var(--text-secondary)', background: 'var(--surface-tertiary)', border: '1px solid var(--border)' }}
              >
                LEGACY
              </span>
              {LEGACY_LINKS.map(link => (
                <NavLink
                  key={link.to}
                  to={link.to}
                  className="text-xs px-2 py-0.5 rounded transition"
                  style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)', textDecoration: 'none' }}
                >
                  {link.label}
                </NavLink>
              ))}
            </div>
          </>
        ) : null}

        <div className="ml-auto flex items-center gap-3">
          <span
            className="text-xs px-2 py-0.5 rounded font-medium hidden sm:inline-flex"
            style={{ background: 'rgba(59,130,246,0.15)', color: '#3b82f6', border: '1px solid rgba(59,130,246,0.3)' }}
          >
            {activeLabel.toUpperCase()}
          </span>

          <NavLink
            to="/zerodha-connect"
            className="text-xs px-2 py-0.5 rounded transition hidden sm:inline-flex"
            style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)', textDecoration: 'none' }}
          >
            Zerodha
          </NavLink>

          {user && (
            <div className="flex items-center gap-2">
              <span
                className="hidden lg:inline text-xs px-2 py-0.5 rounded"
                style={{ color: 'var(--text-secondary)', border: '1px solid var(--border)', background: 'var(--surface)' }}
              >
                {user.email}
              </span>
              <button
                onClick={async () => { await logout(); navigate('/login') }}
                className="text-xs px-2 py-0.5 rounded transition"
                style={{ color: '#ef4444', border: '1px solid rgba(239,68,68,0.3)', background: 'none', cursor: 'pointer' }}
              >
                Sign out
              </button>
            </div>
          )}
        </div>
      </nav>

      {/* ── Mobile drawer ─────────────────────────────────────────── */}
      {menuOpen && (
        <div
          className="md:hidden"
          style={{
            position: 'absolute',
            top: '100%',
            left: 0,
            right: 0,
            background: 'var(--surface-secondary)',
            borderBottom: '1px solid var(--border)',
            boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
            zIndex: 30,
          }}
        >
          {PRIMARY_LINKS.map(link => {
            const active = link.match(location.pathname)
            return (
              <NavLink
                key={link.to}
                to={link.to}
                onClick={() => setMenuOpen(false)}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 12,
                  padding: '14px 20px',
                  borderBottom: '0.5px solid var(--border)',
                  textDecoration: 'none',
                  background: active ? 'rgba(99,102,241,0.08)' : 'transparent',
                  color: active ? '#818cf8' : 'var(--text-secondary)',
                  fontSize: 14,
                  fontWeight: active ? 600 : 400,
                  transition: 'background 0.15s',
                }}
              >
                <span style={{ width: 20, textAlign: 'center', fontSize: 16 }}>{link.icon}</span>
                {link.label}
                {link.dot && (
                  <span style={{
                    width: 7, height: 7, borderRadius: '50%',
                    background: '#4ade80', flexShrink: 0,
                  }} />
                )}
                {active && <span style={{ marginLeft: 'auto', fontSize: 10, color: '#818cf8' }}>●</span>}
              </NavLink>
            )
          })}

          {/* Zerodha + divider at bottom of drawer */}
          <div style={{ padding: '12px 20px', display: 'flex', gap: 12, alignItems: 'center' }}>
            <NavLink
              to="/zerodha-connect"
              onClick={() => setMenuOpen(false)}
              style={{
                fontSize: 12, padding: '6px 14px', borderRadius: 6,
                color: 'var(--text-secondary)', border: '1px solid var(--border)',
                textDecoration: 'none', background: 'transparent',
              }}
            >
              Zerodha Connect
            </NavLink>
          </div>
        </div>
      )}
    </div>
  )
}
