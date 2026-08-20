import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api'
import CreateQrModal from '../components/CreateQrModal'
import { ErrorBanner, formatDate, Spinner, StatusPill } from '../components/ui'

const PAGE_SIZE = 20

export default function QrCodesPage() {
  const [items, setItems] = useState([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [creating, setCreating] = useState(false)
  const [flash, setFlash] = useState(null)
  const [busyId, setBusyId] = useState(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = { limit: PAGE_SIZE, offset }
      if (status) params.status = status
      const data = await api.listQrs(params)
      setItems(data.items)
      setTotal(data.total)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [offset, status])

  useEffect(() => {
    load()
  }, [load])

  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const page = Math.floor(offset / PAGE_SIZE) + 1

  const onCreated = (created) => {
    setCreating(false)
    setStatus('')
    setOffset(0)
    setFlash(`QR code ${created.code} created`)
    load()
  }

  const changeStatus = async (qr) => {
    const target = qr.status === 'active' ? 'expired' : 'active'
    const verb = target === 'expired' ? 'deactivate' : 'reactivate'
    const confirmed = window.confirm(
      `Are you sure you want to ${verb} QR code ${qr.code}?\n\n` +
        (target === 'expired'
          ? 'A deactivated code can no longer be redeemed by users.'
          : 'A reactivated code becomes redeemable again.'),
    )
    if (!confirmed) return

    setBusyId(qr.qr_id)
    setError(null)
    try {
      await api.updateQrStatus(qr.qr_id, target)
      setFlash(`QR code ${qr.code} ${target === 'expired' ? 'deactivated' : 'reactivated'}`)
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
        <h1 className="page-title">QR Codes</h1>
        <div className="page-actions">
          <button className="btn btn-ghost" onClick={load} disabled={loading}>
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
          <button className="btn btn-primary" onClick={() => setCreating(true)}>
            Create QR
          </button>
        </div>
      </div>

      {flash && <div className="flash-banner">{flash}</div>}
      <ErrorBanner message={error} onClose={() => setError(null)} />

      <div className="toolbar">
        <label className="field field-inline">
          <span>Status</span>
          <select value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0) }}>
            <option value="">All</option>
            <option value="active">Active</option>
            <option value="expired">Expired</option>
            <option value="redeemed">Redeemed</option>
          </select>
        </label>
        <span className="muted toolbar-total">{total} QR code(s)</span>
      </div>

      {loading ? (
        <Spinner />
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>QR code</th>
                <th>Product</th>
                <th>Coins</th>
                <th>Status</th>
                <th>Expiration</th>
                <th>Redeemed by</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 && (
                <tr>
                  <td colSpan="8" className="table-empty">
                    No QR codes found
                  </td>
                </tr>
              )}
              {items.map((qr) => (
                <tr key={qr.qr_id}>
                  <td className="mono">{qr.code}</td>
                  <td>
                    {qr.product.name}
                    <div className="muted mono">{qr.product.sku}</div>
                  </td>
                  <td>{qr.coin_value}</td>
                  <td><StatusPill status={qr.status} /></td>
                  <td>{formatDate(qr.expires_at)}</td>
                  <td>
                    {qr.redeemed_by ? (
                      <>
                        {qr.redeemed_by.username}
                        <div className="muted">{formatDate(qr.redeemed_at)}</div>
                      </>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td>{formatDate(qr.created_at)}</td>
                  <td className="table-actions">
                    <Link className="btn btn-ghost btn-small" to={`/qr-codes/${qr.qr_id}`}>
                      View
                    </Link>
                    {qr.status !== 'redeemed' && (
                      <button
                        className={`btn btn-small ${qr.status === 'active' ? 'btn-danger' : 'btn-ghost'}`}
                        onClick={() => changeStatus(qr)}
                        disabled={busyId === qr.qr_id}
                      >
                        {qr.status === 'active' ? 'Deactivate' : 'Reactivate'}
                      </button>
                    )}
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

      {creating && <CreateQrModal onCreated={onCreated} onClose={() => setCreating(false)} />}
    </div>
  )
}