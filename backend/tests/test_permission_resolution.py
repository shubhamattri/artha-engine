"""How a role and a set of grants become one answer.

Two rules, and they are the whole resolver:
  * an elevated role (admin, super_admin) is `edit` on everything except managing people
  * a member is whatever they were granted, defaulting to `none`

`none` rather than `view` is the deliberate part. A new family member sees nothing until somebody
decides otherwise, and that decision is a visible row rather than an absence.
"""

from auth.permissions import KEYS, resolve
from auth.roles import Level, Role


def test_an_admin_can_edit_everything():
    perms = resolve(Role.admin, grants={})
    assert all(perms[k] is Level.edit for k in KEYS)


def test_a_super_admin_resolves_the_same_as_an_admin():
    """The difference between them is managing PEOPLE, which is a role check rather than a
    permission key. Keeping it out of the key space stops it drifting into a grantable thing."""
    assert resolve(Role.super_admin, grants={}) == resolve(Role.admin, grants={})


def test_a_member_with_no_grants_sees_nothing():
    perms = resolve(Role.member, grants={})
    assert all(perms[k] is Level.none for k in KEYS)


def test_a_member_gets_exactly_what_was_granted():
    perms = resolve(Role.member, grants={"spending": Level.view, "budget": Level.edit})
    assert perms["spending"] is Level.view
    assert perms["budget"] is Level.edit
    assert perms["debt"] is Level.none


def test_every_key_is_present_whatever_the_role():
    """Callers never handle a missing key. A `.get(key)` returning None somewhere in the request path
    is how a permission check silently becomes a no-op."""
    for role in Role:
        perms = resolve(role, grants={})
        assert set(perms) == set(KEYS)


def test_a_grant_for_a_key_that_no_longer_exists_is_ignored_not_crashed():
    """A key removed from the catalogue leaves rows behind. Reading one must not take down every
    request that user makes."""
    perms = resolve(Role.member, grants={"a_key_that_was_deleted": Level.edit})
    assert set(perms) == set(KEYS)


def test_a_grant_never_widens_an_elevated_role_or_narrows_it():
    """Grants are a member concept. An admin row carrying a stale `none` grant must not lock them
    out of their own household."""
    perms = resolve(Role.admin, grants={"debt": Level.none, "wealth": Level.view})
    assert perms["debt"] is Level.edit
    assert perms["wealth"] is Level.edit
