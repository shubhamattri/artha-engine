"""`purpose` must be settable through the API, not by a script against the database.

The donation RD was flagged `purpose='donation'` by an ad-hoc script, so the product could compute a
wealth/donation split that no user could ever produce. These tests pin the round trip: POST/PUT carry
purpose, a bad value is rejected at the edge, and the totals separate committed giving from wealth.
"""

from datetime import date

from models.tenant import Household


def _household(db_session) -> Household:
    h = Household(name="H")
    db_session.add(h)
    db_session.commit()
    return h


def _body(**over) -> dict:
    b = {
        "name": "Giving RD", "kind": "rd", "as_of_date": date(2026, 1, 1).isoformat(),
        "opening_balance": 0.0, "monthly_contribution": 5000.0, "annual_rate_pct": 7.0,
    }
    b.update(over)
    return b


def test_create_persists_purpose(db_session, client_for):
    c = client_for(_household(db_session))
    row = c.post("/api/recurring", json=_body(purpose="donation")).json()
    assert row["purpose"] == "donation"
    listed = c.get("/api/recurring").json()["investments"]
    assert [i["purpose"] for i in listed] == ["donation"]


def test_purpose_defaults_to_wealth(db_session, client_for):
    c = client_for(_household(db_session))
    assert c.post("/api/recurring", json=_body()).json()["purpose"] == "wealth"


def test_update_can_flip_purpose_both_ways(db_session, client_for):
    c = client_for(_household(db_session))
    inv_id = c.post("/api/recurring", json=_body()).json()["id"]
    assert c.put(f"/api/recurring/{inv_id}", json=_body(purpose="donation")).json()["purpose"] == "donation"
    assert c.put(f"/api/recurring/{inv_id}", json=_body(purpose="wealth")).json()["purpose"] == "wealth"


def test_unknown_purpose_is_rejected(db_session, client_for):
    c = client_for(_household(db_session))
    assert c.post("/api/recurring", json=_body(purpose="charity")).status_code == 422
    inv_id = c.post("/api/recurring", json=_body()).json()["id"]
    assert c.put(f"/api/recurring/{inv_id}", json=_body(purpose="")).status_code == 422


def test_totals_keep_giving_out_of_wealth(db_session, client_for):
    """A donation instrument is a real bank outflow but never part of the corpus."""
    c = client_for(_household(db_session))
    c.post("/api/recurring", json=_body(name="SIP", kind="sip", monthly_contribution=10000.0))
    c.post("/api/recurring", json=_body(purpose="donation", monthly_contribution=5000.0))
    t = c.get("/api/recurring").json()

    assert t["donation_outflow"] == 5000.0
    assert t["takehome_outflow"] == 15000.0          # both still debit the bank
    assert t["wealth_outflow"] == 10000.0            # only the SIP builds wealth
    assert t["total_wealth_value"] < t["total_value"]
    assert t["total_value"] - t["total_wealth_value"] > 0


def test_donation_is_selectable_in_the_category_picker(db_session, client_for):
    """Donation must be pickable like the other committed categories — and stay out of the
    variable Plan, exactly as Rent and Insurance are."""
    c = client_for(_household(db_session))
    cats = {x["value"]: x for x in c.get("/api/transactions/categories").json()["categories"]}
    assert "Donation" in cats
    assert cats["Donation"]["is_spend"] == cats["Rent / Housing"]["is_spend"] is False
    assert cats["Donation"]["is_spend"] == cats["Insurance"]["is_spend"]
