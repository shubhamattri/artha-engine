import { NavLink } from 'react-router-dom'
import Icon from './Icon'
import { NAV as TABS } from '../../nav'

export default function BottomNav() {
  // The tabs share the width, so the label size has to follow the tab COUNT. Past six, a 320px phone
  // gives each tab ~45px and an 11px label clips mid-word. Derived from the list rather than
  // hand-tuned, so adding a tab cannot silently break the one next to it.
  const tight = TABS.length > 6
  return (
    <nav className="safe-x z-40 flex shrink-0 border-t border-line bg-surface pb-[max(6px,env(safe-area-inset-bottom))] pt-1.5 md:hidden">
      {TABS.map((t) => (
        <NavLink
          key={t.to}
          to={t.to}
          className={({ isActive }) =>
            `flex min-h-[52px] min-w-0 flex-1 flex-col items-center justify-center px-0.5 font-semibold ${
              tight ? 'gap-0.5 text-[10px] tracking-tight' : 'gap-1 text-[11px]'
            } ${isActive ? 'text-accent' : 'text-ink-3'}`
          }
        >
          <Icon name={t.icon} size={tight ? 20 : 21} />
          <span className="max-w-full truncate">{t.label}</span>
        </NavLink>
      ))}
    </nav>
  )
}
