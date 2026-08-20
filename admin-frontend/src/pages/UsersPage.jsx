import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'
import { ErrorBanner, formatDate, Spinner } from '../components/ui'

const PAGE_SIZE = 20

export default function UsersPage() {
  const { admin } = useAuth()
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [isActive, setIsActive] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [flash, setFlash] = useState(null)
  const [busyId, setBusyId] = useState(null)
  const searchTimer = useRef(null)

  useEffect(() => {
    clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => {
      setDebouncedQuery(query)
      setOffset(0)
    }, 300)
    return () => clearTimeout(searchTimer.current)
  }, [query])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = { limit: PAGE_SIZE, offset }
      if (debouncedQuery) params.q = debouncedQuery
      if (isActive !== '') params.isActive = isActive
      const data = await api.listUsers(params)
      setItems(data.items)
      setTotal(data.total)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [offset, debouncedQuery, isActive])

  useEffect(() => {
    load()
  }, [load])

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const page = Math.floor(offset / PAGE_SIZE) + 1

  const changeStatus = async (user) => {
    const target = !user.is_active
    const verb = target ? 'reactivate' : 'deactivate'
    const confirmed = window.confirm(
      `Are you sure you want to ${verb} user ${user.username}?\n\n` +
        (target
          ? 'A reactivated user can log in again.'
          : 'A deactivated user cannot log in and loses access to protected features.'),
    )
    if (!confirmed) return

    setBusyId(user.user_id)
    setError(null)
    try {
      await api.updateUserStatus(user.user_id, target)
      setFlash(`User ${user.username} ${target ? 'reactivated' : 'deactivated'}`)
      load()
    } catch (err) {
      setError(err.message)
      load()
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div>
      <div className="page-head">
        <h1 className="page-title">Users</h1>
        <button className="btn btn-ghost" onClick={load} disabled={loading}>
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      {flash && <div className="flash-banner">{flash}</div>}
      <ErrorBanner message={error} onClose={() => setError(null)} />

      <div className="toolbar">
        <label className="field field-inline search-field">
          <span>Search</span>
          <input
            type="search"
            placeholder="Username or email…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <label className="field field-inline">
          <span>Status</span>
          <select
            value={isActive}
            onChange={(e) => {
              setIsActive(e.target.value)
              setOffset(0)
            }}
          >
            <option value="">All</option>
            <option value="true">Active</option>
            <option value="false">Inactive</option>
          </select>
        </label>
        <span className="muted toolbar-total">{total} user(s)</span>
      </div>

      {loading ? (
        <Spinner />
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>User</th>
                <th>Email</th>
                <th>Coins</th>
                <th>Streak</th>
                <th>Status</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 && (
                <tr>
                  <td colSpan="7" className="table-empty">
                    No users found
                  </td>
                </tr>
              )}
              {items.map((user) => (
                <tr key={user.user_id}>
                  <td className="user-cell">
                    <span className="avatar">{initials(user.username)}</span>
                    <span>{user.username}</span>
                  </td>
                  <td>{user.email}</td>
                  <td>{user.coin_balance}</td>
                  <td>{user.winning_streak}</td>
                  <td>
                    <span className={`pill ${user.is_active ? 'pill-active' : 'pill-inactive'}`}>
                      {user.is_active ? 'active' : 'inactive'}
                    </span>
                  </td>
                  <td>{formatDate(user.created_at)}</td>
                  <td className="table-actions">
                    <Link className="btn btn-ghost btn-small" to={`/users/${user.user_id}`}>
                      View
                    </Link>
                    {user.user_id !== admin?.user_id && (
                      <button
                        className={`btn btn-small ${user.is_active ? 'btn-danger' : 'btn-ghost'}`}
                        onClick={() => changeStatus(user)}
                        disabled={busyId === user.user_id}
                      >
                        {user.is_active ? 'Deactivate' : 'Reactivate'}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {pageCount > 1 && (
        <div className="pagination">
          <button
            className="btn btn-ghost btn-small"
            disabled={page <= 1}
            onClick={() => setOffset(offset - PAGE_SIZE)}
          >
            ← Prev
          </button>
          <span>
            Page {page} of {pageCount}
          </span>
          <button
            className="btn btn-ghost btn-small"
            disabled={page >= pageCount}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            Next →
          </button>
        </div>
      )}
    </div>
  )
}

function initials(username) {
  return (username || '?').slice(0, 2).toUpperCase()
}