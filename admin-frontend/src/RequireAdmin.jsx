import { Navigate } from 'react-router-dom'
import { useAuth } from './auth'

// Route guard for everything under the admin panel. Access is verified
// against the backend (GET /admin/me), never against local state: a user
// who hides this component in the browser still gets 401/403 from the API.
export default function RequireAdmin({ children }) {
  const { admin, booting } = useAuth()

  if (booting) return <div className="screen-center">Checking access…</div>
  if (!admin) return <Navigate to="/login" replace />
  return children
}