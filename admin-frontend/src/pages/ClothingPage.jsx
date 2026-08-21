import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import { ErrorBanner, formatDate, Spinner, StatusPill } from '../components/ui'
import ClothingFormModal from '../components/ClothingFormModal'

const PAGE_SIZE = 20

export default function ClothingPage() {
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [categoryId, setCategoryId] = useState('')
  const [availability, setAvailability] = useState('')
  const [categories, setCategories] = useState([])
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

  useEffect(() => {
    api
      .clothingCategories()
      .then(setCategories)
      .catch((err) => setError(err.message))
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = { limit: PAGE_SIZE, offset }
      if (debouncedQuery) params.q = debouncedQuery
      if (categoryId) params.categoryId = categoryId
      if (availability) params.availability = availability
      const data = await api.listClothing(params)
      setItems(data.items)
      setTotal(data.total)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [offset, debouncedQuery, categoryId, availability])

  useEffect(() => {
    load()
  }, [load])

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const page = Math.floor(offset / PAGE_SIZE) + 1

  const deleteItem = async (item) => {
    const confirmed = window.confirm(
      `Are you sure you want to delete clothing item "${item.name}"?\n\n` +
        'This is permanent. Items owned or worn by users cannot be deleted — ' +
        'mark them UNAVAILABLE instead.',
    )
    if (!confirmed) return

    setBusyId(item.item_id)
    setError(null)
    try {
      await api.deleteClothing(item.item_id)
      setFlash(`Clothing item ${item.name} deleted`)
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
        <h1 className="page-title">Clothing</h1>
        <div className="page-actions">
          <button className="btn btn-ghost" onClick={load} disabled={loading}>
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
          <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
            New clothing
          </button>
        </div>
      </div>

      {flash && <div className="flash-banner">{flash}</div>}
      <ErrorBanner message={error} onClose={() => setError(null)} />

      <div className="toolbar">
        <label className="field field-inline search-field">
          <span>Search</span>
          <input
            type="search"
            placeholder="Name or description…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <label className="field field-inline">
          <span>Category</span>
          <select value={categoryId} onChange={(e) => { setCategoryId(e.target.value); setOffset(0) }}>
            <option value="">All</option>
            {categories.map((c) => (
              <option key={c.category_id} value={c.category_id}>
                {c.category_name}
              </option>
            ))}
          </select>
        </label>
        <label className="field field-inline">
          <span>Availability</span>
          <select value={availability} onChange={(e) => { setAvailability(e.target.value); setOffset(0) }}>
            <option value="">All</option>
            <option value="available">Available</option>
            <option value="unavailable">Unavailable</option>
            <option value="upcoming">Upcoming</option>
          </select>
        </label>
        <span className="muted toolbar-total">{total} item(s)</span>
      </div>

      {loading ? (
        <Spinner />
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Item</th>
                <th>Category</th>
                <th>Slot</th>
                <th>Price</th>
                <th>Availability</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 && (
                <tr>
                  <td colSpan="7" className="table-empty">
                    No clothing items found
                  </td>
                </tr>
              )}
              {items.map((item) => (
                <tr key={item.item_id}>
                  <td>
                    {item.image_url && (
                      <img
                        className="avatar avatar-img table-thumb"
                        src={item.image_url}
                        alt=""
                        onError={(e) => { e.currentTarget.style.display = 'none' }}
                      />
                    )}
                    {item.name}
                  </td>
                  <td>{item.category_name}</td>
                  <td className="muted">{item.slot}</td>
                  <td>{item.price}</td>
                  <td><StatusPill status={item.availability_status} /></td>
                  <td>{formatDate(item.created_at)}</td>
                  <td className="table-actions">
                    <Link className="btn btn-ghost btn-small" to={`/clothing/${item.item_id}`}>
                      View
                    </Link>
                    <Link className="btn btn-ghost btn-small" to={`/clothing/${item.item_id}?edit=1`}>
                      Edit
                    </Link>
                    <button
                      className="btn btn-danger btn-small"
                      onClick={() => deleteItem(item)}
                      disabled={busyId === item.item_id}
                      title={
                        item.availability_status !== 'available'
                          ? 'Also consider marking it AVAILABLE again instead of deleting'
                          : 'Delete clothing item'
                      }
                    >
                      {busyId === item.item_id ? 'Deleting…' : 'Delete'}
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
        <ClothingFormModal
          title="New clothing item"
          submitLabel="Create clothing"
          onSubmit={async (values) => {
            const created = await api.createClothing(values)
            setFlash(`Clothing item ${created.name} created`)
            setShowCreate(false)
            setQuery('')
            setDebouncedQuery('')
            setCategoryId('')
            setAvailability('')
            setOffset(0)
            load()
          }}
          onClose={() => setShowCreate(false)}
        />
      )}
    </div>
  )
}
