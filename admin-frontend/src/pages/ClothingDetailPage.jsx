import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../api'
import { ErrorBanner, formatDate, Spinner, StatusPill } from '../components/ui'
import ClothingFormModal from '../components/ClothingFormModal'

export default function ClothingDetailPage() {
  const { itemId } = useParams()
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const [item, setItem] = useState(null)
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
      .getClothing(itemId)
      .then((data) => {
        if (!cancelled) setItem(data)
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
  }, [itemId])

  const closeEdit = () => {
    setSearchParams({})
  }

  const saveItem = async (values) => {
    const updated = await api.updateClothing(itemId, values)
    setItem(updated)
    setFlash(`Clothing item ${updated.name} saved`)
    closeEdit()
  }

  const deleteItem = async () => {
    if (!item) return
    const confirmed = window.confirm(
      `Are you sure you want to delete clothing item "${item.name}"?\n\n` +
        'This is permanent. Items owned or worn by users cannot be deleted — ' +
        'mark them UNAVAILABLE instead.',
    )
    if (!confirmed) return
    setBusy(true)
    setError(null)
    try {
      await api.deleteClothing(itemId)
      navigate('/clothing')
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }

  if (loading) return <Spinner />
  if (error && !item) {
    return (
      <div>
        <Link className="btn btn-ghost btn-small" to="/clothing">← Back</Link>
        <ErrorBanner message={error} />
      </div>
    )
  }
  if (!item) return null

  const rows = [
    ['Item ID', item.item_id],
    ['Name', item.name],
    ['Description', item.description || '—'],
    ['Category', `${item.category_name} (#${item.category_id})`],
    ['Slot', item.slot],
    ['Price', `${item.price} coins`],
    ['Availability', <StatusPill status={item.availability_status} />],
    ['Collection ID', item.collection_id || '—'],
    ['Created', formatDate(item.created_at)],
  ]

  return (
    <div>
      <div className="page-head">
        <h1 className="page-title">{item.name}</h1>
        <Link className="btn btn-ghost btn-small" to="/clothing">← Back</Link>
      </div>
      {flash && <div className="flash-banner">{flash}</div>}
      <ErrorBanner message={error} onClose={() => setError(null)} />

      <div className="detail-grid">
        <div className="card profile-card">
          <div className="profile-head">
            {item.image_url ? (
              <img className="avatar avatar-lg avatar-img" src={item.image_url} alt="" />
            ) : (
              <span className="avatar avatar-lg">{item.name.slice(0, 2).toUpperCase()}</span>
            )}
            <div>
              <div className="card-title">{item.name}</div>
              <div className="muted">{item.category_name}</div>
            </div>
          </div>
          <p className="profile-bio">
            <StatusPill status={item.availability_status} />
          </p>
          {item.description && <p>{item.description}</p>}
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
          {item.availability_status === 'available'
            ? 'Marking an item UNAVAILABLE removes it from the shop shelf and blocks purchases without destroying any user\'s ownership history.'
            : 'Only items with no wardrobe/equipment references can be deleted. Otherwise, edit its availability instead.'}
        </p>
        <div className="actions-row">
          <button className="btn btn-primary" onClick={() => setSearchParams({ edit: '1' })}>
            Edit clothing
          </button>
          <button className="btn btn-danger" onClick={deleteItem} disabled={busy}>
            {busy ? 'Deleting…' : 'Delete clothing'}
          </button>
        </div>
      </div>

      {showEdit && (
        <ClothingFormModal
          title="Edit clothing"
          submitLabel="Save changes"
          initial={item}
          onSubmit={saveItem}
          onClose={closeEdit}
        />
      )}
    </div>
  )
}
