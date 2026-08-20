import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { api, clearToken, getToken, setToken } from './api'

// Holds the authenticated administrator + login/logout. The admin flag
// itself is never trusted from the client: it is re-fetched from
// GET /admin/me (which requires get_current_admin on the backend) every
// time the app loads or the guard runs.

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [admin, setAdmin] = useState(null)
  const [booting, setBooting] = useState(Boolean(getToken()))

  const loadMe = useCallback(async () => {
    try {
      setAdmin(await api.adminMe())
    } catch (error) {
      // 401 expired/invalid token or 403 not-an-admin: drop it and
      // require a fresh admin login.
      setAdmin(null)
      clearToken()
    } finally {
      setBooting(false)
    }
  }, [])

  useEffect(() => {
    if (getToken()) loadMe()
  }, [loadMe])

  const login = useCallback(
    async (username, password) => {
      const { access_token } = await api.login(username, password)
      setToken(access_token)
      try {
        const me = await api.adminMe()
        setAdmin(me)
        return me
      } catch (error) {
        // Logged in as a normal (non-admin) user — revoke the token so
        // the panel stays closed.
        clearToken()
        setAdmin(null)
        error.notAdmin = true
        throw error
      }
    },
    [],
  )

  const logout = useCallback(() => {
    clearToken()
    setAdmin(null)
  }, [])

  return (
    <AuthContext.Provider value={{ admin, booting, login, logout, loadMe }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  return useContext(AuthContext)
}