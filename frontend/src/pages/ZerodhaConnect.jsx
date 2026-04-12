import { useState, useEffect } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import api from '../api'

export default function ZerodhaConnect() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const [status, setStatus] = useState('idle')   // idle | fetching | success | error
  const [message, setMessage] = useState(null)
  const [loginUrl, setLoginUrl] = useState(null)
  const [zerodhaUser, setZerodhaUser] = useState(null)

  // On mount, check if Zerodha is already connected AND handle redirect callback
  useEffect(() => {
    const requestToken = searchParams.get('request_token')
    if (requestToken) {
      handleCallback(requestToken)
    } else {
      checkStatus()
    }
  }, []) // eslint-disable-line

  async function checkStatus() {
    try {
      const res = await api.get('/auth/zerodha/status')
      if (res.data.authenticated) {
        setStatus('success')
        setZerodhaUser(res.data.profile?.user_name || 'Connected')
      } else {
        await fetchLoginUrl()
      }
    } catch {
      await fetchLoginUrl()
    }
  }

  async function fetchLoginUrl() {
    try {
      const res = await api.get('/auth/zerodha/login-url')
      setLoginUrl(res.data.login_url)
      setStatus('idle')
    } catch (err) {
      setStatus('error')
      setMessage(err.response?.data?.detail || 'Could not get Zerodha login URL.')
    }
  }

  async function handleCallback(requestToken) {
    setStatus('fetching')
    setMessage('Exchanging token with Zerodha…')
    try {
      const res = await api.post('/auth/zerodha/session', { request_token: requestToken })
      setStatus('success')
      setZerodhaUser(res.data.user_name || 'Connected')
      // Clear the request_token from URL
      navigate('/zerodha-connect', { replace: true })
    } catch (err) {
      setStatus('error')
      setMessage(err.response?.data?.detail || 'Token exchange failed.')
    }
  }

  const cardStyle = { background: 'var(--surface-secondary)', border: '1px solid var(--border)' }

  return (
    <div className="max-w-lg mx-auto p-6">
      <div className="mb-6">
        <h1 className="text-lg font-bold text-slate-100 mb-1">Zerodha Connection</h1>
        <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>
          Connect your Zerodha account to enable Paper Trade Replay. Tokens expire daily at 6 AM IST.
        </p>
      </div>

      <div className="rounded-xl p-5" style={cardStyle}>
        {status === 'success' && (
          <div className="space-y-4">
            <div className="flex items-center gap-3">
              <div className="w-2 h-2 rounded-full bg-green-400" />
              <span className="text-sm text-slate-100 font-medium">
                Connected as {zerodhaUser}
              </span>
            </div>
            <p className="text-xs" style={{ color: 'var(--text-secondary)' }}>
              Your Zerodha token is stored securely. You can now run Paper Trade sessions.
            </p>
            <div className="flex gap-3">
              <button onClick={() => navigate('/paper')}
                className="px-4 py-2 rounded text-sm font-semibold"
                style={{ background: '#f59e0b', color: '#0f172a', cursor: 'pointer' }}>
                Go to Paper Trading
              </button>
              <button onClick={fetchLoginUrl}
                className="px-4 py-2 rounded text-sm"
                style={{ background: 'var(--surface)', color: 'var(--text-secondary)', cursor: 'pointer' }}>
                Reconnect
              </button>
            </div>
          </div>
        )}

        {(status === 'idle' || status === 'error') && loginUrl && (
          <div className="space-y-4">
            <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
              Click below to authenticate with Zerodha. After login, you&apos;ll be redirected back automatically.
            </p>
            {status === 'error' && message && (
              <div className="px-3 py-2 rounded text-xs"
                style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.3)', color: '#ef4444' }}>
                {message}
              </div>
            )}
            <a href={loginUrl}
              className="inline-flex items-center gap-2 px-5 py-2 rounded font-semibold text-sm"
              style={{ background: '#f59e0b', color: '#0f172a', cursor: 'pointer', textDecoration: 'none' }}>
              Connect Zerodha Account
            </a>
          </div>
        )}

        {status === 'fetching' && (
          <div className="flex items-center gap-3 text-sm" style={{ color: 'var(--text-secondary)' }}>
            <span className="spinner" />
            {message || 'Loading…'}
          </div>
        )}
      </div>
    </div>
  )
}
