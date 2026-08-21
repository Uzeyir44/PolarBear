import { useEffect, useState } from 'react'
import { api } from '../api'
import { ErrorBanner } from './ui'

// Shared modal form for creating and editing a clothing item. The category
// dropdown is populated from the backend's clothing_categories lookup rows —
// the admin never types a category id, and the slot is inherited from the
// selected category (never editable here). item_id/created_at are
// database-owned and never sent.
export default function ClothingFormModal({ initial = {}, title, submitLabel, onSubmit, onClose }) {
  const [categories, setCategories] = useState([])
  const [name, setName] = useState(initial.name || '')
  const [description, setDescription] = useState(initial.description || '')
  const [categoryId, setCategoryId] = useState(initial.category_id || '')
  const [price, setPrice] = useState(initial.price ?? '')
  const [imageUrl, setImageUrl] = useState(initial.image_url || '')
  const [availability, setAvailability] = useState(initial.availability_status || 'available')
  const [collectionId, setCollectionId] = useState(initial.collection_id || '')
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)
  const [loadingCategories, setLoadingCategories] = useState(true)

  useEffect(() => {
    api
      .clothingCategories()
      .then((data) => {
        setCategories(data)
        // Default to the first category so the dropdown has a real selection.
        if (data.length > 0) setCategoryId((current) => current || data[0].category_id)
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoadingCategories(false))
  }, [])

  const selectedCategory = categories.find((c) => String(c.category_id) === String(categoryId))

  const submit = async (event) => {
    event.preventDefault()
    setError(null)
    if (!categoryId) {
      setError('Please select a category first.')
      return
    }
    setSubmitting(true)
    try {
      const body = {
        name: name.trim(),
        description: description.trim() === '' ? null : description.trim(),
        category_id: Number(categoryId),
        price: Number(price),
        image_url: imageUrl.trim(),
        availability_status: availability,
      }
      if (collectionId.trim() !== '') body.collection_id = collectionId.trim()
      await onSubmit(body)
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
            <span>Category</span>
            <select
              value={categoryId}
              onChange={(e) => setCategoryId(e.target.value)}
              required
              disabled={loadingCategories || categories.length === 0}
            >
              {loadingCategories && <option value="">Loading categories…</option>}
              {!loadingCategories && categories.length === 0 && (
                <option value="">No categories available</option>
              )}
              {categories.map((c) => (
                <option key={c.category_id} value={c.category_id}>
                  {c.category_name} ({c.slot})
                </option>
              ))}
            </select>
            {selectedCategory && (
              <small className="field-hint">Equips into the “{selectedCategory.slot}” slot.</small>
            )}
          </label>
          <label className="field">
            <span>Price (coins)</span>
            <input
              type="number"
              min="0"
              step="1"
              value={price}
              onChange={(e) => setPrice(e.target.value)}
              required
            />
          </label>
          <label className="field">
            <span>Image URL</span>
            <input
              type="url"
              value={imageUrl}
              onChange={(e) => setImageUrl(e.target.value)}
              maxLength={2048}
              placeholder="https://…"
              required
            />
          </label>
          <label className="field">
            <span>Availability</span>
            <select value={availability} onChange={(e) => setAvailability(e.target.value)}>
              <option value="available">Available</option>
              <option value="unavailable">Unavailable</option>
              <option value="upcoming">Upcoming</option>
            </select>
          </label>
          <label className="field">
            <span>Description (optional)</span>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={1000}
              rows={3}
            />
          </label>
          <label className="field">
            <span>Collection ID (optional)</span>
            <input
              type="text"
              value={collectionId}
              onChange={(e) => setCollectionId(e.target.value)}
              placeholder="UUID"
            />
            <small className="field-hint">Reserved for future collections — leave empty.</small>
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
