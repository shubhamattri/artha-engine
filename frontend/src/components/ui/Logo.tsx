// Brand logomark. Wordmark + name come from /config (brand_wordmark / brand_name), never hardcoded —
// so a white-label deploy rebrands without code. Defaults to the Devanagari "अर्थ" / "Artha".
import { useConfig } from '../../api/meta'

const DEVANAGARI = '"Kohinoor Devanagari","Devanagari Sangam MN","Noto Sans Devanagari",system-ui'

export function LogoMark({ size = 36, glyph = 'अ' }: { size?: number; glyph?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" role="img" aria-label="Artha">
      <defs>
        <linearGradient id="artha-coin" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--accent)" />
          <stop offset="1" stopColor="var(--accent-2)" />
        </linearGradient>
      </defs>
      <rect x="1" y="1" width="38" height="38" rx="11" fill="url(#artha-coin)" />
      {/* upward "growth" notch — wealth rising */}
      <path d="M11 27l6-6 4 3 8-9" fill="none" stroke="#ffffff" strokeOpacity="0.35"
        strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
      <text x="20" y="20" textAnchor="middle" dominantBaseline="central" fill="#ffffff"
        style={{ fontFamily: DEVANAGARI, fontWeight: 700, fontSize: '20px' }}>
        {glyph}
      </text>
    </svg>
  )
}

export default function Logo({ size = 34, showWord = true }: { size?: number; showWord?: boolean }) {
  const { data } = useConfig()
  const wordmark = data?.brand_wordmark ?? 'अर्थ'
  const name = data?.brand_name ?? 'Artha'
  return (
    <span className="inline-flex items-center gap-2.5 align-middle">
      <LogoMark size={size} glyph={wordmark.charAt(0) || 'अ'} />
      {showWord && (
        <span className="leading-none">
          <span className="block text-xl font-bold text-ink" style={{ fontFamily: DEVANAGARI }}>{wordmark}</span>
          <span className="block text-[10px] font-semibold uppercase tracking-[0.22em] text-ink-3">{name}</span>
        </span>
      )}
    </span>
  )
}
