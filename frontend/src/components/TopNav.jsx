import { NavLink } from 'react-router-dom'

export default function TopNav() {
  const linkClass = ({ isActive }) =>
    [
      'px-4 py-0 text-xs font-medium transition-colors relative flex items-center h-full',
      isActive
        ? 'text-blue-400 after:absolute after:bottom-0 after:left-0 after:right-0 after:h-0.5 after:bg-blue-400'
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
      {/* Logo */}
      <span className="font-bold text-slate-100 mr-8 tracking-tight text-sm">
        Adaptive<span className="text-blue-400">Options</span>
      </span>

      {/* Nav links */}
      <div className="flex h-full">
        <NavLink to="/backtest" className={linkClass}>
          Backtest
        </NavLink>
        <NavLink to="/dashboard" className={linkClass}>
          Dashboard
        </NavLink>
      </div>

      {/* Regime tag (decorative) */}
      <div className="ml-auto flex items-center gap-2">
        <span className="text-xs px-2 py-0.5 rounded font-medium"
          style={{ background: 'rgba(34,197,94,0.15)', color: '#22c55e', border: '1px solid rgba(34,197,94,0.3)' }}>
          BACKTEST MODE
        </span>
      </div>
    </nav>
  )
}
