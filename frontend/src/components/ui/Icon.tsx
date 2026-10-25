// Single SVG icon set for the app — no emoji (design rule). Stroke-based, inherits currentColor.
export type IconName =
  | 'home' | 'chart' | 'list' | 'card' | 'wallet' | 'plus' | 'check' | 'x'
  | 'alert' | 'info' | 'chevron' | 'edit' | 'trash' | 'upload' | 'arrow' | 'shield' | 'filter' | 'calendar'

const PATHS: Record<IconName, string> = {
  home: 'M3 11l9-7 9 7M5 10v9h14v-9',
  chart: 'M4 19V5m0 14h16M8 16V9m4 7V6m4 10v-4',
  list: 'M4 6h16M4 12h10M4 18h7',
  card: 'M4 7h16v12H4zM4 11h16',
  wallet: 'M3 7h15a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2zM16 13h2',
  plus: 'M12 5v14M5 12h14',
  check: 'M5 13l4 4L19 7',
  x: 'M6 6l12 12M18 6L6 18',
  alert: 'M12 9v4m0 4h.01M10.3 4.3L2.6 18a2 2 0 001.7 3h15.4a2 2 0 001.7-3L13.7 4.3a2 2 0 00-3.4 0z',
  info: 'M12 8h.01M11 12h1v4h1',
  chevron: 'M9 6l6 6-6 6',
  edit: 'M4 20h4L18 10l-4-4L4 16zM14 6l4 4',
  trash: 'M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13',
  upload: 'M12 16V4m-5 5l5-5 5 5M5 20h14',
  arrow: 'M5 12h14M13 6l6 6-6 6',
  shield: 'M12 3l8 3v6c0 4.5-3.2 7.7-8 9-4.8-1.3-8-4.5-8-9V6z',
  filter: 'M3 5h18l-7 8v6l-4-2v-4z',
  calendar: 'M4 6h16v14H4zM4 10h16M8 3v4M16 3v4',
}

export default function Icon({ name, size = 20, className = '' }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      <path d={PATHS[name]} />
    </svg>
  )
}
