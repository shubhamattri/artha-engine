"""Tests for evals/number_check.py — the deterministic number verifier (T7).

Three violation kinds are tested:
  (a) hallucinated_number  — cited value doesn't match DB recompute
  (b) gross_pool_as_free   — "free/headroom" claim cites the pool, not actually_free
  (c) contradiction        — "over budget" + "plenty free" in the same answer

Most tests use monkeypatching to inject a fake DB snapshot rather than a real Postgres
session.  Contradiction tests are pure-text (no DB at all).
"""

from __future__ import annotations



# ── Helpers ───────────────────────────────────────────────────────────────────

def _cited(label: str, value: float, tool: str,
           unit: str = "INR", source: str = "tool") -> dict:
    """Build a cited_numbers entry the way T4 assembles them."""
    return {"label": label, "value": value, "unit": unit,
            "tool": tool, "source": source, "verified": None}


def _called(name: str, lens: str = "cycle", ok: bool = True) -> dict:
    """Build a tools_called entry."""
    return {"name": name, "input": {"lens": lens}, "ok": ok}


def _mock_cashflow(pool: float = 150_000.0) -> dict:
    return {
        "income": 300_000.0, "emi": 50_000.0, "fixed": 30_000.0,
        "saving": 25_000.0, "sinking": 10_000.0, "insurance": 5_000.0,
        "leftover": pool,
    }


def _mock_budget(spent: float = 145_000.0, limit: float = 150_000.0) -> dict:
    return {
        "net_spent_inr": spent,
        "items": [{"category": "food", "limit_inr": limit, "spent_inr": spent}],
    }


# ── Contradiction tests (pure text, no DB) ────────────────────────────────────

class TestContradiction:
    def test_over_budget_and_plenty_free_flagged(self):
        from evals.number_check import check_numbers
        answer = (
            "You're over budget this cycle by about ₹16,000, "
            "but you still have plenty of headroom to buy the briefcase."
        )
        result = check_numbers(answer, [], [], None, 1)
        kinds = [v["kind"] for v in result["violations"]]
        assert "contradiction" in kinds
        assert result["passed"] is False

    def test_over_budget_without_plenty_free_not_flagged(self):
        from evals.number_check import check_numbers
        answer = (
            "You're over budget this cycle — I'd hold off on the purchase for now."
        )
        result = check_numbers(answer, [], [], None, 1)
        kinds = [v["kind"] for v in result["violations"]]
        assert "contradiction" not in kinds

    def test_plenty_free_without_over_budget_not_flagged(self):
        from evals.number_check import check_numbers
        answer = "You have plenty of headroom this cycle — the briefcase fits easily."
        result = check_numbers(answer, [], [], None, 1)
        kinds = [v["kind"] for v in result["violations"]]
        assert "contradiction" not in kinds

    def test_contradiction_variant_phrases(self):
        from evals.number_check import check_numbers
        # "exceeded" matches _OVER_BUDGET_RE; "lots of" + "available" matches _PLENTY_RE + _FREE_WORDS_RE
        answer = (
            "You've exceeded your budget already. "
            "That said, there's lots of available cash left in your account."
        )
        result = check_numbers(answer, [], [], None, 1)
        kinds = [v["kind"] for v in result["violations"]]
        assert "contradiction" in kinds

    def test_empty_answer_no_violations(self):
        from evals.number_check import check_numbers
        result = check_numbers("", [], [], None, 1)
        assert result["passed"] is True
        assert result["violations"] == []


# ── Gross-pool-as-free tests (monkeypatched DB) ───────────────────────────────

