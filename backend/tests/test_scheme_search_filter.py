"""The direct-plan filter hid a fund the household actually owns.

`search_schemes` required BOTH "Direct" and "Growth" in the AMFI name. AMFI does not name schemes
consistently: "Motilal Oswal Nifty Midcap 150 Index Fund - Direct Plan" is a direct growth plan and
says nothing about growth, because that AMC only publishes an IDCW variant under a separate name.

The effect reached the screen. Searching for that fund in the add-fund flow returned nothing, so the
one path to link a fund to live NAV dead-ended, and the fund stayed on the assumed 12% rate — which
is the exact failure this whole feature exists to end.

What the filter should mean is "the direct plan, not the dividend option", so it excludes the payout
variants rather than demanding a word AMFI does not always print.
"""

import json
from io import BytesIO

import pytest
from services import mf_nav


def _feed(monkeypatch, names: list[str]):
    rows = [{"schemeCode": 100000 + i, "schemeName": n} for i, n in enumerate(names)]
    payload = BytesIO(json.dumps(rows).encode())

    class _Resp:
        def __enter__(self): return payload
        def __exit__(self, *a): return False

    monkeypatch.setattr(mf_nav.urllib.request, "urlopen", lambda *a, **k: _Resp())


def test_a_direct_plan_without_the_word_growth_is_still_returned(monkeypatch):
    """The bug, stated as a test. This exact fund is held by the household."""
    _feed(monkeypatch, ["Motilal Oswal Nifty Midcap 150 Index Fund - Direct Plan"])
    names = [s["scheme_name"] for s in mf_nav.search_schemes("motilal midcap")]
    assert names == ["Motilal Oswal Nifty Midcap 150 Index Fund - Direct Plan"]


def test_the_regular_plan_is_still_excluded(monkeypatch):
    """The filter's real job. A regular plan carries a distributor commission, so offering it beside
    the direct plan invites picking the wrong one by a single word."""
    _feed(monkeypatch, [
        "Motilal Oswal Nifty Midcap 150 Index Fund - Regular Plan",
        "Motilal Oswal Nifty Midcap 150 Index Fund - Direct Plan",
    ])
    names = [s["scheme_name"] for s in mf_nav.search_schemes("motilal midcap")]
    assert names == ["Motilal Oswal Nifty Midcap 150 Index Fund - Direct Plan"]


@pytest.mark.parametrize("payout", [
    "Parag Parikh Flexi Cap Fund - Direct Plan - IDCW",
    "Parag Parikh Flexi Cap Fund - Direct Plan - Dividend Payout",
    "Parag Parikh Flexi Cap Fund - Direct Plan - Dividend Reinvestment",
])
def test_dividend_variants_are_excluded_even_though_they_are_direct(monkeypatch, payout):
    """These are direct plans, so a name-contains-Direct rule alone would let them through. Holding
    the IDCW variant when you meant growth silently changes what the money does."""
    _feed(monkeypatch, [payout, "Parag Parikh Flexi Cap Fund - Direct Plan - Growth"])
    names = [s["scheme_name"] for s in mf_nav.search_schemes("parag parikh")]
    assert names == ["Parag Parikh Flexi Cap Fund - Direct Plan - Growth"]


def test_an_explicit_growth_direct_plan_still_matches(monkeypatch):
    """The old behaviour has to keep working; every other fund the household holds is named this way."""
    _feed(monkeypatch, ["Nippon India Nifty Smallcap 250 Index Fund - Direct Plan - Growth Option"])
    assert len(mf_nav.search_schemes("nippon smallcap")) == 1


def test_turning_the_filter_off_returns_everything(monkeypatch):
    _feed(monkeypatch, [
        "Motilal Oswal Nifty Midcap 150 Index Fund - Regular Plan",
        "Motilal Oswal Nifty Midcap 150 Index Fund - Direct Plan",
    ])
    assert len(mf_nav.search_schemes("motilal", direct_growth_only=False)) == 2
