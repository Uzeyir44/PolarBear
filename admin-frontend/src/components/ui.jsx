export function ErrorBanner({ message, onClose }) {
  if (!message) return null
  return (
    <div className="error-banner" role="alert">
      <span>{message}</span>
      {onClose && (
        <button className="btn btn-ghost btn-small" onClick={onClose}>
          ×
        </button>
      )}
    </div>
  )
}

export function StatusPill({ status }) {
  return <span className={`pill pill-${status}`}>{status}</span>
}

export function Spinner({ text = 'Loading…' }) {
  return (
    <div className="spinner-block">
      <span className="spinner" />
      <span>{text}</span>
    </div>
  )
}

export function formatDate(value) {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toLocaleString()
}