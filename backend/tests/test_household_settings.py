"""Household settings must be settable from the app, not by a script run against the database.

Tax regime, the tracking-start date and currency were each set once by an ad-hoc script. That works
for the household whose laptop ran the script and for nobody else, so these three are now ordinary
endpoints with ordinary validation — and these tests pin the rules that keep the values honest.
"""

from datetime import date, timedelta

from models.tenant import Household


def _household(db):
    h = Household(name="H")
    db.add(h)
    db.commit()
    return h


def test_defaults_are_reported_as_defaults_not_as_choices(db_session, client_for):
    h = _household(db_session)
    got = client_for(h).get("/api/household/settings").json()
    assert got["tax_regime"] is None                 # nothing chosen...
    assert got["tax_regime_effective"] == "new"      # ...but the engines still use something
    assert got["budget_baseline_from"] is None
    assert got["currency"] is None and got["currency_effective"]


def test_the_three_settings_round_trip_through_the_api(db_session, client_for):
    h = _household(db_session)
    c = client_for(h)
    r = c.put("/api/household/settings",
              json={"tax_regime": "old", "budget_baseline_from": "2026-01-01", "currency": "usd"})
    assert r.status_code == 200
    db_session.refresh(h)
    assert h.tax_regime == "old"
    assert h.budget_baseline_from == date(2026, 1, 1)
    assert h.currency == "USD"                       # normalised, so "usd" and "USD" are one value
    assert c.get("/api/household/settings").json()["tax_regime_effective"] == "old"


def test_only_old_or_new_are_regimes(db_session, client_for):
    c = client_for(_household(db_session))
    assert c.put("/api/household/settings", json={"tax_regime": "OLD"}).status_code == 200
    assert c.put("/api/household/settings", json={"tax_regime": "hybrid"}).status_code == 400
    assert c.put("/api/household/settings", json={"tax_regime": "2026"}).status_code == 400


def test_a_future_tracking_start_is_refused(db_session, client_for):
    """A start date after today excludes every period there is, blanking targets with no explanation."""
    c = client_for(_household(db_session))
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert c.put("/api/household/settings", json={"budget_baseline_from": tomorrow}).status_code == 400
    assert c.put("/api/household/settings",
                 json={"budget_baseline_from": date.today().isoformat()}).status_code == 200


def test_an_unparseable_tracking_start_is_refused(db_session, client_for):
    c = client_for(_household(db_session))
    assert c.put("/api/household/settings",
                 json={"budget_baseline_from": "January 2026"}).status_code == 400


def test_currency_must_be_a_three_letter_code(db_session, client_for):
    c = client_for(_household(db_session))
    assert c.put("/api/household/settings", json={"currency": "Rupees"}).status_code == 400
    assert c.put("/api/household/settings", json={"currency": "IN"}).status_code == 400


def test_saving_one_setting_leaves_the_others_alone(db_session, client_for):
    """The whole point of a settings screen is that changing one thing changes one thing."""
    h = _household(db_session)
    c = client_for(h)
    c.put("/api/household/settings",
          json={"tax_regime": "old", "budget_baseline_from": "2026-02-01", "currency": "INR"})
    c.put("/api/household/settings", json={"currency": "USD"})
    db_session.refresh(h)
    assert h.tax_regime == "old" and h.budget_baseline_from == date(2026, 2, 1)


def test_null_clears_a_setting_back_to_the_app_default(db_session, client_for):
    """Explicit null is a decision to stop deciding — distinct from not sending the field at all."""
    h = _household(db_session)
    c = client_for(h)
    c.put("/api/household/settings", json={"tax_regime": "old", "budget_baseline_from": "2026-02-01"})
    c.put("/api/household/settings", json={"tax_regime": None, "budget_baseline_from": None})
    db_session.refresh(h)
    assert h.tax_regime is None and h.budget_baseline_from is None
