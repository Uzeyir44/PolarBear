import { useEffect, useState } from 'react'
import { api } from '../api'
import { ErrorBanner } from './ui'

// Modal form for generating a new QR code. Fetches the read-only product
// list for the dropdown; submits to POST /admin/qr-codes.
export default function CreateQrModal({ onCreated, onClose }) {
  const [products, setProducts] = useState([])
  const [productId, setProductId] = useState('')
  const [coinValue, setCoinValue] = useState('')
  const [expiresAt, setExpiresAt] = useState('')
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    api
      .products()
      .then((data) => {
        setProducts(data.items)
        if (data.items.length === 1) setProductId(data.items[0].product_id)
      })
      .catch((err) => setError(err.message))
  }, [])

  const submit = async (event) => {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      const body = { product_id: productId, coin_value: Number(coinValue) }
      if (expiresAt) body.expires_at = new Date(expiresAt).toISOString()
      const created = await api.createQr(body)
      onCreated(created)
    } catch (err) {
      setError(err.message)
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>Create QR code</h2>
          <button className="btn btn-ghost btn-small" onClick={onClose}>
            ×
          </button>
        </div>
        <ErrorBanner message={error} />
        <form onSubmit={submit}>
          <label className="field">
            <span>Product</span>
            <select
              value={productId}
              onChange={(e) => setProductId(e.target.value)}
              required
              disabled={products.length === 0}
            >
              {products.length === 0 && <option value="">No products available</option>}
              {products.map((p) => (
                <option key={p.product_id} value={p.product_id}>
                  {p.name} ({p.sku})
                </option>
              ))}
            </select>
            {products.length === 0 && (
              <small className="field-hint">
                Add products to the database first — no products exist yet.
              </small>
            )}
          </label>
          <label className="field">
            <span>Coin value</span>
            <input
              type="number"
              min="1"
              step="1"
              value={coinValue}
              onChange={(e) => setCoinValue(e.target.value)}
              required
            />
          </label>
          <label className="field">
            <span>Expiration (optional)</span>
            <input
              type="datetime-local"
              value={expiresAt}
              onChange={(e) => setExpiresAt(e.target.value)}
            />
          </label>
          <div className="modal-actions">
            <button type="button" className="btn btn-ghost" onClick={onClose}>
              Cancel
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={submitting || products.length === 0}
            >
              {submitting ? 'Creating…' : 'Create'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}