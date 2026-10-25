import { useEffect, useRef, useState } from 'react'

/**
 * Terse-noun + info-tooltip idiom: the label stays a short noun, the explanation lives in the tip.
 *
 * Tap to toggle, hover to preview. It used to be hover-only on a 14px target, which meant that on a
 * phone — the primary device for this app — the explanation could not be reached at all. Every one of
 * these tips explains how one of your numbers is worked out, so on mobile the reasoning behind the
 * figures was simply missing rather than merely inconvenient.
 */
export default function InfoTip({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLSpanElement>(null)

  // Tap anywhere else, or press Escape, to dismiss — otherwise an opened tip has no way out on touch.
  useEffect(() => {
    if (!open) return
    const away = (e: MouseEvent | TouchEvent) => {
      if (!wrap.current?.contains(e.target as Node)) setOpen(false)
    }
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('pointerdown', away)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('pointerdown', away)
      document.removeEventListener('keydown', esc)
    }
  }, [open])

  return (
    <span ref={wrap} className="group relative inline-flex align-middle">
      {/* 44px is the tap-target floor. The visible chip stays small; the button around it is what your
          thumb has to hit, so the padding is the fix rather than a bigger glyph. */}
      <button
        type="button"
        aria-expanded={open}
        aria-label="What this means"
        onClick={() => setOpen((o) => !o)}
        className="-my-3 inline-flex min-h-11 min-w-11 items-center justify-center px-1"
      >
        <span className="flex h-4 w-4 items-center justify-center rounded-chip border border-line text-[10px] text-ink-3 group-hover:border-line-2 group-hover:text-ink-2">
          ?
        </span>
      </button>
      <span
        role="tooltip"
        className={`pointer-events-none absolute bottom-full left-1/2 z-20 mb-1 w-56 -translate-x-1/2 rounded-input border border-line bg-surface px-3 py-2 text-xs leading-relaxed text-ink-2 shadow-lg transition-opacity duration-150 ${
          open ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'
        }`}
      >
        {text}
      </span>
    </span>
  )
}
