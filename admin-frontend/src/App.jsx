import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './auth'
import RequireAdmin from './RequireAdmin'
import Layout from './components/Layout'
import LoginPage from './pages/LoginPage'
import DashboardPage from './pages/DashboardPage'
import QrCodesPage from './pages/QrCodesPage'
import QrDetailPage from './pages/QrDetailPage'
import UsersPage from './pages/UsersPage'
import UserDetailPage from './pages/UserDetailPage'
import ProductsPage from './pages/ProductsPage'
import ProductDetailPage from './pages/ProductDetailPage'
import ClothingPage from './pages/ClothingPage'
import ClothingDetailPage from './pages/ClothingDetailPage'

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/"
            element={
              <RequireAdmin>
                <Layout />
              </RequireAdmin>
            }
          >
            <Route index element={<Navigate to="/dashboard" replace />} />
            <Route path="dashboard" element={<DashboardPage />} />
            <Route path="qr-codes" element={<QrCodesPage />} />
            <Route path="qr-codes/:qrId" element={<QrDetailPage />} />
            <Route path="users" element={<UsersPage />} />
            <Route path="users/:userId" element={<UserDetailPage />} />
            <Route path="products" element={<ProductsPage />} />
            <Route path="products/:productId" element={<ProductDetailPage />} />
            <Route path="clothing" element={<ClothingPage />} />
            <Route path="clothing/:itemId" element={<ClothingDetailPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  )
}