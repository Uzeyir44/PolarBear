import { Link } from 'react-router-dom'
import { useAuth } from '../auth'

export default function DashboardPage() {
  const { admin } = useAuth()
  return (
    <div>
      <h1 className="page-title">Dashboard</h1>
      <p className="muted">
        Welcome back, <strong>{admin?.username}</strong>. Manage MyColaBear from here.
      </p>

      <div className="card-grid">
        <Link className="card card-link" to="/qr-codes">
          <div className="card-title">QR Codes</div>
          <div className="card-desc">Generate, inspect and manage redemption codes.</div>
        </Link>
        <Link className="card card-link" to="/users">
          <div className="card-title">Users</div>
          <div className="card-desc">Search accounts and manage activation status.</div>
        </Link>
        <Link className="card card-link" to="/clothing">
          <div className="card-title">Clothing</div>
          <div className="card-desc">Manage the clothing catalog users browse and buy.</div>
        </Link>
        <Link className="card card-link" to="/products">
          <div className="card-title">Products</div>
          <div className="card-desc">Manage products — QR codes link to these.</div>
        </Link>
        <div className="card card-planned">
          <div className="card-title">Competitions</div>
          <div className="card-desc">Coming in a later release.</div>
        </div>
        <div className="card card-planned">
          <div className="card-title">Notifications</div>
          <div className="card-desc">Coming in a later release.</div>
        </div>
      </div>
    </div>
  )
}