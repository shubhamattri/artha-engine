import { useHolders } from '../../api/meta'

/**
 * <option> list for holder/member <select>s — sourced from the household's members table,
 * NEVER hardcoded names. Drop inside any existing <select>. Falls back to "Household" before load.
 */
export default function HolderOptions() {
  const { holders } = useHolders()
  const list = holders.length ? holders : ['Household']
  return (
    <>
      {list.map((h) => (
        <option key={h} value={h}>{h}</option>
      ))}
    </>
  )
}
