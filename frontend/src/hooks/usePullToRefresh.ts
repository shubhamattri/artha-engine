import { useEffect, useRef, useState } from 'react'

/** Native-feeling pull-to-refresh: pull down at the top of the page past a threshold to fire
 *  onRefresh (which should refresh ALL app data). Touch-only — no-op on desktop. */
export function usePullToRefresh(onRefresh: () => Promise<unknown>) {
  const [pull, setPull] = useState(0)
  const [refreshing, setRefreshing] = useState(false)
  const pullRef = useRef(0)
  const startY = useRef<number | null>(null)
  const refreshingRef = useRef(false)

  const set = (v: number) => { pullRef.current = v; setPull(v) }

  useEffect(() => {
    const THRESHOLD = 64  // damped px needed to trigger
    const el = document.getElementById('app-scroll')  // the scroll container (Layout's <main>)
    if (!el) return
    const atTop = () => el.scrollTop <= 0

    const onStart = (e: TouchEvent) => {
      startY.current = atTop() && !refreshingRef.current ? e.touches[0].clientY : null
    }
    const onMove = (e: TouchEvent) => {
      if (startY.current == null) return
      const dy = e.touches[0].clientY - startY.current
      if (dy > 0 && atTop()) set(Math.min(dy * 0.45, 90))
      else set(0)
    }
    const onEnd = async () => {
      if (startY.current == null) return
      startY.current = null
      if (pullRef.current >= THRESHOLD && !refreshingRef.current) {
        refreshingRef.current = true; setRefreshing(true); set(48)
        try { await onRefresh() } finally { refreshingRef.current = false; setRefreshing(false); set(0) }
      } else set(0)
    }

    el.addEventListener('touchstart', onStart, { passive: true })
    el.addEventListener('touchmove', onMove, { passive: true })
    el.addEventListener('touchend', onEnd)
    return () => {
      el.removeEventListener('touchstart', onStart)
      el.removeEventListener('touchmove', onMove)
      el.removeEventListener('touchend', onEnd)
    }
  }, [onRefresh])

  return { pull, refreshing }
}
