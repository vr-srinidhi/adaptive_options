import { createContext, useContext, useState, useCallback } from 'react'
import api, { setToken } from '../api'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  // Access token lives only in memory — never localStorage
  const [accessToken, setAccessToken] = useState(null)
  const [user, setUser] = useState(null)

  const login = useCallback(async (email, password) => {
    const res = await api.post('/users/login', { email, password })
    const token = res.data.access_token
    setToken(token)        // wire into axios interceptor
    setAccessToken(token)
    const me = await api.get('/users/me')
    setUser(me.data)
    return token
  }, [])

  const register = useCallback(async (email, password) => {
    await api.post('/users/register', { email, password })
    return login(email, password)
  }, [login])

  const refresh = useCallback(async () => {
    try {
      const res = await api.post('/users/refresh')
      const token = res.data.access_token
      setToken(token)
      setAccessToken(token)
      const me = await api.get('/users/me')
      setUser(me.data)
      return token
    } catch {
      setToken(null)
      setAccessToken(null)
      setUser(null)
      return null
    }
  }, [])

  const logout = useCallback(async () => {
    try { await api.post('/users/logout') } catch { /* ignore */ }
    setToken(null)
    setAccessToken(null)
    setUser(null)
  }, [])

  return (
    <AuthContext.Provider value={{ accessToken, user, login, register, refresh, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
