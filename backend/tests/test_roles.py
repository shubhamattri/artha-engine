"""One source for the role vocabulary.

Role enums copied across packages drift apart, and the practical consequence is that a role which
exists in the backend cannot be assigned from the admin UI and is locked out of the routes it was
created for. One file here, and the
frontend receives the list from the API rather than repeating it.
"""

from auth.roles import Level, Role


def test_there_are_exactly_three_roles():
    assert [r.value for r in Role] == ["member", "admin", "super_admin"]


def test_levels_are_ordered_and_edit_implies_view():
    assert Level.none < Level.view < Level.edit
    assert Level.edit >= Level.view


def test_admin_and_super_admin_are_both_elevated_but_are_not_the_same():
    assert Role.super_admin.is_elevated and Role.admin.is_elevated
    assert not Role.member.is_elevated
    # Only super_admin manages people. There is deliberately NO `is_admin` boolean that ORs a growing
    # set of roles: such a flag starts as `admin`, accretes more roles, and ends up silently granting
    # all of them at every guard site.
    assert Role.super_admin.manages_users
    assert not Role.admin.manages_users
    assert not Role.member.manages_users


def test_an_unknown_role_string_does_not_silently_become_a_member():
    """A row holding a role this code does not know is a bug, and it must read as one. Coercing it to
    `member` would look like a safe default while hiding that the database and the code disagree."""
    import pytest

    with pytest.raises(ValueError):
        Role("owner")
