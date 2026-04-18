import { Navigate } from 'react-router-dom'
import { useAuth } from '../contexts/AuthContext'

export default function ProtectedRoute({ children }) {
  const { accessToken, authReady } = useAuth()
  if (!authReady) {
    return null
  }
  if (!accessToken) {
    return <Navigate to="/login" replace />
  }
  return children
}