class TestGrossPoolAsFree:
    """The core ₹1,50,000 bug class: pool presented as free cash."""

    def _setup(self, monkeypatch):
        """Inject pool=₹1,50,000, spent=₹1,45,000 → actually_free≈₹5,000."""
        import evals.number_check as nc
        monkeypatch.setattr(nc, "_compute_cashflow", lambda db, hh: _mock_cashflow(150_000))
        monkeypatch.setattr(nc, "_compute_budget",
                            lambda db, hh, mode: _mock_budget(145_000, 150_000))
        monkeypatch.setattr(nc, "_compute_debt", lambda db, hh: {"total_balance": 0, "total_emi": 0})
        monkeypatch.setattr(nc, "_compute_assets", lambda db, hh: {"total_inr": 0, "by_type": {}})

    def test_pool_cited_as_free_flagged(self, monkeypatch):
        self._setup(monkeypatch)
        from evals.number_check import check_numbers
        # Classic bug: advisor says the pool is free
        answer = "You have ₹1,50,000 free to spend this cycle — go ahead and buy it!"
        result = check_numbers(answer, [], [_called("actually_free")], object(), 1)
        kinds = [v["kind"] for v in result["violations"]]
        assert "gross_pool_as_free" in kinds, (
            "Pool presented as free should be a violation; violations: %s" % result["violations"]
        )
        assert result["passed"] is False

    def test_actually_free_cited_correctly_passes(self, monkeypatch):
        self._setup(monkeypatch)
        from evals.number_check import check_numbers
        # Correct: advisor cites actually_free (₹5,000)
        answer = "After what's already spent, you have about ₹5,000 actually free this cycle."
        result = check_numbers(answer, [], [_called("actually_free")], object(), 1)
        kinds = [v["kind"] for v in result["violations"]]
        assert "gross_pool_as_free" not in kinds

    def test_no_free_word_near_pool_passes(self, monkeypatch):
        self._setup(monkeypatch)
        from evals.number_check import check_numbers
        # ₹1,50,000 mentioned as the *pool* without calling it "free" — not a violation
        answer = (
            "Your monthly variable-spend pool is ₹1,50,000, "
            "but ₹1,45,000 is already spent, leaving only ₹5,000 for the rest of the cycle."
        )
        result = check_numbers(answer, [], [_called("actually_free")], object(), 1)
        kinds = [v["kind"] for v in result["violations"]]
        assert "gross_pool_as_free" not in kinds

    def test_honest_contrast_sentence_exempt(self, monkeypatch):
        """Sentence that cites BOTH pool AND actually_free is an honest contrast — exempt."""
        self._setup(monkeypatch)  # pool=150000, af=5000
        from evals.number_check import check_numbers
        # Same sentence mentions pool (₹1,50,000) AND actually_free (₹5,000) near "free"
        # The exemption rule: if af_figure is present in same sentence, skip the check
        answer = "The pool is ₹1,50,000 but only ₹5,000 is actually free this cycle."
        result = check_numbers(answer, [], [_called("actually_free")], object(), 1)
        kinds = [v["kind"] for v in result["violations"]]
        assert "gross_pool_as_free" not in kinds, (
            "Honest contrast should be exempt; violations: %s" % result["violations"]
        )

    def test_pool_equals_actually_free_no_false_positive(self, monkeypatch):
        """When pool ≈ actually_free (nothing spent yet), no violation should fire."""
        import evals.number_check as nc
        monkeypatch.setattr(nc, "_compute_cashflow", lambda db, hh: _mock_cashflow(100_000))
        monkeypatch.setattr(nc, "_compute_budget",
                            lambda db, hh, mode: _mock_budget(50, 100_000))  # barely any spend
        monkeypatch.setattr(nc, "_compute_debt", lambda db, hh: {"total_balance": 0, "total_emi": 0})
        monkeypatch.setattr(nc, "_compute_assets", lambda db, hh: {"total_inr": 0, "by_type": {}})
        from evals.number_check import check_numbers
        answer = "You have ₹1,00,000 free this cycle — enjoy!"
        result = check_numbers(answer, [], [_called("actually_free")], object(), 1)
        kinds = [v["kind"] for v in result["violations"]]
        # pool ≈ actually_free (diff ≈ ₹50, below _TEXT_ABS=100) → no gross_pool violation
        assert "gross_pool_as_free" not in kinds


# ── Hallucinated-number tests (monkeypatched DB) ─────────────────────────────

