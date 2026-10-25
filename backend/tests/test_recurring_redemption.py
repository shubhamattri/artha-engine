"""Recording reality on a mutual fund: redemptions, unit-denominated entries, and undo.

The ledger was write-only and rupee-only. A redemption could only be recorded by typing a negative
number into a field labelled "Log a top-up", broker statements that report in UNITS could not be
entered at all, and a mistyped entry was unfixable. These tests pin the three things that close
that gap: scheme lookup, a units contribution priced at the NAV of ITS OWN date, and delete-by-index.
"""

from datetime import date

import pytest
from models.tenant import Household
from services import mf_nav


def _household(db_session, name="H") -> Household:
    h = Household(name=name)
    db_session.add(h)
    db_session.commit()
    return h


def _body(**over) -> dict:
    b = {
        "name": "Parag Parikh Flexi Cap", "kind": "sip",
        "as_of_date": date(2026, 1, 1).isoformat(),
        "opening_balance": 500000.0, "monthly_contribution": 0.0, "annual_rate_pct": 12.0,
    }
    b.update(over)
    return b


@pytest.fixture()
def no_network(monkeypatch):
    """Any accidental call to the live feed fails loudly instead of reaching the internet."""
    def _boom(*a, **k):
        raise AssertionError("test hit the network")
    monkeypatch.setattr(mf_nav.urllib.request, "urlopen", _boom)
    monkeypatch.setattr(mf_nav, "fetch_nav", lambda code: None)


# ---------------------------------------------------------------- scheme search

def test_scheme_search_returns_code_and_name(db_session, client_for, monkeypatch, no_network):
    monkeypatch.setattr(mf_nav, "search_schemes",
                        lambda q, direct_growth_only=True: [
                            {"scheme_code": 122639, "scheme_name": "Parag Parikh Flexi Cap Direct Growth"}])
    r = client_for(_household(db_session)).get("/api/recurring/schemes", params={"q": "parag"})
    assert r.status_code == 200
    assert r.json() == {"schemes": [{"scheme_code": 122639,
                                     "scheme_name": "Parag Parikh Flexi Cap Direct Growth"}]}


def test_scheme_search_asks_for_direct_growth_only(db_session, client_for, monkeypatch, no_network):
    """Regular/dividend plans are a different scheme code with a different NAV — never offer them."""
    seen = {}

    def _search(q, direct_growth_only=True):
        seen["q"], seen["direct"] = q, direct_growth_only
        return []
    monkeypatch.setattr(mf_nav, "search_schemes", _search)
    client_for(_household(db_session)).get("/api/recurring/schemes", params={"q": "hdfc"})
    assert seen == {"q": "hdfc", "direct": True}


def test_scheme_search_short_query_is_empty_and_never_calls_the_feed(db_session, client_for,
                                                                    monkeypatch, no_network):
    def _boom(*a, **k):
        raise AssertionError("searched on a query too short to mean anything")
    monkeypatch.setattr(mf_nav, "search_schemes", _boom)
    c = client_for(_household(db_session))
    for q in ("", " ", "a"):
        r = c.get("/api/recurring/schemes", params={"q": q})
        assert r.status_code == 200
        assert r.json() == {"schemes": []}


def test_scheme_search_missing_query_is_empty_not_an_error(db_session, client_for, no_network):
    r = client_for(_household(db_session)).get("/api/recurring/schemes")
    assert r.status_code == 200
    assert r.json() == {"schemes": []}


