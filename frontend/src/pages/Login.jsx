import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'

export default function Login() {
  const navigate = useNavigate()
  const { login, register } = useAuth()
  const [mode, setMode] = useState('login')   // 'login' | 'register'
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const inputCls = 'w-full px-3 py-2 rounded text-sm outline-none focus:ring-1 focus:ring-blue-500 transition'
  const inputStyle = { background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--text-primary)' }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      if (mode === 'register') {
        await register(email, password)
      } else {
        await login(email, password)
      }
      navigate('/backtest', { replace: true })
    } catch (err) {
      setError(err.response?.data?.detail || err.message || 'Authentication failed.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4"
      style={{ background: 'var(--surface)' }}>
      <div className="w-full max-w-sm">
        <div className="text-center mb-8">
          <h1 className="text-2xl font-bold text-slate-100 mb-1">Adaptive Options</h1>
          <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>
            Options backtesting &amp; paper trading platform
          </p>
        </div>

        <div className="rounded-xl p-6"
          style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}>
          <div className="flex gap-2 mb-6">
            {['login', 'register'].map(m => (
              <button key={m} onClick={() => setMode(m)}
                className="flex-1 py-1.5 rounded text-xs font-semibold capitalize transition"
                style={{
                  background: mode === m ? '#3b82f6' : 'var(--surface)',
                  color: mode === m ? '#fff' : 'var(--text-secondary)',
                }}>
                {m === 'login' ? 'Sign In' : 'Create Account'}
              </button>
            ))}
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-xs uppercase tracking-widest mb-1.5"
                style={{ color: 'var(--text-secondary)' }}>Email</label>
              <input type="email" required className={inputCls} style={inputStyle}
                value={email} onChange={e => setEmail(e.target.value)}
                placeholder="you@example.com" autoComplete="email" />
            </div>
            <div>
              <label className="block text-xs uppercase tracking-widest mb-1.5"
                style={{ color: 'var(--text-secondary)' }}>Password</label>
              <input type="password" required className={inputCls} style={inputStyle}
                value={password} onChange={e => setPassword(e.target.value)}
                placeholder={mode === 'register' ? 'Min 8 characters' : ''}
                autoComplete={mode === 'login' ? 'current-password' : 'new-password'} />
            </div>

            {error && (
              <div className="px-3 py-2 rounded text-xs"
                style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#ef4444' }}>
                {error}
              </div>
            )}

            <button type="submit" disabled={loading}
              className="w-full py-2 rounded font-semibold text-sm transition"
              style={{
                background: loading ? '#334155' : '#3b82f6',
                color: loading ? '#94a3b8' : '#fff',
                cursor: loading ? 'not-allowed' : 'pointer',
              }}>
              {loading ? 'Please wait…' : mode === 'login' ? 'Sign In' : 'Create Account'}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