class TestHallucinatedNumber:

    def _setup(self, monkeypatch):
        import evals.number_check as nc
        monkeypatch.setattr(nc, "_compute_cashflow", lambda db, hh: _mock_cashflow(150_000))
        monkeypatch.setattr(nc, "_compute_budget",
                            lambda db, hh, mode: _mock_budget(145_000, 150_000))
        monkeypatch.setattr(nc, "_compute_debt", lambda db, hh: {"total_balance": 0, "total_emi": 0})
        monkeypatch.setattr(nc, "_compute_assets", lambda db, hh: {"total_inr": 0, "by_type": {}})

    def test_wrong_actually_free_in_cited_flagged(self, monkeypatch):
        self._setup(monkeypatch)
        from evals.number_check import check_numbers
        # Model cited pool (₹1,50,000) as "actually_free_inr" → hallucination
        cn = [_cited("actually_free_inr", 150_000, "actually_free")]
        result = check_numbers("You have ₹1,50,000 free.", cn, [_called("actually_free")], object(), 1)
        kinds = [v["kind"] for v in result["violations"]]
        assert "hallucinated_number" in kinds

    def test_correct_actually_free_in_cited_passes(self, monkeypatch):
        self._setup(monkeypatch)
        from evals.number_check import check_numbers
        # Model correctly cited actually_free_inr ≈ 5,000
        cn = [_cited("actually_free_inr", 5_000.0, "actually_free")]
        result = check_numbers("You have ₹5,000 free.", cn, [_called("actually_free")], object(), 1)
        kinds = [v["kind"] for v in result["violations"]]
        assert "hallucinated_number" not in kinds

    def test_wrong_income_flagged(self, monkeypatch):
        self._setup(monkeypatch)
        from evals.number_check import check_numbers
        # Correct income is ₹3,00,000 but model cited ₹3,50,000
        cn = [_cited("income_inr", 350_000, "cashflow_snapshot")]
        result = check_numbers("Your income is ₹3,50,000.", cn, [_called("cashflow_snapshot")], object(), 1)
        kinds = [v["kind"] for v in result["violations"]]
        assert "hallucinated_number" in kinds

    def test_within_tolerance_passes(self, monkeypatch):
        self._setup(monkeypatch)
        from evals.number_check import check_numbers
        # ₹3,00,000 ± ₹2 (absolute) is within tolerance
        cn = [_cited("income_inr", 299_999.5, "cashflow_snapshot")]
        result = check_numbers("Your income is ₹3,00,000.", cn, [_called("cashflow_snapshot")], object(), 1)
        kinds = [v["kind"] for v in result["violations"]]
        assert "hallucinated_number" not in kinds

    def test_ground_source_skipped(self, monkeypatch):
        self._setup(monkeypatch)
        from evals.number_check import check_numbers
        # source='ground' = KB definition → not DB-derived → never flagged
        cn = [_cited("foir_cap_pct", 50.0, "knowledge", unit="pct", source="ground")]
        result = check_numbers("FOIR cap is 50%.", cn, [], object(), 1)
        assert result["passed"] is True

    def test_failed_tool_cited_number_skipped(self, monkeypatch):
        self._setup(monkeypatch)
        from evals.number_check import check_numbers
        # Tool call failed (ok=False) → skip verification of its cited number
        cn = [_cited("actually_free_inr", 999_999, "actually_free")]
        called = [_called("actually_free", ok=False)]
        result = check_numbers("No info available.", cn, called, object(), 1)
        kinds = [v["kind"] for v in result["violations"]]
        assert "hallucinated_number" not in kinds

    def test_no_db_cited_numbers_skipped(self):
        from evals.number_check import check_numbers
        # db=None → canonical is empty → cited number check can't run → no false positive
        cn = [_cited("actually_free_inr", 999_999, "actually_free")]
        result = check_numbers("Some answer.", cn, [_called("actually_free")], None, 1)
        kinds = [v["kind"] for v in result["violations"]]
        assert "hallucinated_number" not in kinds


# ── Lens inference ────────────────────────────────────────────────────────────

class TestLensInference:
    def test_cycle_lens_from_tools(self):
        from evals.number_check import _infer_lens
        tc = [{"name": "actually_free", "input": {"lens": "cycle"}, "ok": True}]
        assert _infer_lens(tc) == "cycle"

    def test_month_lens_from_tools(self):
        from evals.number_check import _infer_lens
        tc = [{"name": "budget_status", "input": {"lens": "month"}, "ok": True}]
        assert _infer_lens(tc) == "month"

    def test_default_to_cycle_when_no_lens(self):
        from evals.number_check import _infer_lens
        assert _infer_lens([]) == "cycle"
        assert _infer_lens([{"name": "cashflow_snapshot", "input": {}, "ok": True}]) == "cycle"


# ── INR parsing ───────────────────────────────────────────────────────────────

class TestInrParsing:
    def test_indian_grouping(self):
        from evals.number_check import _parse_inr
        assert _parse_inr("1,50,000") == 150_000.0
        assert _parse_inr("5,000") == 5_000.0
        assert _parse_inr("3,000") == 3_000.0

    def test_decimal(self):
        from evals.number_check import _parse_inr
        assert _parse_inr("531.09") == 531.09
        assert _parse_inr("1,000.50") == 1_000.50


# ── Tolerance helper ──────────────────────────────────────────────────────────

class TestTolerance:
    def test_within_abs_tol(self):
        from evals.number_check import _within_tolerance
        assert _within_tolerance(5000.0, 5000.5, 2.0, 0.005)    # within ₹2
        # 5545 IS within 0.5% of 5000 (0.5% = ₹27.7) — the max(abs,rel) combiner matches it.
        assert _within_tolerance(5000.0, 5005.0, 2.0, 0.005)
        assert not _within_tolerance(5000.0, 5060.0, 2.0, 0.005)  # ₹60 > both ₹2 and 0.5% (₹25)

    def test_rel_dominates_for_large_numbers(self):
        from evals.number_check import _within_tolerance
        # 1% of ₹2,00,000 = ₹2,000; within 0.5% rel → must be within ₹1,000
        assert _within_tolerance(200_000, 200_500, 2.0, 0.005)    # 500 < 0.5% × 200k = 1000
        assert not _within_tolerance(200_000, 202_000, 2.0, 0.005)  # 2000 > 1000


