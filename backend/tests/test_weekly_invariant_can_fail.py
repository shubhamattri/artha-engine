"""The weekly-target invariant has to be capable of failing.

Its first version compared `sum(future targets)` against `remaining`. But `weekly_breakdown` DEFINES
the future targets to tile `remaining` exactly, so the comparison was true by construction — an
identity wearing a check's clothes. It reported "ok" on every run for as long as it existed while
the Plan tab told the household it could still spend more than was actually left.

A check that cannot fail is worse than no check: it is a green light with nothing behind it. These
tests feed it the buggy shape directly and require it to go red.
"""

from services.consistency import Report, check_future_week_targets_fit_what_remains

# The shape of the live cycle the bug was found on. Figures are illustrative; the arithmetic is preserved.
BUGGY = {
    "total_target_inr": 124_000.0,
    "net_spent_inr": 47_500.0,
    "weeks": [
        {"week": 1, "spent": 43_300.0, "target": 28_000.0, "is_current": False, "is_future": False},
        {"week": 2, "spent": 4_200.0, "target": 28_000.0, "is_current": True, "is_future": False},
        {"week": 3, "spent": 0.0, "target": 31_500.0, "is_current": False, "is_future": True},
        {"week": 4, "spent": 0.0, "target": 31_500.0, "is_current": False, "is_future": True},
        {"week": 5, "spent": 0.0, "target": 13_500.0, "is_current": False, "is_future": True},
    ],
}

FIXED = {
    "total_target_inr": 124_000.0,
    "net_spent_inr": 47_500.0,
    "weeks": [
        {"week": 1, "spent": 43_300.0, "target": 28_000.0, "is_current": False, "is_future": False},
        {"week": 2, "spent": 4_200.0, "target": 28_000.0, "is_current": True, "is_future": False},
        {"week": 3, "spent": 0.0, "target": 21_700.0, "is_current": False, "is_future": True},
        {"week": 4, "spent": 0.0, "target": 21_700.0, "is_current": False, "is_future": True},
        {"week": 5, "spent": 0.0, "target": 9_300.0, "is_current": False, "is_future": True},
    ],
}


def _run(monkeypatch, payload: dict):
    import api.budget
    monkeypatch.setattr(api.budget, "compute_budget", lambda **kw: payload)
    report = Report()
    check_future_week_targets_fit_what_remains(None, 1, report)
    return [f for f in report.findings if f.check == "weekly_bar_within_budget"][0]


def test_it_goes_red_on_the_shape_that_shipped(monkeypatch):
    """The shape the Plan tab once served (illustrative figures). 23,800 still owed to the week in
    progress, plus 76,500 shared out among the weeks after it, against 76,500 actually left."""
    f = _run(monkeypatch, BUGGY)
    assert f.severity == "error", f"the check passed on the buggy numbers: {f.detail}"
    assert "100,300" in f.detail and "76,500" in f.detail, f.detail


def test_it_goes_green_once_the_current_week_is_reserved(monkeypatch):
    f = _run(monkeypatch, FIXED)
    assert f.severity == "ok", f.detail


def test_it_is_not_an_identity(monkeypatch):
    """The two payloads differ only in the future targets and produce different verdicts. If the
    check were still comparing a quantity against its own definition both would read the same."""
    assert _run(monkeypatch, BUGGY).severity != _run(monkeypatch, FIXED).severity


def test_a_current_week_already_overspent_cannot_make_it_red_on_its_own(monkeypatch):
    """Negative headroom is not a claim on the budget. Without the floor, an overspent current week
    would subtract from the total and could mask a genuine over-promise in the weeks after it."""
    payload = {
        "total_target_inr": 124_000.0,
        "net_spent_inr": 47_500.0,
        "weeks": [
            {"week": 1, "spent": 10_000.0, "target": 28_000.0, "is_current": False, "is_future": False},
            {"week": 2, "spent": 37_500.0, "target": 28_000.0, "is_current": True, "is_future": False},
            {"week": 3, "spent": 0.0, "target": 76_500.0, "is_current": False, "is_future": True},
        ],
    }
    f = _run(monkeypatch, payload)
    assert f.severity == "ok", f.detail          # 0 headroom + 76,500 == 76,500 left, exactly
