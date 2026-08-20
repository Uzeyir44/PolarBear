import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { ErrorBanner, formatDate, Spinner } from '../components/ui'
import ProductFormModal from '../components/ProductFormModal'

export default function ProductDetailPage() {
  const { productId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const [product, setProduct] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [flash, setFlash] = useState(null)
  const [busy, setBusy] = useState(false)

  const showEdit = searchParams.get('edit') === '1'

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .getProduct(productId)
      .then((data) => {
        if (!cancelled) setProduct(data)
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
  }, [productId])

  const closeEdit = () => {
    setSearchParams({})
  }

  const saveProduct = async (values) => {
    const updated = await api.updateProduct(productId, values)
    setProduct(updated)
    setFlash(`Product ${updated.name} saved`)
    closeEdit()
  }

  const deleteProduct = async () => {
    if (!product) return
    const confirmed = window.confirm(
      `Are you sure you want to delete product "${product.name}" (${product.sku})?\n\n` +
        'This is permanent. Products referenced by QR codes cannot be deleted.',
    )
    if (!confirmed) return
    setBusy(true)
    setError(null)
    try {
      await api.deleteProduct(productId)
      navigate('/products')
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  if (loading) return <Spinner />
  if (error && !product) {
    return (
      <div>
        <Link className="btn btn-ghost btn-small" to="/products">← Back</Link>
        <ErrorBanner message={error} />
      </div>
    )
  }
  if (!product) return null

  const referenced = product.qr_code_count > 0

  const rows = [
    ['Product ID', product.product_id],
    ['Name', product.name],
    ['SKU', product.sku],
    ['QR code references', product.qr_code_count],
    ['Created', formatDate(product.created_at)],
  ]

  return (
    <div>
      <div className="page-head">
        <h1 className="page-title">{product.name}</h1>
        <Link className="btn btn-ghost btn-small" to="/products">← Back</Link>
      </div>
      {flash && <div className="flash-banner">{flash}</div>}
      <ErrorBanner message={error} onClose={() => setError(null)} />

      <div className="detail-grid">
        <div className="card profile-card">
          <div className="profile-head">
            <span className="avatar avatar-lg pill-refs">{product.sku.slice(0, 2).toUpperCase()}</span>
            <div>
              <div className="card-title">{product.name}</div>
              <div className="muted">{product.sku}</div>
            </div>
          </div>
          <p className="profile-bio">
            {referenced ? (
              <span className="pill pill-refs">Referenced by {product.qr_code_count} QR code(s)</span>
            ) : (
              <span className="muted">Not referenced by any QR code.</span>
            )}
          </p>
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
          {referenced
            ? 'This product is referenced by QR codes, so it cannot be deleted. You can still edit its name and SKU.'
            : 'A product with no QR references can be deleted. Deletion is permanent.'}
        </p>
        <div className="actions-row">
          <button className="btn btn-primary" onClick={() => setSearchParams({ edit: '1' })}>
            Edit product
          </button>
          <button
            className="btn btn-danger"
            onClick={deleteProduct}
            disabled={busy || referenced}
            title={referenced ? 'Referenced by QR codes — cannot be deleted' : 'Delete product'}
          >
            {busy ? 'Deleting…' : 'Delete product'}
          </button>
        </div>
      </div>

      {showEdit && (
        <ProductFormModal
          title="Edit product"
          submitLabel="Save changes"
          initial={{ name: product.name, sku: product.sku }}
          onSubmit={saveProduct}
          onClose={closeEdit}
        />
      )}
    </div>
  )
}