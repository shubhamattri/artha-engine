"""Onboarding must collect what banks require, and must never hand it back.

Statement PDFs are locked with a password derived from the holder's name, DOB, mobile, PAN or PIN
code. The app collected none of it, so statement import silently failed to open most files. That is
an onboarding gap, not a per-card setting.
"""

from datetime import date

import pytest

from models.account import Account
from models.tenant import Household, Member


def _hh(db, name="H"):
    h = Household(name=name)
    db.add(h)
    db.commit()
    return h


def _member(db, h, name, **kw):
    m = Member(household_id=h.id, name=name, key=name, **kw)
    db.add(m)
    db.commit()
    return m


def test_identity_is_write_only(db_session, client_for):
    """The UI must learn WHETHER a value is set, never what it is. A PAN that leaves the server ends
    up in a browser cache, a screenshot, or a support transcript."""
    h = _hh(db_session)
    m = _member(db_session, h, "A")
    c = client_for(h)
    r = c.patch(f"/api/members/{m.id}/identity",
                json={"dob": "1990-01-01", "mobile": "9876512345",
                      "pan": "abcde1234f", "pincode": "560001"})
    assert r.status_code == 200, r.text
    assert r.json()["identity_complete"] is True

    body = c.get("/api/members").json()
    blob = str(body)
    for secret in ("9876512345", "ABCDE1234F", "abcde1234f", "1990-01-01", "560001"):
        assert secret not in blob, f"{secret} leaked in GET /members"
    me = next(x for x in body["members"] if x["id"] == m.id)
    assert me == {**me, "has_dob": True, "has_mobile": True, "has_pan": True, "has_pincode": True}


def test_values_are_normalised_on_the_way_in(db_session, client_for):
    h = _hh(db_session)
    m = _member(db_session, h, "A")
    c = client_for(h)
    c.patch(f"/api/members/{m.id}/identity",
            json={"pan": " abcde1234f ", "mobile": "+91 98765 12345", "pincode": "560 001"})
    db_session.refresh(m)
    assert m.pan == "ABCDE1234F"       # banks want it capitalised
    assert m.mobile == "9876512345"    # country code and spaces stripped
    assert m.pincode == "560001"


def test_bad_values_are_refused_with_a_usable_message(db_session, client_for):
    h = _hh(db_session)
    m = _member(db_session, h, "A")
    c = client_for(h)
    assert c.patch(f"/api/members/{m.id}/identity", json={"pan": "NOTAPAN"}).status_code == 400
    assert c.patch(f"/api/members/{m.id}/identity", json={"pincode": "123"}).status_code == 400
    assert c.patch(f"/api/members/{m.id}/identity", json={"mobile": "123"}).status_code == 400
    assert c.patch(f"/api/members/{m.id}/identity", json={"dob": "25 Jan 1998"}).status_code == 400


@pytest.mark.parametrize("fmt", ["1990-01-01", "01-01-1990", "01/01/1990"])
def test_dob_accepts_the_formats_people_type(db_session, client_for, fmt):
    h = _hh(db_session)
    m = _member(db_session, h, "A")
    client_for(h).patch(f"/api/members/{m.id}/identity", json={"dob": fmt})
    db_session.refresh(m)
    assert m.dob == date(1990, 1, 1)


def test_a_card_uses_its_own_holders_details(db_session):
    """A household is not one person: a card in one spouse's name is locked with THEIR date of birth,
    so a single household-wide profile would fail for every card the other person holds."""
    from parsers.pdf_password_rules import candidates_for_card

    h = _hh(db_session)
    _member(db_session, h, "Alpha", dob=date(1988, 2, 2))
    _member(db_session, h, "Beta", dob=date(1985, 11, 12))
    db_session.add(Account(household_id=h.id, bank="X", kind="card", last4="1111", holder="Beta"))
    db_session.commit()

    got = candidates_for_card(db_session, "1111", household_id=h.id)
    assert any("1211" in c for c in got), "must use Beta's DOB (12 Nov)"
    assert not any("0202" in c for c in got), "must not use Alpha's DOB"


def test_unknown_holder_falls_back_to_everyone(db_session):
    """Better to try a few extra candidates than fail to open a statement we could have opened."""
    from parsers.pdf_password_rules import candidates_for_card

    h = _hh(db_session)
    _member(db_session, h, "Alpha", dob=date(1988, 2, 2))
    _member(db_session, h, "Beta", dob=date(1985, 11, 12))
    db_session.commit()
    got = candidates_for_card(db_session, "9999", household_id=h.id)   # no such account
    assert any("0202" in c for c in got) and any("1211" in c for c in got)


def test_one_household_never_derives_from_anothers_members(db_session):
    from parsers.pdf_password_rules import candidates_for_card

    a, b = _hh(db_session, "A"), _hh(db_session, "B")
    _member(db_session, a, "Alpha", dob=date(1988, 2, 2))
    db_session.add(Account(household_id=a.id, bank="X", kind="card", last4="1111", holder="Alpha"))
    db_session.commit()
    assert candidates_for_card(db_session, "1111", household_id=b.id) == []
