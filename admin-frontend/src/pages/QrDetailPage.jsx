import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import { ErrorBanner, formatDate, Spinner, StatusPill } from '../components/ui'

export default function QrDetailPage() {
  const { qrId } = useParams()
  const [qr, setQr] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [flash, setFlash] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    api
      .getQr(qrId)
      .then((data) => {
        if (!cancelled) setQr(data)
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
  }, [qrId])

  const changeStatus = async () => {
    const target = qr.status === 'active' ? 'expired' : 'active'
    const verb = target === 'expired' ? 'deactivate' : 'reactivate'
    const confirmed = window.confirm(`Are you sure you want to ${verb} QR code ${qr.code}?`)
    if (!confirmed) return
    setBusy(true)
    setError(null)
    try {
      const updated = await api.updateQrStatus(qr.qr_id, target)
      setQr(updated)
      setFlash(`QR code ${updated.code} ${target === 'expired' ? 'deactivated' : 'reactivated'}`)
    } catch (err) {
      setError(err.message)
      api.getQr(qrId).then(setQr).catch(() => {})
    } finally {
      setBusy(false)
    }
  }

  if (loading) return <Spinner />
  if (error && !qr) {
    return (
      <div>
        <Link className="btn btn-ghost btn-small" to="/qr-codes">← Back</Link>
        <ErrorBanner message={error} />
      </div>
    )
  }
  if (!qr) return null

  const rows = [
    ['QR code', <span className="mono">{qr.code}</span>],
    ['Product', `${qr.product.name} (${qr.product.sku})`],
    ['Coin value', String(qr.coin_value)],
    ['Status', <StatusPill status={qr.status} />],
    ['Created', formatDate(qr.created_at)],
    ['Expiration', formatDate(qr.expires_at)],
  ]

  return (
    <div>
      <div className="page-head">
        <h1 className="page-title mono">{qr.code}</h1>
        <Link className="btn btn-ghost btn-small" to="/qr-codes">← Back</Link>
      </div>
      {flash && <div className="flash-banner">{flash}</div>}
      <ErrorBanner message={error} onClose={() => setError(null)} />

      <div className="detail-grid">
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

        <div className="card">
          <div className="card-title">Redemption</div>
          {qr.redeemed_by ? (
            <table className="table detail-table">
              <tbody>
                <tr>
                  <td className="muted">Redeemed by</td>
                  <td>
                    {qr.redeemed_by.username}
                    <div className="muted mono">{qr.redeemed_by.user_id}</div>
                  </td>
                </tr>
                <tr>
                  <td className="muted">Redeemed at</td>
                  <td>{formatDate(qr.redeemed_at)}</td>
                </tr>
              </tbody>
            </table>
          ) : (
            <p className="muted">This code has not been redeemed.</p>
          )}
        </div>
      </div>

      {qr.status !== 'redeemed' && (
        <div className="card actions-card">
          <div className="card-title">Administrative actions</div>
          <p className="muted">
            {qr.status === 'active'
              ? 'Deactivating makes this code no longer redeemable by users.'
              : 'Reactivating makes this code redeemable again (if not past its expiration).'}
          </p>
          <button className={`btn ${qr.status === 'active' ? 'btn-danger' : 'btn-primary'}`} onClick={changeStatus} disabled={busy}>
            {busy ? 'Saving…' : qr.status === 'active' ? 'Deactivate QR code' : 'Reactivate QR code'}
          </button>
        </div>
      )}
    </div>
  )
}