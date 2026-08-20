import { useState } from 'react'
import { ErrorBanner } from './ui'

// Shared modal form for creating and editing a product. Only name and SKU
// are editable — product_id and created_at are database-owned and never
// sent to the backend.
export default function ProductFormModal({ initial = {}, title, submitLabel, onSubmit, onClose }) {
  const [name, setName] = useState(initial.name || '')
  const [sku, setSku] = useState(initial.sku || '')
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  const submit = async (event) => {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await onSubmit({ name: name.trim(), sku: sku.trim() })
    } catch (err) {
      setError(err.message)
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="btn btn-ghost btn-small" onClick={onClose}>
            ×
          </button>
        </div>
        <ErrorBanner message={error} onClose={() => setError(null)} />
        <form onSubmit={submit}>
          <label className="field">
            <span>Name</span>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={255}
              required
              autoFocus
            />
          </label>
          <label className="field">
            <span>SKU</span>
            <input
              type="text"
              value={sku}
              onChange={(e) => setSku(e.target.value)}
              maxLength={100}
              required
            />
            <small className="field-hint">Must be unique — the backend rejects duplicates.</small>
          </label>
          <div className="modal-actions">
            <button type="button" className="btn btn-ghost" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={submitting}>
              {submitting ? 'Saving…' : submitLabel}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}