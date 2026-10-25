"""One profile per person: the login and the person are the same record to the outside world.

Before this a human was two disconnected halves — a users row that could sign in and a members row
that held cards and a date of birth — and the UI had to guess which member the signed-in user was.
"""

from datetime import date

from models.account import Account
from models.tenant import Household, Member
from models.user import User


def _setup(db, name="Alpha"):
    h = Household(name="H")
    db.add(h)
    db.commit()
    m = Member(household_id=h.id, name=name, key=name, is_household=False, active=True)
    db.add(m)
    db.commit()
    u = User(household_id=h.id, email=f"{name}@x.test", username=name.lower(),
             name=name, password_hash="x", is_active=True)
    db.add(u)
    db.commit()
    return h, m, u


def test_profile_joins_the_login_to_the_person(db_session, client_for_user):
    h, m, u = _setup(db_session)
    c = client_for_user(h, u)
    p = c.get("/api/profile").json()
    assert p["username"] == "alpha"          # from the login
    assert p["member_id"] == m.id            # ...and the person, in one record
    db_session.refresh(u)
    assert u.member_id == m.id               # link persisted, not recomputed every request


def test_a_login_with_no_member_gets_one_rather_than_an_error(db_session, client_for_user):
    """Someone who signed up before members existed must not hit a broken profile screen."""
    h = Household(name="H")
    db_session.add(h)
    db_session.commit()
    u = User(household_id=h.id, email="n@x.test", username="new", name="New",
             password_hash="x", is_active=True)
    db_session.add(u)
    db_session.commit()

    p = client_for_user(h, u).get("/api/profile").json()
    assert p["member_id"] is not None
    assert p["name"] == "New"


def test_identity_is_never_returned_only_whether_it_is_set(db_session, client_for_user):
    h, m, u = _setup(db_session)
    c = client_for_user(h, u)
    c.patch("/api/profile", json={"dob": "1990-01-01", "mobile": "9876512345",
                                  "pan": "abcde1234f", "pincode": "560001"})
    blob = str(c.get("/api/profile").json())
    for secret in ("1990-01-01", "9876512345", "ABCDE1234F", "560001"):
        assert secret not in blob, f"{secret} leaked from /profile"
    ident = c.get("/api/profile").json()["identity"]
    assert ident["dob"]["set"] is True and ident["dob"]["required"] is True
    assert ident["pan"]["set"] is True and ident["pan"]["required"] is False


def test_every_field_says_what_it_unlocks(db_session, client_for_user):
    """Asking for a PAN with no stated reason reads as data harvesting."""
    h, m, u = _setup(db_session)
    ident = client_for_user(h, u).get("/api/profile").json()["identity"]
    assert all(v["unlocks"] for v in ident.values())


def test_only_dob_blocks(db_session, client_for_user):
    h, m, u = _setup(db_session)
    c = client_for_user(h, u)
    assert c.get("/api/profile").json()["missing_required"] == ["dob"]
    c.patch("/api/profile", json={"dob": "1990-01-01"})
    p = c.get("/api/profile").json()
    assert p["missing_required"] == [] and p["complete"] is True
    assert set(p["missing_optional"]) == {"mobile", "pan", "pincode"}


def test_profile_shows_what_this_person_holds(db_session, client_for_user):
    """The profile explains WHY the details are wanted by naming the cards they unlock."""
    h, m, u = _setup(db_session)
    db_session.add(Account(household_id=h.id, bank="B", kind="card", last4="1111", holder="Alpha"))
    db_session.add(Account(household_id=h.id, bank="B", kind="card", last4="2222", holder="Other"))
    db_session.commit()
    got = client_for_user(h, u).get("/api/profile").json()["accounts"]
    assert [a["last4"] for a in got] == ["1111"]


def test_household_view_lists_everyone_not_just_you(db_session, client_for_user):
    """A card in another member's name is locked with THEIR date of birth, so the screen has to show
    whose details are missing, not only the signed-in user's."""
    h, m, u = _setup(db_session)
    other = Member(household_id=h.id, name="Beta", key="Beta", is_household=False, active=True)
    db_session.add(other)
    db_session.commit()
    client_for_user(h, u).patch("/api/profile", json={"dob": "1990-01-01"})

    people = client_for_user(h, u).get("/api/profile/household").json()["people"]
    by = {p["name"]: p for p in people}
    assert by["Alpha"]["complete"] is True and by["Alpha"]["is_you"] is True
    assert by["Beta"]["complete"] is False and by["Beta"]["has_login"] is False


def test_bad_values_are_refused(db_session, client_for_user):
    h, m, u = _setup(db_session)
    c = client_for_user(h, u)
    assert c.patch("/api/profile", json={"pan": "NOPE"}).status_code == 400
    assert c.patch("/api/profile", json={"pincode": "12"}).status_code == 400
    assert c.patch("/api/profile", json={"dob": "4 March 1990"}).status_code == 400


def test_omitted_fields_are_left_alone(db_session, client_for_user):
    """Saving one field must never wipe another."""
    h, m, u = _setup(db_session)
    c = client_for_user(h, u)
    c.patch("/api/profile", json={"dob": "1990-01-01", "pan": "ABCDE1234F"})
    c.patch("/api/profile", json={"mobile": "9876512345"})
    db_session.refresh(m)
    assert m.dob == date(1990, 1, 1) and m.pan == "ABCDE1234F" and m.mobile == "9876512345"
