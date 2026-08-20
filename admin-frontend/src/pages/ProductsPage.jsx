import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { ErrorBanner, formatDate, Spinner } from '../components/ui'
import ProductFormModal from '../components/ProductFormModal'

const PAGE_SIZE = 20

export default function ProductsPage() {
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [flash, setFlash] = useState(null)
  const [busyId, setBusyId] = useState(null)
  const [showCreate, setShowCreate] = useState(false)
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
      const data = await api.listProducts(params)
      setItems(data.items)
      setTotal(data.total)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [offset, debouncedQuery])

  useEffect(() => {
    load()
  }, [load])

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const page = Math.floor(offset / PAGE_SIZE) + 1

  const deleteProduct = async (product) => {
    const confirmed = window.confirm(
      `Are you sure you want to delete product "${product.name}" (${product.sku})?\n\n` +
        'This is permanent. Products referenced by QR codes cannot be deleted.',
    )
    if (!confirmed) return

    setBusyId(product.product_id)
    setError(null)
    try {
      await api.deleteProduct(product.product_id)
      setFlash(`Product ${product.name} deleted`)
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
        <h1 className="page-title">Products</h1>
        <button className="btn btn-ghost" onClick={load} disabled={loading}>
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
        <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
          New product
        </button>
      </div>

      {flash && <div className="flash-banner">{flash}</div>}
      <ErrorBanner message={error} onClose={() => setError(null)} />

      <div className="toolbar">
        <label className="field field-inline search-field">
          <span>Search</span>
          <input
            type="search"
            placeholder="Name or SKU…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <span className="muted toolbar-total">{total} product(s)</span>
      </div>

      {loading ? (
        <Spinner />
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>SKU</th>
                <th>QR refs</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 && (
                <tr>
                  <td colSpan="5" className="table-empty">
                    No products found
                  </td>
                </tr>
              )}
              {items.map((product) => (
                <tr key={product.product_id}>
                  <td>{product.name}</td>
                  <td>{product.sku}</td>
                  <td>
                    <span className="pill pill-refs">{product.qr_code_count}</span>
                  </td>
                  <td>{formatDate(product.created_at)}</td>
                  <td className="table-actions">
                    <Link className="btn btn-ghost btn-small" to={`/products/${product.product_id}`}>
                      View
                    </Link>
                    <Link className="btn btn-ghost btn-small" to={`/products/${product.product_id}?edit=1`}>
                      Edit
                    </Link>
                    <button
                      className="btn btn-danger btn-small"
                      onClick={() => deleteProduct(product)}
                      disabled={busyId === product.product_id || product.qr_code_count > 0}
                      title={
                        product.qr_code_count > 0
                          ? 'Referenced by QR codes — cannot be deleted'
                          : 'Delete product'
                      }
                    >
                      {busyId === product.product_id ? 'Deleting…' : 'Delete'}
                    </button>
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

      {showCreate && (
        <ProductFormModal
          title="New product"
          submitLabel="Create product"
          onSubmit={async (values) => {
            const created = await api.createProduct(values)
            setFlash(`Product ${created.name} created`)
            setShowCreate(false)
            setQuery('')
            setDebouncedQuery('')
            load()
          }}
          onClose={() => setShowCreate(false)}
        />
      )}
    </div>
  )
}