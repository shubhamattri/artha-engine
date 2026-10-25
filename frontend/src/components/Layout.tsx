import { useEffect, useState } from 'react'
import { NAV } from '../nav'
import { useConfig } from '../api/meta'
import { useQuery } from '@tanstack/react-query'
import { NavLink, Outlet, Link, useLocation } from 'react-router-dom'
import { logout } from '../api/auth'
import { fetchMembers } from '../api/client'
import { useDisplayCurrency } from '../api/meta'
import { BottomNav, Sheet, Icon } from './ui'
import Logo from './ui/Logo'
import AffordSheet from './AffordSheet'
import OnboardingGate from './OnboardingGate'
import { useAuth } from '../auth/AuthContext'
import { useCan } from '../auth/Can'



async function doLogout() {
  try { await logout() } finally { window.location.assign('/login') }
}

export default function Layout() {
  // Mounted once, above every authenticated page, so formatINR/shortINR render this household's
  // currency instead of a hardcoded rupee.
  useDisplayCurrency()
  // Surfaced in the menu so an incomplete profile is visible without opening it — the whole failure
  // mode here was a required thing being invisible until something silently did not work.
  const membersQ = useQuery({ queryKey: ['members'], queryFn: fetchMembers, retry: false })
  const profileIncomplete = (membersQ.data?.members ?? [])
    .filter((m) => !m.is_household && (m.identity_missing_required?.length ?? 0) > 0).length
  const { user: me, managesUsers } = useAuth()
  const [menu, setMenu] = useState(false)
  const [afford, setAfford] = useState(false)
  const initial = (me?.name ?? 'A').trim().charAt(0).toUpperCase()
  // The brand name is household config, exactly as ui/Logo already resolves it. Hardcoding it
  // here meant a rebranded deploy renamed the logo and left this reading "Ask Artha".
  const brand = useConfig().data?.brand_name ?? 'Artha'

  // The FAB opens the quick "Log a spend" sheet (affordability lives in Ask Artha now) — so it's a
  // The daily-money action, and the gate was inverted on BOTH counts. It tested `/dashboard`, which
  // now redirects to `/today`, so the FAB was absent from the one screen that opens ten times a day.
  // And it showed on `/spending`, which the design forbids outright: every row there is already
  // tappable, so a floating button occludes the list it duplicates.
  // No FAB for someone who cannot log a spend — it opens a sheet whose only button would be refused.
  const mayEdit = useCan('spending', 'edit')
  const { pathname } = useLocation()
  const showAfford = mayEdit && (pathname === '/' || pathname.startsWith('/today'))

  /**
   * Collapse the FAB to icon-only while scrolling DOWN, expand on the way up or at the top.
   *
   * 160px of pill against 52px of icon is 67% less of the content covered, and the moment you are
   * scrolling down is exactly the moment you are reading rather than logging. Local state, per the
   * handoff — nothing else in the app needs to know, and a global would make an unrelated re-render
   * able to pop the button open mid-scroll.
   */
  const [fabOpen, setFabOpen] = useState(true)
  useEffect(() => {
    const el = document.getElementById('app-scroll')
    if (!el) return
    let last = el.scrollTop
    const onScroll = () => {
      const y = el.scrollTop
      // A threshold, because a 1px jitter should not toggle a 108px width change.
      if (y <= 8) setFabOpen(true)
      else if (y - last > 6) setFabOpen(false)
      else if (last - y > 6) setFabOpen(true)
      last = y
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])
  // A tab change re-mounts nothing, so the button would stay collapsed on a fresh screen at scroll 0.
  useEffect(() => { setFabOpen(true) }, [pathname])

  return (
    // The gate renders OVER the shell when a cardholder is missing the details banks need to unlock
    // statements. It renders children untouched once satisfied, dismissed, or if the lookup fails.
    <OnboardingGate>
    {/* Fixed-height app shell: header + bottom nav are pinned; ONLY <main> scrolls. This stops iOS
        overscroll from dragging the footer/FAB around. 100dvh tracks the dynamic viewport. */}
    <div className="grain flex flex-col overflow-hidden bg-bg text-ink" style={{ height: '100dvh' }}>
      {/* Top bar (pinned), 52px per the handoff: 4 + 44 + 4. The padding shrank rather than the
          children — every child here is a 44px tap target, so squeezing them to fit would have
          traded iOS's minimum for the design's bar height. The safe-area inset still wins on
          notched devices. */}
      <header className="safe-x z-30 flex shrink-0 items-center justify-between border-b border-line bg-bg/90 px-5 backdrop-blur-md"
        style={{ paddingTop: 'max(4px,env(safe-area-inset-top))', paddingBottom: '4px' }}>
        <Link to="/today" className="inline-flex min-h-11 items-center"><Logo size={32} /></Link>
        {/* desktop nav */}
        <nav className="hidden items-center gap-1 md:flex">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to}
              className={({ isActive }) => `rounded-lg px-3 py-2 text-sm font-medium ${isActive ? 'bg-accent-soft text-accent' : 'text-ink-2 hover:text-ink'}`}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="flex items-center gap-2">
          <Link to="/ask" className="inline-flex min-h-11 items-center rounded-full bg-accent px-4 text-xs font-bold text-white hover:bg-accent-2">
            ✦ Ask {brand}
          </Link>
          <button onClick={() => setMenu(true)} aria-label="Profile"
            className="flex min-h-11 min-w-11 items-center justify-center rounded-full text-sm font-bold text-accent">
            {/* The disc stays 36px; the button around it carries the 44px hit area. */}
            <span className="flex h-9 w-9 items-center justify-center rounded-full bg-accent-soft">{initial}</span>
          </button>
        </div>
      </header>

      {/* Scroll region. The inner band is the APP-LEVEL max-width shell: content centers at
          `content` (1200px) with real gutters on desktop instead of filling 100% then stranding
          a ~640px column in the middle. Pages own their vertical rhythm (see ui/PageContainer). */}
      {/* 88px = FAB 52 + inset 14x2 + 8 of breathing, at EVERY width. It was md:pb-10, and the FAB
          has no breakpoint gate, so on desktop the button floated over content with 40px of room. */}
      <main id="app-scroll" className="flex-1 overflow-y-auto overscroll-contain pb-[88px]">
        <div className="mx-auto w-full max-w-content">
          <Outlet />
        </div>
      </main>

      {/* contextual quick action — a LABELED "Log spend" pill (not a bare "+"), shown only on the
          daily-money screens so its meaning is unambiguous and it stops overlapping card actions on
          Debt/Wealth/Plan. */}
      {showAfford && (
        <button
          onClick={() => setAfford(true)}
          aria-label="Log spend"
          className={`safe-x fixed z-40 flex items-center justify-center rounded-full bg-accent font-bold text-white transition-all duration-200 hover:bg-accent-2 active:scale-95 ${
            fabOpen ? 'gap-2 pl-4 pr-5 text-sm' : ''}`}
          style={{
            right: 14,
            bottom: 'calc(env(safe-area-inset-bottom) + 76px)',
            height: 52,
            width: fabOpen ? undefined : 52,
            // The system shadow, not a glowing green one. `shadow-accent/30` painted a coloured halo
            // that exists nowhere else in the app.
            boxShadow: 'var(--shadow)',
          }}
        >
          <Icon name="plus" size={18} />
          {fabOpen && 'Log spend'}
        </button>
      )}
      <AffordSheet open={afford} onClose={() => setAfford(false)} />

      <BottomNav />

      <Sheet open={menu} onClose={() => setMenu(false)} title={me?.name ? `Signed in as ${me.name}` : 'Account'}>
        <div className="space-y-2">
          {/* Profile first and named the obvious thing. The identity form used to live only at the
              bottom of Settings under "Statement access", which nobody looking for a profile finds. */}
          <Link to="/profile" onClick={() => setMenu(false)}
            className="flex w-full items-center justify-between gap-3 rounded-xl border border-line px-4 py-3 text-left text-sm font-medium text-ink hover:border-line-2">
            <span className="flex items-center gap-3"><Icon name="home" size={18} /> Profile</span>
            {profileIncomplete > 0 && (
              <span className="rounded-chip bg-warn/15 px-2 py-0.5 text-[10px] font-bold text-warn">
                {profileIncomplete} to fill
              </span>
            )}
          </Link>
          <Link to="/settings" onClick={() => setMenu(false)}
            className="flex w-full items-center gap-3 rounded-xl border border-line px-4 py-3 text-left text-sm font-medium text-ink hover:border-line-2">
            <Icon name="card" size={18} /> Accounts &amp; cards
          </Link>
          <Link to="/spending" onClick={() => setMenu(false)}
            className="flex w-full items-center gap-3 rounded-xl border border-line px-4 py-3 text-left text-sm font-medium text-ink hover:border-line-2">
            <Icon name="upload" size={18} /> Upload statements
          </Link>
          <Link to="/history" onClick={() => setMenu(false)}
            className="flex w-full items-center gap-3 rounded-xl border border-line px-4 py-3 text-left text-sm font-medium text-ink hover:border-line-2">
            <Icon name="list" size={18} /> History
          </Link>
          {/* Only the super admin sees this, and only they can use it — the server refuses the
              /admin routes for anyone else. Hiding it keeps the menu honest rather than keeping
              anyone out. */}
          {managesUsers && (
            <Link to="/admin" onClick={() => setMenu(false)}
              className="flex w-full items-center gap-3 rounded-xl border border-line px-4 py-3 text-left text-sm font-medium text-ink hover:border-line-2">
              <Icon name="shield" size={18} /> People &amp; permissions
            </Link>
          )}
          <button onClick={doLogout}
            className="flex w-full items-center gap-3 rounded-xl border border-line px-4 py-3 text-left text-sm font-medium text-negative hover:border-line-2">
            <Icon name="arrow" size={18} /> Sign out
          </button>
        </div>
      </Sheet>
    </div>
    </OnboardingGate>
  )
}
