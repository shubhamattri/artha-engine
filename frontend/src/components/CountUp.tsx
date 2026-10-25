import { useEffect, useRef, useState } from 'react'
import { animate } from 'framer-motion'

interface CountUpProps {
  value: number
  /** format the running value into a display string */
  format: (n: number) => string
  durationMs?: number
  className?: string
}

/** Animates a number from 0 to `value` on mount/update. Expensive-feeling, slow ease. */
export default function CountUp({ value, format, durationMs = 1100, className }: CountUpProps) {
  const [display, setDisplay] = useState(0)
  const prev = useRef(0)

  useEffect(() => {
    const controls = animate(prev.current, value, {
      duration: durationMs / 1000,
      ease: [0.16, 1, 0.3, 1], // ease-out-expo: fast then settle
      onUpdate: (v) => setDisplay(v),
    })
    prev.current = value
    return () => controls.stop()
  }, [value, durationMs])

  return <span className={className}>{format(display)}</span>
}
