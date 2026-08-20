import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import { useAuth } from '../auth'
import { ErrorBanner, formatDate, Spinner } from '../components/ui'

export default function UserDetailPage() {
  const { userId } = useParams()
  const { admin } = useAuth()
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [flash, setFlash] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .getUser(userId)
      .then((data) => {
        if (!cancelled) setUser(data)
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [userId])

  const isSelf = user?.user_id === admin?.user_id

  const changeStatus = async () => {
    if (!user) return
    const target = !user.is_active
    const verb = target ? 'reactivate' : 'deactivate'
    const confirmed = window.confirm(`Are you sure you want to ${verb} user ${user.username}?`)
    if (!confirmed) return
    setBusy(true)
    setError(null)
    try {
      const updated = await api.updateUserStatus(user.user_id, target)
      setUser(updated)
      setFlash(`User ${updated.username} ${target ? 'reactivated' : 'deactivated'}`)
    } catch (err) {
      setError(err.message)
      api.getUser(userId).then(setUser).catch(() => {})
    } finally {
      setBusy(false)
    }
  }

  if (loading) return <Spinner />
  if (error && !user) {
    return (
      <div>
        <Link className="btn btn-ghost btn-small" to="/users">← Back</Link>
        <ErrorBanner message={error} />
      </div>
    )
  }
  if (!user) return null

  const rows = [
    ['Username', user.username],
    ['Email', user.email],
    ['Coin balance', String(user.coin_balance)],
    ['Winning streak', String(user.winning_streak)],
    ['Status', <span className={`pill ${user.is_active ? 'pill-active' : 'pill-inactive'}`}>{user.is_active ? 'active' : 'inactive'}</span>],
    ['Created', formatDate(user.created_at)],
    ['Updated', formatDate(user.updated_at)],
  ]

  return (
    <div>
      <div className="page-head">
        <h1 className="page-title">{user.username}</h1>
        <Link className="btn btn-ghost btn-small" to="/users">← Back</Link>
      </div>
      {flash && <div className="flash-banner">{flash}</div>}
      <ErrorBanner message={error} onClose={() => setError(null)} />

      <div className="detail-grid">
        <div className="card profile-card">
          <div className="profile-head">
            {user.profile_picture_url ? (
              <img className="avatar avatar-img" src={user.profile_picture_url} alt="" />
            ) : (
              <span className="avatar avatar-lg">{initials(user.username)}</span>
            )}
            <div>
              <div className="card-title">{user.username}</div>
              <div className="muted">{user.email}</div>
            </div>
          </div>
          <p className="profile-bio">{user.biography || <span className="muted">No biography.</span>}</p>
        </div>

        <div className="card">
          <table className="table detail-table">
            <tbody>
              {rows.map(([label, value]) => (
                <tr key={label}>
                  <td className="muted">{label}</td>
                  <td>{value}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card actions-card">
        <div className="card-title">Administrative actions</div>
        <p className="muted">
          {isSelf
            ? 'You cannot deactivate your own account from the admin panel.'
            : user.is_active
              ? 'Deactivating revokes this user’s access — they can no longer log in.'
              : 'Reactivating restores this user’s access to the app.'}
        </p>
        {!isSelf && (
          <button
            className={`btn ${user.is_active ? 'btn-danger' : 'btn-primary'}`}
            onClick={changeStatus}
            disabled={busy}
          >
            {busy ? 'Saving…' : user.is_active ? 'Deactivate user' : 'Reactivate user'}
          </button>
        )}
      </div>
    </div>
  )
}

function initials(username) {
  return (username || '?').slice(0, 2).toUpperCase()
}