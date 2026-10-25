import { api } from './client'

export interface AuthUser {
  id: number
  email: string
  username: string
  name: string
  /** member | admin | super_admin. For LABELLING a person, never for deciding what to render —
   *  read `permissions` for that, so the UI and the server cannot disagree about what a role
   *  implies. */
  role: string
  /** The member row this login belongs to. Published so the profile screen stops matching a user to
   *  a member by comparing names. */
  member_id: number | null
  household_id: number
  /** The RESOLVED map: every permission key, 0 none / 1 view / 2 edit. Resolved server-side from
   *  role plus grants, so the frontend never re-implements that rule. */
  permissions: Record<string, number>
}

export async function login(identifier: string, password: string): Promise<AuthUser> {
  const { data } = await api.post<AuthUser>('/auth/login', { identifier, password })
  return data
}

export async function logout(): Promise<void> {
  await api.post('/auth/logout')
}

export async function fetchMe(): Promise<AuthUser> {
  const { data } = await api.get<AuthUser>('/auth/me')
  return data
}

export async function forgotPassword(email: string): Promise<string> {
  const { data } = await api.post<{ message: string }>('/auth/forgot-password', { email })
  return data.message
}

export async function resetPassword(token: string, newPassword: string): Promise<string> {
  const { data } = await api.post<{ message: string }>('/auth/reset-password', {
    token,
    new_password: newPassword,
  })
  return data.message
}

/** Set a new password from inside the app. No email, no token — the current password is the proof. */
export async function changePassword(currentPassword: string, newPassword: string): Promise<string> {
  const { data } = await api.post('/auth/change-password', {
    current_password: currentPassword,
    new_password: newPassword,
  })
  return data.message as string
}
