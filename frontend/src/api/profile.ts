import { useQuery } from '@tanstack/react-query'

import { api } from './client'

/**
 * The signed-in person: their login, their identity, and what they hold.
 *
 * `/members` cannot answer "which of these is me" — it returns a household and leaves the screen to
 * guess. These routes resolve the person from the session and self-heal the link if it was never
 * made, which is the whole reason `backend/api/profile.py` exists.
 */
export type IdentityKey = 'dob' | 'mobile' | 'pan' | 'pincode'

/** Whether a value is stored, never the value: identity is write-only across this boundary. */
export interface IdentityField {
  set: boolean
  required: boolean
  /** What filling this in actually buys. SERVER-OWNED copy — render it, never restate it. */
  unlocks: string
}

export interface ProfileAccount {
  id: number
  bank: string
  kind: 'card' | 'account'
  last4: string
  label: string | null
}

export interface Profile {
  /** GET /profile only. PATCH returns the same body without it. */
  household?: { id: number; name: string }
  user_id: number
  username: string
  email: string
  member_id: number
  name: string
  /** The name accounts are filed under, which is not always the display name. */
  holder_key: string
  identity: Record<IdentityKey, IdentityField>
  missing_required: IdentityKey[]
  missing_optional: IdentityKey[]
  /** REQUIRED fields only — dob alone. Not `/members`' `identity_complete`, which wants all four. */
  complete: boolean
  accounts: ProfileAccount[]
}

/** Omit a key to leave it untouched. Send "" to clear it. `null` reads as omitted, not as clear. */
export interface ProfilePatch {
  name?: string
  dob?: string
  mobile?: string
  pan?: string
  pincode?: string
}

export interface HouseholdPerson {
  member_id: number
  name: string
  has_login: boolean
  is_you: boolean
  missing_required: IdentityKey[]
  missing_optional: IdentityKey[]
  complete: boolean
}

export interface HouseholdRoster {
  household: { id: number; name: string }
  people: HouseholdPerson[]
}

export async function fetchProfile(): Promise<Profile> {
  const { data } = await api.get<Profile>('/profile')
  return data
}

export async function updateProfile(body: ProfilePatch): Promise<Profile> {
  const { data } = await api.patch<Profile>('/profile', body)
  return data
}

export async function fetchHouseholdRoster(): Promise<HouseholdRoster> {
  const { data } = await api.get<HouseholdRoster>('/profile/household')
  return data
}

export function useProfile() {
  return useQuery({ queryKey: ['profile'], queryFn: fetchProfile })
}

export function useHouseholdRoster() {
  return useQuery({ queryKey: ['profile-household'], queryFn: fetchHouseholdRoster })
}
