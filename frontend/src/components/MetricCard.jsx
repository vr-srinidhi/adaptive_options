export default function MetricCard({ label, value, subtext, color, onClick }) {
  return (
    <div
      className={`p-4 rounded-lg flex flex-col gap-1 ${onClick ? 'cursor-pointer hover:opacity-80 transition-opacity' : ''}`}
      style={{ background: 'var(--surface-secondary)', border: '1px solid var(--border)' }}
      onClick={onClick}
    >
      <span className="text-xs uppercase tracking-widest" style={{ color: 'var(--text-secondary)' }}>
        {label}
      </span>
      <span className="text-xl font-bold" style={{ color: color || 'var(--text-primary)' }}>
        {value}
      </span>
      {subtext && (
        <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{subtext}</span>
      )}
    </div>
  )
}
