import { BRAND_LOGO_PATH, BRAND_NAME } from '../constants/brand'

export default function BrandLogo({
  size = 32,
  stacked = false,
  className = '',
  subtitle = null,
  wordmarkClassName = '',
}) {
  return (
    <div className={`inline-flex ${stacked ? 'flex-col text-center' : 'items-center'} gap-3 ${className}`.trim()}>
      <img
        src={BRAND_LOGO_PATH}
        alt={`${BRAND_NAME} logo`}
        width={size}
        height={size}
        draggable={false}
        style={{ width: size, height: size, objectFit: 'contain', flexShrink: 0 }}
      />

      <div className={wordmarkClassName || 'leading-tight'}>
        <div className="font-bold tracking-tight text-slate-100" style={{ fontSize: stacked ? '1.5rem' : '0.95rem' }}>
          <span>Adaptive</span><span className="text-blue-400">Options</span>
        </div>
        {subtitle && (
          <div className="text-xs mt-1" style={{ color: 'var(--text-secondary)' }}>
            {subtitle}
          </div>
        )}
      </div>
    </div>
  )
}
