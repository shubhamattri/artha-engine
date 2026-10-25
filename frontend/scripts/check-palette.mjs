#!/usr/bin/env node
/**
 * The category palette exists twice — as Tailwind tokens and as a JS array — because a colour is
 * reached both ways: `bg-cat-3` in a class list, `strapColor(2)` in a style prop. Two copies of one
 * list drift, and on 18 Aug 2026 they had: `cat-6` was plum #7A5C82, a colour the array drops
 * outright, so Plan painted Transport purple while the strap beside it could not.
 *
 * It took a browser measurement to notice. This makes it a build-time failure instead.
 *
 * The rule: `cat-N` === STRAP_PALETTE[(N-1) % length]. Plus no banned colour in either list.
 */
import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const root = join(here, '..')

// Banned outright. #B94F39/#C0533B are the negative reds — red only ever means money going the
// wrong way, and a category is not a direction. #7A5C82/#7C5CBF are purple, which is not in the
// system at all.
const BANNED = ['#B94F39', '#C0533B', '#7A5C82', '#7C5CBF']

const hexes = (s) => (s.match(/#[0-9A-Fa-f]{6}/g) ?? []).map((h) => h.toUpperCase())
const fail = []

// The array is the source. Read the literal rather than importing, so this stays a plain node
// script with no TS toolchain and no new dependency.
const strapSrc = readFileSync(join(root, 'src/lib/strap.ts'), 'utf8')
const block = strapSrc.match(/export const STRAP_PALETTE = \[([\s\S]*?)\] as const/)
if (!block) fail.push('src/lib/strap.ts: could not find the STRAP_PALETTE literal')
const palette = block ? hexes(block[1]) : []

// The Tailwind tokens.
const twSrc = readFileSync(join(root, 'tailwind.config.js'), 'utf8')
const tokens = new Map()
for (const m of twSrc.matchAll(/'cat-(\d+)':\s*'(#[0-9A-Fa-f]{6})'/g)) {
  tokens.set(Number(m[1]), m[2].toUpperCase())
}
if (!tokens.size) fail.push('tailwind.config.js: no cat-N tokens found')

for (const c of palette) if (BANNED.includes(c)) fail.push(`STRAP_PALETTE contains banned ${c}`)
for (const [n, c] of tokens) if (BANNED.includes(c)) fail.push(`cat-${n} is banned ${c}`)

for (const [n, got] of [...tokens].sort((a, b) => a[0] - b[0])) {
  const want = palette[(n - 1) % palette.length]
  if (got !== want) fail.push(`cat-${n} is ${got}, but strapColor(${n - 1}) is ${want}`)
}

// A private copy of the array is how the drift started: three components each carried their own
// CAT_COLORS. This scans rather than naming files, because the named list was itself a thing that
// could rot — it went stale the day one of those three components was deleted, and took the whole
// check down with an ENOENT instead of reporting on the files that still exist.
function walk(dir) {
  return readdirSync(join(root, dir), { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? walk(join(dir, e.name))
      : /\.tsx?$/.test(e.name) ? [join(dir, e.name)] : [])
}
for (const f of walk('src')) {
  const src = readFileSync(join(root, f), 'utf8')
  if (/const CAT_COLORS = \[/.test(src)) fail.push(`${f}: re-declares CAT_COLORS — import STRAP_PALETTE instead`)
}

if (fail.length) {
  console.error('✗ category palette is inconsistent\n' + fail.map((f) => '  ' + f).join('\n'))
  process.exit(1)
}
console.log(`✓ category palette consistent — ${palette.length} colours, ${tokens.size} tokens, none banned`)
