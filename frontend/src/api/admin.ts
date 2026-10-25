import { api } from './client'

/**
 * Managing people: who can sign in, and what each of them may change.
 *
 * Every route here is `require_super_admin` on the server. Nothing in this file keeps anyone out —
 * it is refused before the endpoint body runs whatever the UI does.
 */
export interface AdminUser {
  id: number
  username: string
  email: string
  name: string
  role: string
  is_active: boolean
  member_id: number | null
  /** The RESOLVED map, not the stored grant rows: an admin holds every key and no rows. */
  permissions: Record<string, number>
}

export interface AdminUsers {
  users: AdminUser[]
  /** The catalogue, sent by the server so the screen never hardcodes a key list that can drift. */
  keys: string[]
}

export async function fetchAdminUsers(): Promise<AdminUsers> {
  const { data } = await api.get<AdminUsers>('/admin/users')
  return data
}

export async function setUserRole(id: number, role: string): Promise<AdminUser> {
  const { data } = await api.patch<AdminUser>(`/admin/users/${id}/role`, { role })
  return data
}

/** Replaces the whole set. Sending a partial list revokes everything absent from it. */
export async function setUserPermissions(
  id: number,
  grants: { key: string; level: number }[],
): Promise<AdminUser> {
  const { data } = await api.patch<AdminUser>(`/admin/users/${id}/permissions`, { grants })
  return data
}

/** Removes the login, not the person: their transactions are household history and their member
 *  row is what unlocks statement PDFs. */
export async function deactivateUser(id: number): Promise<AdminUser> {
  const { data } = await api.post<AdminUser>(`/admin/users/${id}/deactivate`, {})
  return data
}
