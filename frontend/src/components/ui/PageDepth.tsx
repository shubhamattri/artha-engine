import { createContext, useContext } from 'react'

/**
 * How many PageContainers deep the current subtree is.
 *
 * The redesign merges twelve destinations into four tabs, so a tab is now a COMPOSITION of screens
 * that were each written as a standalone page: `<PageContainer>` plus a level-1 `<SectionHeader>`.
 * Rendered inside another tab as-is, each one contributes its own page gutters and its own `<h1>`,
 * so Plan would open with three page paddings and three competing top-level headings.
 *
 * The alternative was an `embedded` prop threaded through seven page components and every early
 * return inside them. Depth in context means the primitives handle it once and no page has to know
 * whether it is being composed — which is the difference between a merge and a rewrite.
 */
export const PageDepth = createContext(0)

export function usePageDepth(): number {
  return useContext(PageDepth)
}
