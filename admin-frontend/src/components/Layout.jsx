import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import { useAuth } from '../auth'

export default function Layout() {
  const { admin, logout } = useAuth()
  const navigate = useNavigate()

  const items = [
    { to: '/dashboard', label: 'Dashboard', end: true },
    { to: '/qr-codes', label: 'QR Codes', end: false },
    { to: '/products', label: 'Products', end: false },
    { to: '/clothing', label: 'Clothing', end: false },
    { to: '/users', label: 'Users', end: false },
  ]

  const handleLogout = () => {
    logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar-brand">MyColaBear Admin</div>
        <nav className="sidebar-nav">
          {items.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="sidebar-admin">
            <div className="sidebar-admin-name">{admin?.username || '…'}</div>
            <div className="sidebar-admin-email">{admin?.email || ''}</div>
          </div>
          <button className="btn btn-ghost sidebar-logout" onClick={handleLogout}>
            Log out
          </button>
        </div>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  )
}