def test_scheme_search_survives_a_dead_feed(db_session, client_for, monkeypatch):
    """A search box must not 500 because AMFI's mirror is down."""
    def _dead(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(mf_nav.urllib.request, "urlopen", _dead)
    r = client_for(_household(db_session)).get("/api/recurring/schemes", params={"q": "parag"})
    assert r.status_code == 200
    assert r.json() == {"schemes": []}


# ---------------------------------------------------------------- units contribution

def _fund(c, **over):
    """A row with a scheme code (so units are resolvable) but no anchored units."""
    return c.post("/api/recurring", json=_body(**over)).json()["id"]


@pytest.fixture()
def nav_stub(monkeypatch):
    """Historical NAV keyed by date. Records every lookup so tests can pin WHICH date was priced."""
    calls = []
    table = {date(2026, 4, 15): 82.5, date(2026, 7, 10): 90.0}

    def _on(code, on):
        calls.append((str(code), on))
        nav = table.get(on)
        return (nav, on.isoformat()) if nav else None
    monkeypatch.setattr(mf_nav, "fetch_nav_on", _on)
    return calls


def test_units_redemption_stores_rupees_units_and_the_nav_used(db_session, client_for,
                                                               nav_stub, no_network):
    c = client_for(_household(db_session))
    inv = _fund(c, scheme_code="122639", units=1000.0)
    r = c.post(f"/api/recurring/{inv}/contribute",
               json={"units": -500.0, "date": "2026-04-15", "note": "partial redemption"})
    assert r.status_code == 200, r.text
    entry = r.json()["contributions"][-1]
    assert entry["amount"] == -41250.0            # -500 units x 82.50
    assert entry["units"] == -500.0
    assert entry["nav"] == 82.5
    assert entry["nav_date"] == "2026-04-15"
    assert entry["direction"] == "redeem"


def test_units_are_priced_at_the_nav_of_their_own_date_not_today(db_session, client_for,
                                                                 nav_stub, no_network):
    """A redemption entered in August must use April's NAV, or the rupee figure is fiction."""
    c = client_for(_household(db_session))
    inv = _fund(c, scheme_code="122639", units=1000.0)
    apr = c.post(f"/api/recurring/{inv}/contribute",
                 json={"units": -100.0, "date": "2026-04-15"}).json()["contributions"][-1]
    jul = c.post(f"/api/recurring/{inv}/contribute",
                 json={"units": -100.0, "date": "2026-07-10"}).json()["contributions"][-1]
    assert apr["amount"] == -8250.0
    assert jul["amount"] == -9000.0
    assert [d for _code, d in nav_stub] == [date(2026, 4, 15), date(2026, 7, 10)]


def test_amount_and_units_together_is_rejected(db_session, client_for, nav_stub, no_network):
    c = client_for(_household(db_session))
    inv = _fund(c, scheme_code="122639", units=1000.0)
    r = c.post(f"/api/recurring/{inv}/contribute",
               json={"amount": -1000.0, "units": -10.0, "date": "2026-04-15"})
    assert r.status_code == 400
    assert "exactly one" in r.json()["detail"].lower()


def test_neither_amount_nor_units_is_rejected(db_session, client_for, nav_stub, no_network):
    c = client_for(_household(db_session))
    inv = _fund(c, scheme_code="122639", units=1000.0)
    r = c.post(f"/api/recurring/{inv}/contribute", json={"date": "2026-04-15"})
    assert r.status_code == 400
    assert "exactly one" in r.json()["detail"].lower()


def test_zero_is_rejected_whichever_field_carries_it(db_session, client_for, nav_stub, no_network):
    c = client_for(_household(db_session))
    inv = _fund(c, scheme_code="122639", units=1000.0)
    assert c.post(f"/api/recurring/{inv}/contribute",
                  json={"units": 0.0, "date": "2026-04-15"}).status_code == 400
    assert c.post(f"/api/recurring/{inv}/contribute",
                  json={"amount": 0.0, "date": "2026-04-15"}).status_code == 400


def test_units_without_a_scheme_code_is_rejected(db_session, client_for, nav_stub, no_network):
    """An RD has no NAV. Units there would price at nothing and silently store zero rupees."""
    c = client_for(_household(db_session))
    inv = c.post("/api/recurring", json=_body(kind="rd", name="RD")).json()["id"]
    r = c.post(f"/api/recurring/{inv}/contribute", json={"units": -10.0, "date": "2026-04-15"})
    assert r.status_code == 400
    assert "scheme" in r.json()["detail"].lower()


def test_units_are_rejected_when_the_nav_cannot_be_resolved(db_session, client_for,
                                                            nav_stub, no_network):
    """Better a visible failure than a ledger entry worth an invented number of rupees."""
    c = client_for(_household(db_session))
    inv = _fund(c, scheme_code="122639", units=1000.0)
    r = c.post(f"/api/recurring/{inv}/contribute", json={"units": -10.0, "date": "2026-05-05"})
    assert r.status_code == 400
    assert "nav" in r.json()["detail"].lower()
    assert c.get("/api/recurring").json()["investments"][0]["contributions"] == []


def test_positive_units_are_not_silently_flipped(db_session, client_for, nav_stub, no_network):
    """Sign is the caller's choice. Positive units buy; we record direction, we do not correct it."""
    c = client_for(_household(db_session))
    inv = _fund(c, scheme_code="122639", units=1000.0)
    entry = c.post(f"/api/recurring/{inv}/contribute",
                   json={"units": 200.0, "date": "2026-04-15"}).json()["contributions"][-1]
    assert entry["amount"] == 16500.0
    assert entry["units"] == 200.0
    assert entry["direction"] == "buy"


def test_a_units_withdrawal_cannot_be_funded_from_the_wallet(db_session, client_for,
                                                             nav_stub, no_network):
    """The rupee rule must survive the units path — money coming BACK never debits the wallet."""
    c = client_for(_household(db_session))
    inv = _fund(c, scheme_code="122639", units=1000.0)
    r = c.post(f"/api/recurring/{inv}/contribute",
               json={"units": -100.0, "date": "2026-04-15", "funded_from": "wallet"})
    assert r.status_code == 400
    assert "wallet" in r.json()["detail"].lower()


def test_redeemed_units_stop_being_counted_as_held(db_session, client_for, nav_stub, monkeypatch):
    """The whole point. Units sold are gone: the holding must shrink, not just the cost basis.
    Leaving `units` at the anchor would keep valuing sold units AND book the proceeds as gain."""
    monkeypatch.setattr(mf_nav, "fetch_nav", lambda code: (100.0, "2026-08-18"))
    c = client_for(_household(db_session))
    inv = _fund(c, scheme_code="122639", units=1000.0)
    before = c.get("/api/recurring").json()["investments"][0]
    assert before["units"] == 1000.0 and before["current_value"] == 100000.0

    c.post(f"/api/recurring/{inv}/contribute", json={"units": -400.0, "date": "2026-04-15"})
    after = c.get("/api/recurring").json()["investments"][0]
    assert after["units"] == 600.0
    assert after["current_value"] == 60000.0
    assert after["total_contributed"] == 500000.0 - 33000.0     # 400 units x 82.50 came back


def test_deleting_a_units_entry_puts_the_units_back(db_session, client_for, nav_stub, monkeypatch):
    """`market.units` stays the anchor and the ledger carries the movements, so undo is exact."""
    monkeypatch.setattr(mf_nav, "fetch_nav", lambda code: (100.0, "2026-08-18"))
    c = client_for(_household(db_session))
    inv = _fund(c, scheme_code="122639", units=1000.0)
    c.post(f"/api/recurring/{inv}/contribute", json={"units": -400.0, "date": "2026-04-15"})
    assert c.delete(f"/api/recurring/{inv}/contribute/0").json()["units"] == 1000.0
    assert c.get("/api/recurring").json()["investments"][0]["current_value"] == 100000.0


def test_redeeming_more_units_than_held_never_goes_negative(db_session, client_for,
                                                            nav_stub, monkeypatch):
    monkeypatch.setattr(mf_nav, "fetch_nav", lambda code: (100.0, "2026-08-18"))
    c = client_for(_household(db_session))
    inv = _fund(c, scheme_code="122639", units=100.0)
    c.post(f"/api/recurring/{inv}/contribute", json={"units": -900.0, "date": "2026-04-15"})
    row = c.get("/api/recurring").json()["investments"][0]
    assert row["units"] == 0.0
    assert row["current_value"] == 0.0


def test_a_rupee_only_ledger_leaves_the_units_untouched(db_session, client_for, monkeypatch):
    """Every entry logged before units existed has no `units` key — it must not move the holding."""
    monkeypatch.setattr(mf_nav, "fetch_nav", lambda code: (100.0, "2026-08-18"))
    c = client_for(_household(db_session))
    inv = _fund(c, scheme_code="122639", units=1000.0)
    c.post(f"/api/recurring/{inv}/contribute", json={"amount": -50000.0, "date": "2026-04-15"})
    row = c.get("/api/recurring").json()["investments"][0]
    assert row["units"] == 1000.0
    assert row["current_value"] == 100000.0


# ---------------------------------------------------------------- delete a ledger entry

def test_delete_removes_the_entry_at_that_index(db_session, client_for, nav_stub, no_network):
    c = client_for(_household(db_session))
    inv = _fund(c, scheme_code="122639", units=1000.0)
    for d, u in (("2026-04-15", -100.0), ("2026-07-10", -200.0)):
        c.post(f"/api/recurring/{inv}/contribute", json={"units": u, "date": d})
    r = c.delete(f"/api/recurring/{inv}/contribute/0")
    assert r.status_code == 200, r.text
    left = r.json()["contributions"]
    assert len(left) == 1
    assert left[0]["date"] == "2026-07-10"
    assert c.get("/api/recurring").json()["investments"][0]["contributions"] == left


def test_delete_undoes_a_mistyped_entry_exactly(db_session, client_for, nav_stub, no_network):
    c = client_for(_household(db_session))
    inv = _fund(c, scheme_code="122639")
    before = c.get("/api/recurring").json()["investments"][0]["total_contributed"]
    c.post(f"/api/recurring/{inv}/contribute", json={"units": -900.0, "date": "2026-04-15"})
    c.delete(f"/api/recurring/{inv}/contribute/0")
    assert c.get("/api/recurring").json()["investments"][0]["total_contributed"] == before


def test_delete_out_of_range_index_is_404(db_session, client_for, nav_stub, no_network):
    c = client_for(_household(db_session))
    inv = _fund(c, scheme_code="122639", units=1000.0)
    assert c.delete(f"/api/recurring/{inv}/contribute/0").status_code == 404
    c.post(f"/api/recurring/{inv}/contribute", json={"units": -1.0, "date": "2026-04-15"})
    assert c.delete(f"/api/recurring/{inv}/contribute/1").status_code == 404
    assert c.delete(f"/api/recurring/{inv}/contribute/-1").status_code == 404
    assert len(c.get("/api/recurring").json()["investments"][0]["contributions"]) == 1


def test_delete_is_scoped_to_the_owning_household(db_session, client_for, nav_stub, no_network):
    """Another household's ledger is not reachable by guessing an id."""
    mine, theirs = _household(db_session, "mine"), _household(db_session, "theirs")
    c_mine, c_theirs = client_for(mine), client_for(theirs)
    inv = _fund(c_mine, scheme_code="122639", units=1000.0)
    c_mine.post(f"/api/recurring/{inv}/contribute", json={"units": -100.0, "date": "2026-04-15"})
    assert c_theirs.delete(f"/api/recurring/{inv}/contribute/0").status_code == 404
    assert len(c_mine.get("/api/recurring").json()["investments"][0]["contributions"]) == 1


def test_contribute_is_scoped_to_the_owning_household(db_session, client_for, nav_stub, no_network):
    mine, theirs = _household(db_session, "mine"), _household(db_session, "theirs")
    inv = _fund(client_for(mine), scheme_code="122639", units=1000.0)
    r = client_for(theirs).post(f"/api/recurring/{inv}/contribute",
                                json={"units": -100.0, "date": "2026-04-15"})
    assert r.status_code == 404


# ---------------------------------------------------------------- historical NAV service

_HISTORY = {"status": "SUCCESS", "data": [
    {"date": "10-07-2026", "nav": "90.0000"},
    {"date": "09-07-2026", "nav": "89.5000"},
    {"date": "03-07-2026", "nav": "88.0000"},   # Friday; 4th & 5th are the weekend
    {"date": "15-04-2026", "nav": "82.5000"},
]}


@pytest.fixture()
def feed(monkeypatch):
    """Stubs the raw HTTP layer and counts fetches, so caching is observable."""
    import io
    import json as _json
    calls = []

    class _Resp(io.StringIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _urlopen(url, timeout=None):
        calls.append(url)
        return _Resp(_json.dumps(_HISTORY))
    monkeypatch.setattr(mf_nav.urllib.request, "urlopen", _urlopen)
    mf_nav.clear_history_cache()
    return calls


def test_nav_on_an_exact_trading_day(feed):
    assert mf_nav.fetch_nav_on("122639", date(2026, 4, 15)) == (82.5, "2026-04-15")


def test_nav_on_a_weekend_falls_back_to_the_last_trading_day(feed):
    """Markets close Sat/Sun. Exact-match-only would reject a perfectly valid redemption date."""
    assert mf_nav.fetch_nav_on("122639", date(2026, 7, 5)) == (88.0, "2026-07-03")


def test_nav_after_the_last_record_uses_the_most_recent_one(feed):
    assert mf_nav.fetch_nav_on("122639", date(2026, 8, 18)) == (90.0, "2026-07-10")


def test_nav_before_the_first_record_is_none(feed):
    """No NAV existed yet — say so rather than inventing the oldest one."""
    assert mf_nav.fetch_nav_on("122639", date(2020, 1, 1)) is None


def test_nav_history_is_fetched_once_per_scheme(feed):
    for d in (date(2026, 4, 15), date(2026, 7, 10), date(2026, 7, 5)):
        assert mf_nav.fetch_nav_on("122639", d) is not None
    assert len(feed) == 1


def test_nav_on_a_dead_feed_is_none_not_an_exception(monkeypatch):
    def _dead(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(mf_nav.urllib.request, "urlopen", _dead)
    mf_nav.clear_history_cache()
    assert mf_nav.fetch_nav_on("122639", date(2026, 4, 15)) is None


def test_nav_on_a_missing_scheme_code_is_none(feed):
    assert mf_nav.fetch_nav_on("", date(2026, 4, 15)) is None
    assert mf_nav.fetch_nav_on(None, date(2026, 4, 15)) is None
    assert feed == []
