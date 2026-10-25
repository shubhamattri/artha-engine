/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        // ── New "Sage" design system (v2, green/light · the product identity) ──
        // Semantic names drive every rebuilt component. Tokens are CSS vars in index.css.
        bg: 'var(--bg)',
        surface: 'var(--surface)',
        'surface-2': 'var(--surface-2)',
        line: 'var(--line)',
        'line-2': 'var(--line-2)',
        ink: 'var(--ink)',
        'ink-2': 'var(--ink-2)',
        'ink-3': 'var(--ink-3)',
        accent: 'var(--accent)',
        'accent-2': 'var(--accent-2)',
        'accent-soft': 'var(--accent-soft)',
        positive: 'var(--positive)',
        negative: 'var(--negative)',
        warn: 'var(--warn)',

        // ── Legacy token NAMES remapped to the Sage palette (role-preserving) so every
        //    un-migrated page flips to the new light/green identity with no rewrite. ──
        void: '#F6F5F1',          // was dark bg → now light canvas
        'void-2': '#FFFFFF',      // cards
        'void-3': '#FBFAF7',
        hairline: '#E8E6DD',      // light hairline
        'hairline-2': '#DAD7CC',
        brass: '#0F7A52',         // accent → green
        'brass-bright': '#0B5E3F',
        paper: '#161814',         // was light text → now dark ink
        'paper-dim': '#5E6358',
        'paper-faint': '#969A8C',
        pos: '#0F7A52',
        neg: '#C0533B',

        // ── Category / chart palette (data-driven categories rotate through these).
        //    Warm + green-forward, NO red — red is RESERVED for over-budget / negative only
        //    (use `negative`), and NO purple.
        //
        //    These mirror STRAP_PALETTE in src/lib/strap.ts, which is the source: the rule is
        //    `cat-N` === strapColor(N-1), including the wrap at cat-8. Keep it that way. A screen
        //    can reach a category colour through a class or through the function, and until
        //    18 Aug 2026 the two disagreed — `cat-6` was plum #7A5C82, which the palette drops
        //    outright, so the Plan tab painted Transport purple while the strap on the same page
        //    could not. Change one list without the other and that comes back.
        'cat-1': '#0F7A52', // green (accent)
        'cat-2': '#2E728A', // teal
        'cat-3': '#B07D1E', // gold
        'cat-4': '#A6704A', // clay / brown
        'cat-5': '#6E8B5A', // sage
        'cat-6': '#5B6472', // slate
        'cat-7': '#C9A24B', // sand
        'cat-8': '#0F7A52', // wraps to cat-1, as strapColor(7) does
      },
      // ── ONE radius scale. Every surface picks a token here; nothing is a sharp rectangle. ──
      borderRadius: {
        chip: '9999px',    // pills / badges / segmented toggles
        input: '0.75rem',  // 12px — inputs, small controls  (== rounded-xl)
        tile: '1rem',      // 16px — stat tiles, rows          (== rounded-2xl)
        card: '1rem',      // 16px — primary card corner (alias of tile)
        panel: '1.25rem',  // 20px — grouped panels
        sheet: '1.5rem',   // 24px — bottom sheets, hero blocks (== rounded-3xl)
      },
      maxWidth: {
        // The app content band — desktop fills a real width, not a stranded phone column.
        content: '1200px',
      },
      fontFamily: {
        // ONE system, three faces:
        //   body/UI  → Hanken Grotesk (humanist, warm — not Inter/Roboto)
        //   display  → Clash Display (editorial headings)
        //   mono     → JetBrains Mono (distinctive TABULAR figures — every money value)
        ui: ['"Hanken Grotesk"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        sans: ['"Hanken Grotesk"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
        display: ['"Clash Display"', '"Hanken Grotesk"', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
    },
  },
  plugins: [],
}
