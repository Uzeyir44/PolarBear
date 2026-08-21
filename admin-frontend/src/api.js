// Minimal API client for the admin panel.
//
// The backend is the source of truth for authorization: every request
// below sends the stored JWT, and any non-2xx (especially 401/403) is
// handled by the caller — we never trust client-side state for access.

const TOKEN_KEY = 'mycolabear_admin_token'

export const getToken = () => localStorage.getItem(TOKEN_KEY)
export const setToken = (token) => localStorage.setItem(TOKEN_KEY, token)
export const clearToken = () => localStorage.removeItem(TOKEN_KEY)

async function request(path, { method = 'GET', body } = {}) {
  const headers = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers.Authorization = `Bearer ${token}`

  const response = await fetch(path, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  const data = await response.json().catch(() => null)

  if (!response.ok) {
    const error = new Error(statusMessage(response, data))
    error.status = response.status
    throw error
  }
  return data
}

function statusMessage(response, data) {
  if (data && data.detail) {
    const detail = data.detail
    return typeof detail === 'string' ? detail : JSON.stringify(detail)
  }
  return `Request failed (${response.status})`
}

export const api = {
  login: (username, password) =>
    request('/auth/login', {
      method: 'POST',
      body: { username, password },
    }),
  adminMe: () => request('/admin/me'),
  listQrs: (params) => {
    const query = new URLSearchParams()
    if (params.status) query.set('status', params.status)
    if (params.productId) query.set('product_id', params.productId)
    query.set('limit', String(params.limit || 20))
    query.set('offset', String(params.offset || 0))
    return request(`/admin/qr-codes?${query.toString()}`)
  },
  createQr: (body) => request('/admin/qr-codes', { method: 'POST', body }),
  getQr: (qrId) => request(`/admin/qr-codes/${qrId}`),
  updateQrStatus: (qrId, status) =>
    request(`/admin/qr-codes/${qrId}`, { method: 'PATCH', body: { status } }),
  products: () => request('/admin/products?limit=100'),
  listProducts: (params) => {
    const query = new URLSearchParams()
    if (params.q) query.set('q', params.q)
    query.set('limit', String(params.limit || 20))
    query.set('offset', String(params.offset || 0))
    return request(`/admin/products?${query.toString()}`)
  },
  getProduct: (productId) => request(`/admin/products/${productId}`),
  createProduct: (body) => request('/admin/products', { method: 'POST', body }),
  updateProduct: (productId, body) =>
    request(`/admin/products/${productId}`, { method: 'PATCH', body }),
  deleteProduct: (productId) => request(`/admin/products/${productId}`, { method: 'DELETE' }),
  listUsers: (params) => {
    const query = new URLSearchParams()
    if (params.q) query.set('q', params.q)
    if (params.isActive !== undefined && params.isActive !== '') query.set('is_active', String(params.isActive))
    query.set('limit', String(params.limit || 20))
    query.set('offset', String(params.offset || 0))
    return request(`/admin/users?${query.toString()}`)
  },
  getUser: (userId) => request(`/admin/users/${userId}`),
  updateUserStatus: (userId, isActive) =>
    request(`/admin/users/${userId}/status`, { method: 'PATCH', body: { is_active: isActive } }),
  clothingCategories: () => request('/admin/clothing/categories'),
  listClothing: (params) => {
    const query = new URLSearchParams()
    if (params.q) query.set('q', params.q)
    if (params.categoryId) query.set('category_id', String(params.categoryId))
    if (params.availability) query.set('availability', params.availability)
    query.set('limit', String(params.limit || 20))
    query.set('offset', String(params.offset || 0))
    return request(`/admin/clothing?${query.toString()}`)
  },
  getClothing: (itemId) => request(`/admin/clothing/${itemId}`),
  createClothing: (body) => request('/admin/clothing', { method: 'POST', body }),
  updateClothing: (itemId, body) =>
    request(`/admin/clothing/${itemId}`, { method: 'PATCH', body }),
  deleteClothing: (itemId) => request(`/admin/clothing/${itemId}`, { method: 'DELETE' }),
}