# ── P0 regression — the briefcase scenario ───────────────────────────────────

class TestP0FreeCashBriefcase:
    """Locks the exact behaviour that caught the ₹1,50,000 FREE-CASH bug."""

    def test_buggy_advisor_answer_fails(self, monkeypatch):
        """Old advisor: 'you have ₹1,50,000 of free headroom — buy it'. Must fail."""
        import evals.number_check as nc
        monkeypatch.setattr(nc, "_compute_cashflow", lambda db, hh: _mock_cashflow(150_000))
        monkeypatch.setattr(nc, "_compute_budget",
                            lambda db, hh, mode: _mock_budget(145_000, 150_000))
        monkeypatch.setattr(nc, "_compute_debt", lambda db, hh: {"total_balance": 0, "total_emi": 0})
        monkeypatch.setattr(nc, "_compute_assets", lambda db, hh: {"total_inr": 0, "by_type": {}})
        from evals.number_check import check_numbers

        # The model simultaneously says:
        #   (a) "₹1,50,000 is sitting there free" → gross_pool_as_free (pool near "free", no af in same sentence)
        #   (b) "over budget" + "plenty of free" → contradiction
        #   (c) cited actually_free_inr=₹1,50,000 in cited_numbers → hallucinated_number (DB says ₹5,000)
        buggy_answer = (
            "You're over budget this cycle BUT you have plenty of free headroom — "
            "₹1,50,000 is sitting there free for you to use! "
            "A ₹3,000 briefcase is no problem."
        )
        cn = [_cited("actually_free_inr", 150_000, "actually_free")]
        result = check_numbers(buggy_answer, cn, [_called("actually_free")], object(), 1)

        assert result["passed"] is False
        kinds = {v["kind"] for v in result["violations"]}
        # All three violation types must fire for this classic bug
        assert "hallucinated_number" in kinds, "wrong cited figure not caught"
        assert "gross_pool_as_free" in kinds, "pool presented as free not caught"
        assert "contradiction" in kinds, "over-budget + plenty-free contradiction not caught"

    def test_correct_advisor_answer_passes(self, monkeypatch):
        """New advisor: cites actually_free ≈ ₹5,000, says it's tight. Must pass."""
        import evals.number_check as nc
        monkeypatch.setattr(nc, "_compute_cashflow", lambda db, hh: _mock_cashflow(150_000))
        monkeypatch.setattr(nc, "_compute_budget",
                            lambda db, hh, mode: _mock_budget(145_000, 150_000))
        monkeypatch.setattr(nc, "_compute_debt", lambda db, hh: {"total_balance": 0, "total_emi": 0})
        monkeypatch.setattr(nc, "_compute_assets", lambda db, hh: {"total_inr": 0, "by_type": {}})
        from evals.number_check import check_numbers

        good_answer = (
            "You're over budget this cycle by about ₹16,000. "
            "The pool is ₹1,50,000 but ₹1,45,000 is already spent — "
            "only ₹5,000 is actually free. A ₹3,000 briefcase technically fits, "
            "but it would wipe out your remaining headroom. "
            "I'd wait for next cycle or use a staggered purchase."
        )
        cn = [_cited("actually_free_inr", 5_000.0, "actually_free")]
        result = check_numbers(good_answer, cn, [_called("actually_free")], object(), 1)

        assert result["passed"] is True, (
            "Correct advisor answer should pass; violations: %s" % result["violations"]
        )


def test_gross_pool_as_free_caught_in_lakh_shorthand():
    """REGRESSION (the ₹150k bug rephrased): '₹1.5 lakh free' must be flagged exactly like
    '₹1,50,000 free' — the verifier cannot be bypassed by lakh/crore/k shorthand or by a
    decimal point splitting the sentence."""
    from evals.number_check import _check_gross_pool_as_free
    assert _check_gross_pool_as_free("You've got ₹1.5 lakh free headroom, just buy it.",
                                     pool=150000, actually_free=5000)
    assert _check_gross_pool_as_free("You have ₹1,50,000 free to spend right now.",
                                     pool=150000, actually_free=5000)
    # honest contrast (pool AND actually-free both cited) must NOT flag
    assert not _check_gross_pool_as_free("Pool is ₹1.5 lakh but only ₹5,000 is actually free.",
                                         pool=150000, actually_free=5000)
