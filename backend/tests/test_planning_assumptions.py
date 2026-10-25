"""Market/behavioural assumptions are configuration, not literals frozen in engine modules.

Two things are pinned here. (1) Today's defaults are unchanged, so moving them into config was a
refactor and not a silent re-forecast. (2) advisor's EMI-per-rupee is DERIVED from the configured
home-loan rate and tenure — the old 0.00884 literal restated 8.75%/20y and would have gone quietly
wrong the moment either changed. Changing the rate here must move the derived figure.
"""

from __future__ import annotations

import pytest

from api import afford
from config import settings
from services import advisor, dedup, projection
from services.loan_math import emi


def test_defaults_are_unchanged_after_moving_to_config():
    assert (afford.HOME_LOAN_RATE, afford.HOME_LOAN_YEARS) == (0.0875, 20)
    assert (afford.EQUITY_RETURN, afford.LTV, afford.FOIR_CAP) == (0.11, 0.80, 0.50)
    assert (projection.LUMP_FLOOR, projection.LUMP_MULTIPLE) == (2_000.0, 3.0)
    assert (projection.SURPRISE_DAMP, projection.WARMUP_DAYS) == (0.5, 5)
    assert (dedup.DATE_TOL_DAYS, dedup.TS_TOL_SEC) == (3, 900)


def test_emi_per_rupee_matches_the_old_literal_at_default_settings():
    # The 0.00884 that used to be hardcoded, now reproduced from the rate/tenure it came from.
    assert advisor._emi_per_rupee() == pytest.approx(0.00884, abs=5e-6)
    assert advisor._EMI_PER_RUPEE == advisor._emi_per_rupee()


def test_emi_per_rupee_tracks_the_configured_rate(monkeypatch):
    before = advisor._EMI_PER_RUPEE
    monkeypatch.setattr(settings, "home_loan_rate", 0.12)

    after = advisor._EMI_PER_RUPEE
    assert after > before                                   # a dearer loan costs more per rupee
    assert after == pytest.approx(emi(1.0, 12.0, 240))
    assert after != pytest.approx(0.00884, abs=5e-6)        # no longer pinned to the old constant


def test_emi_per_rupee_tracks_the_configured_tenure(monkeypatch):
    monkeypatch.setattr(settings, "home_loan_years", 10)
    assert advisor._EMI_PER_RUPEE == pytest.approx(emi(1.0, 8.75, 120))


def test_afford_loan_factor_follows_the_same_configured_rate(monkeypatch):
    monkeypatch.setattr(settings, "home_loan_rate", 0.12)
    # afford and advisor must never drift apart: both are the same amortisation of the same config.
    assert afford._emi_factor(settings.home_loan_rate, settings.home_loan_years) == pytest.approx(
        advisor._emi_per_rupee())


def test_projection_lump_threshold_follows_config(monkeypatch):
    assert projection.lump_threshold(100.0) == 2_000.0
    monkeypatch.setattr(settings, "lump_floor_inr", 500.0)
    monkeypatch.setattr(settings, "lump_multiple", 2.0)
    assert projection.lump_threshold(100.0) == 500.0        # floor still binds
    assert projection.lump_threshold(1_000.0) == 2_000.0    # multiple binds


def test_dedup_tolerances_follow_config(monkeypatch):
    monkeypatch.setattr(settings, "dedup_ts_tol_sec", 60)
    monkeypatch.setattr(settings, "dedup_date_tol_days", 1)
    assert dedup.TS_TOL_SEC == 60
    assert dedup.DATE_TOL_DAYS == 1


def test_no_config_field_is_wired_to_nothing():
    """Config that reads as tunable and changes nothing invites someone to tune it and conclude the
    system ignores them. dedup_t_match/dedup_t_review were exactly that: constants left over from a
    scoring design the rule-based matcher replaced."""
    import re
    from pathlib import Path

    from config import Settings

    backend = Path(__file__).resolve().parent.parent
    files = [f for f in backend.rglob("*.py")
             if not any(p in {".venv", "tests", "alembic"} for p in f.parts)]
    # config.py counts as a consumer: several fields exist only to compose another (db_host and
    # friends build db_url). Its own DEFINITION lines do not count, or every field reads itself.
    src_parts = []
    for f in files:
        text = f.read_text(errors="ignore")
        if f.name == "config.py":
            text = "\n".join(ln for ln in text.split("\n")
                             if not re.match(r"\s*[a-z_]+\s*:\s*[A-Za-z\[]", ln))
        src_parts.append(text)
    src = "\n".join(src_parts)
    orphans = [n for n in Settings.model_fields
               # No trailing \b: a quoted name is followed by a comma or paren, and \b after a
               # quote can never match. getattr(settings, "x", ...) is a real read.
               if not re.search(rf'(\bsettings\.{n}\b|\bself\.{n}\b|"{n}")', src)]
    assert not orphans, f"config fields nothing reads: {orphans}"
