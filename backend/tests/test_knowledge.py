"""Unit tests for the T5 Knowledge Base (knowledge.py).

All tests are pure — no DB, no LLM, no network. They use temporary directories
so the real backend/knowledge/ files are never touched during CI.
"""
from __future__ import annotations

import sys
from pathlib import Path


# Ensure backend/ is on sys.path so `import services.knowledge` works.
# Note: this is module-level path manipulation only (no os.chdir).
_BACKEND = Path(__file__).parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import services.knowledge as kb


# ─── helpers ──────────────────────────────────────────────────────────────────

def _set_kb_dir(tmp_path: Path) -> None:
    """Point the knowledge module at a temp directory and clear its cache."""
    kb._KB_DIR = tmp_path
    kb._facts_cache = None


# ─── sample content ───────────────────────────────────────────────────────────

DEFINITIONS_MD = """\
# Test Definitions

## Leftover is a pool not free cash

The leftover is the variable-spend pool. Every grocery and dining debit comes
from it throughout the month. It is not spare cash sitting unused.

## actually_free is pool minus spent

actually_free = pool minus net_spent_this_period. When negative the household
is over the pool for this period. Never present the gross pool as free.

## FOIR cap

FOIR equals EMI divided by income times 100 percent. Banks cap at 50 percent.
FOIR headroom equals 0.50 times income minus current EMI total.
"""

LENS_MD = """\
# Test Lenses

## Cycle lens is the canonical default

The cycle lens tracks spend within each credit card billing cycle. ICICI bills
on the 12th, HDFC and Axis on the 22nd. Default to cycle for all current-period
questions.

## Month lens is calendar month

The month lens covers the 1st to the last day of the calendar month. Use when
the user explicitly asks about a calendar month.
"""


# ─── parsing tests ─────────────────────────────────────────────────────────────

class TestParseMd:
    def test_returns_one_fact_per_h2(self, tmp_path):
        f = tmp_path / "definitions.md"
        f.write_text(DEFINITIONS_MD, encoding="utf-8")
        facts = kb._parse_md(f)
        assert len(facts) == 3

    def test_title_is_heading_text(self, tmp_path):
        f = tmp_path / "definitions.md"
        f.write_text(DEFINITIONS_MD, encoding="utf-8")
        facts = kb._parse_md(f)
        assert facts[0]["title"] == "Leftover is a pool not free cash"
        assert facts[1]["title"] == "actually_free is pool minus spent"

    def test_source_is_filename(self, tmp_path):
        f = tmp_path / "definitions.md"
        f.write_text(DEFINITIONS_MD, encoding="utf-8")
        facts = kb._parse_md(f)
        assert all(fa["source"] == "definitions.md" for fa in facts)

    def test_id_is_slug_of_source_and_title(self, tmp_path):
        f = tmp_path / "definitions.md"
        f.write_text(DEFINITIONS_MD, encoding="utf-8")
        facts = kb._parse_md(f)
        assert facts[0]["id"].startswith("definitions_")
        assert "leftover" in facts[0]["id"]

    def test_id_is_stable(self, tmp_path):
        """Parsing the same file twice yields identical ids."""
        f = tmp_path / "definitions.md"
        f.write_text(DEFINITIONS_MD, encoding="utf-8")
        f1 = kb._parse_md(f)
        f2 = kb._parse_md(f)
        assert [fa["id"] for fa in f1] == [fa["id"] for fa in f2]

    def test_text_is_section_body(self, tmp_path):
        f = tmp_path / "definitions.md"
        f.write_text(DEFINITIONS_MD, encoding="utf-8")
        facts = kb._parse_md(f)
        assert "variable-spend pool" in facts[0]["text"]
        assert "0.50" in facts[2]["text"]

    def test_h1_preamble_is_skipped(self, tmp_path):
        """Lines before the first ## heading are not returned as a fact."""
        f = tmp_path / "definitions.md"
        f.write_text(DEFINITIONS_MD, encoding="utf-8")
        facts = kb._parse_md(f)
        assert not any(fa["title"] == "Test Definitions" for fa in facts)

    def test_empty_section_is_skipped(self, tmp_path):
        content = "## Has content\n\nSome text.\n\n## Empty section\n\n"
        f = tmp_path / "a.md"
        f.write_text(content, encoding="utf-8")
        facts = kb._parse_md(f)
        assert len(facts) == 1
        assert facts[0]["title"] == "Has content"

    def test_trailing_separator_stripped(self, tmp_path):
        content = "## Title\n\nBody text.\n\n---\n"
        f = tmp_path / "a.md"
        f.write_text(content, encoding="utf-8")
        facts = kb._parse_md(f)
        assert facts[0]["text"] == "Body text."

    def test_multiple_files_combined(self, tmp_path):
        (tmp_path / "a.md").write_text(DEFINITIONS_MD, encoding="utf-8")
        (tmp_path / "b.md").write_text(LENS_MD, encoding="utf-8")
        _set_kb_dir(tmp_path)
        facts = kb._load_all_facts()
        assert len(facts) == 5  # 3 from definitions + 2 from lens


# ─── tokeniser ────────────────────────────────────────────────────────────────

class TestTokenize:
    def test_lowercase(self):
        assert "foir" in kb._tokenize("FOIR cap")

    def test_stop_words_removed(self):
        tokens = kb._tokenize("what is the leftover")
        assert "the" not in tokens
        assert "is" not in tokens
        assert "leftover" in tokens

    def test_short_tokens_dropped(self):
        tokens = kb._tokenize("a b cd emi loan")
        # 'a' and 'b' are dropped (<2 chars), 'cd' kept
        assert "a" not in tokens
        assert "b" not in tokens
        assert "emi" in tokens
        assert "loan" in tokens

    def test_punctuation_stripped(self):
        tokens = kb._tokenize("pool − spent = actually_free")
        assert "pool" in tokens
        assert "spent" in tokens
        assert "actually_free" in tokens

    def test_empty_string(self):
        assert kb._tokenize("") == []


# ─── scoring ──────────────────────────────────────────────────────────────────

class TestScoreFact:
    def _fact(self, title: str, text: str) -> dict:
        return {"id": "test", "title": title, "text": text, "source": "test.md"}

    def test_title_match_scores_higher_than_text_only(self):
        f_title = self._fact("FOIR cap", "Some unrelated body text here.")
        f_text = self._fact("Unrelated title", "FOIR equals EMI over income.")
        tokens = kb._tokenize("FOIR")
        assert kb._score_fact(f_title, tokens) > kb._score_fact(f_text, tokens)

    def test_zero_score_for_no_match(self):
        f = self._fact("FOIR cap", "EMI and income limits.")
        tokens = kb._tokenize("zomato delivery")
        assert kb._score_fact(f, tokens) == 0.0

    def test_higher_frequency_scores_higher(self):
        f_once = self._fact("Pool", "The pool is important.")
        f_thrice = self._fact("Pool", "The pool is the pool and the pool is spent.")
        tokens = kb._tokenize("pool")
        assert kb._score_fact(f_thrice, tokens) > kb._score_fact(f_once, tokens)

    def test_empty_tokens(self):
        f = self._fact("FOIR", "EMI over income.")
        assert kb._score_fact(f, []) == 0.0


# ─── ground() ─────────────────────────────────────────────────────────────────

class TestGround:
    def test_returns_expected_shape(self, tmp_path):
        (tmp_path / "definitions.md").write_text(DEFINITIONS_MD, encoding="utf-8")
        _set_kb_dir(tmp_path)
        result = kb.ground("what is leftover")
        assert "facts" in result
        assert "summary" in result
        assert isinstance(result["facts"], list)
        assert isinstance(result["summary"], str)

    def test_each_fact_has_required_keys(self, tmp_path):
        (tmp_path / "definitions.md").write_text(DEFINITIONS_MD, encoding="utf-8")
        _set_kb_dir(tmp_path)
        result = kb.ground("pool")
        for fact in result["facts"]:
            assert "id" in fact
            assert "title" in fact
            assert "text" in fact
            assert "source" in fact

    def test_k_limits_results(self, tmp_path):
        (tmp_path / "definitions.md").write_text(DEFINITIONS_MD, encoding="utf-8")
        (tmp_path / "lens.md").write_text(LENS_MD, encoding="utf-8")
        _set_kb_dir(tmp_path)
        result = kb.ground("spend cycle pool free", k=2)
        assert len(result["facts"]) <= 2

    def test_pool_free_query_returns_leftover_fact(self, tmp_path):
        """Queries about free cash must surface the leftover-is-a-pool fact."""
        (tmp_path / "definitions.md").write_text(DEFINITIONS_MD, encoding="utf-8")
        _set_kb_dir(tmp_path)
        result = kb.ground("how much free cash do I have leftover")
        assert result["facts"]
        titles = [f["title"] for f in result["facts"]]
        assert any("leftover" in t.lower() or "pool" in t.lower() for t in titles), (
            f"Expected a leftover/pool fact in top results, got: {titles}"
        )

    def test_foir_query_returns_foir_fact(self, tmp_path):
        """EMI / FOIR queries must surface the FOIR definition."""
        (tmp_path / "definitions.md").write_text(DEFINITIONS_MD, encoding="utf-8")
        _set_kb_dir(tmp_path)
        result = kb.ground("what is the FOIR cap for a new loan EMI")
        assert result["facts"]
        titles = [f["title"] for f in result["facts"]]
        assert any("foir" in t.lower() for t in titles), (
            f"Expected a FOIR fact in top results, got: {titles}"
        )

    def test_actually_free_query(self, tmp_path):
        """Queries about actually_free must return the correct formula fact."""
        (tmp_path / "definitions.md").write_text(DEFINITIONS_MD, encoding="utf-8")
        _set_kb_dir(tmp_path)
        result = kb.ground("actually free formula pool minus spent")
        assert result["facts"]
        titles = [f["title"] for f in result["facts"]]
        assert any("actually" in t.lower() for t in titles), (
            f"Expected actually_free fact, got: {titles}"
        )

    def test_cycle_lens_query(self, tmp_path):
        """Cycle vs month lens queries must surface the lens facts."""
        (tmp_path / "lens.md").write_text(LENS_MD, encoding="utf-8")
        _set_kb_dir(tmp_path)
        result = kb.ground("cycle lens billing period default")
        assert result["facts"]
        titles = [f["title"] for f in result["facts"]]
        assert any("cycle" in t.lower() for t in titles), (
            f"Expected a cycle fact, got: {titles}"
        )

    def test_best_match_is_first(self, tmp_path):
        """The highest-scoring fact appears at index 0."""
        (tmp_path / "definitions.md").write_text(DEFINITIONS_MD, encoding="utf-8")
        _set_kb_dir(tmp_path)
        result = kb.ground("FOIR cap headroom")
        assert result["facts"]
        # FOIR fact should outrank others for a FOIR-specific query
        assert "foir" in result["facts"][0]["title"].lower()

    def test_empty_kb_dir(self, tmp_path):
        """Empty directory returns gracefully — no exception."""
        _set_kb_dir(tmp_path)  # no .md files
        result = kb.ground("any query")
        assert result["facts"] == []
        assert result["summary"]

    def test_missing_kb_dir(self, tmp_path):
        """Non-existent directory returns gracefully."""
        _set_kb_dir(tmp_path / "nonexistent")
        result = kb.ground("any query")
        assert result["facts"] == []

    def test_unmatched_query_returns_empty_facts(self, tmp_path):
        """A query that matches zero facts returns facts=[] not an arbitrary fact."""
        (tmp_path / "definitions.md").write_text(DEFINITIONS_MD, encoding="utf-8")
        _set_kb_dir(tmp_path)
        # A query with no overlap with any KB fact should yield no results.
        result = kb.ground("xylophone noodle completely unrelated query xyz")
        assert result["facts"] == [], (
            f"Expected empty facts for zero-match query, got: {result['facts']}"
        )
        assert result["summary"]  # summary should still be a non-empty string

    def test_summary_is_non_empty_string(self, tmp_path):
        (tmp_path / "definitions.md").write_text(DEFINITIONS_MD, encoding="utf-8")
        _set_kb_dir(tmp_path)
        result = kb.ground("leftover pool")
        assert isinstance(result["summary"], str)
        assert len(result["summary"]) > 0

    def test_summary_mentions_fact_title(self, tmp_path):
        (tmp_path / "definitions.md").write_text(DEFINITIONS_MD, encoding="utf-8")
        _set_kb_dir(tmp_path)
        # With exactly one fact returned for a k=1 call, summary includes the title
        single = kb.ground("leftover pool not free cash", k=1)
        # summary should contain the title of the top fact
        assert single["facts"][0]["title"] in single["summary"]

    def test_multi_file_retrieval(self, tmp_path):
        """Facts from different files are all searchable."""
        (tmp_path / "definitions.md").write_text(DEFINITIONS_MD, encoding="utf-8")
        (tmp_path / "lens.md").write_text(LENS_MD, encoding="utf-8")
        _set_kb_dir(tmp_path)
        result_foir = kb.ground("FOIR EMI income")
        result_lens = kb.ground("cycle lens billing month")
        foir_sources = {f["source"] for f in result_foir["facts"]}
        lens_sources = {f["source"] for f in result_lens["facts"]}
        assert "definitions.md" in foir_sources
        assert "lens.md" in lens_sources

    def test_bad_md_file_does_not_crash(self, tmp_path):
        """A malformed .md file is skipped; valid files still load."""
        (tmp_path / "good.md").write_text(DEFINITIONS_MD, encoding="utf-8")
        # Write a file with valid extension but content that would cause
        # read to succeed but yield nothing useful (no ## headings)
        (tmp_path / "empty_headings.md").write_text(
            "# Only H1 here\n\nNo H2 sections at all.\n",
            encoding="utf-8",
        )
        _set_kb_dir(tmp_path)
        result = kb.ground("leftover pool")
        # Should still return results from the good file
        assert result["facts"]


# ─── reload() ─────────────────────────────────────────────────────────────────

class TestReload:
    def test_reload_clears_cache(self, tmp_path):
        """reload() forces the next ground() call to re-read disk."""
        f = tmp_path / "definitions.md"
        f.write_text(DEFINITIONS_MD, encoding="utf-8")
        _set_kb_dir(tmp_path)

        assert kb.ground("leftover")["facts"], "the cache must be warm before the file changes"

        # Append a new fact on disk
        extra = tmp_path / "extra.md"
        extra.write_text("## Brand new fact\n\nThis is a new definition.\n", encoding="utf-8")

        # Without reload, cache holds old data
        kb.ground("brand new")
        # The new fact may or may not appear depending on cache state — but
        # after reload it MUST appear.
        kb.reload()

        result_after = kb.ground("brand new fact")
        assert any(f["title"] == "Brand new fact" for f in result_after["facts"]), (
            "Expected 'Brand new fact' to appear after reload()"
        )

    def test_reload_sets_cache_to_none(self, tmp_path):
        (tmp_path / "definitions.md").write_text(DEFINITIONS_MD, encoding="utf-8")
        _set_kb_dir(tmp_path)
        _ = kb.ground("leftover")  # populate cache
        assert kb._facts_cache is not None
        kb.reload()
        assert kb._facts_cache is None


# ─── slug helper ──────────────────────────────────────────────────────────────

class TestSlug:
    def test_lowercase_and_underscores(self):
        assert kb._slug("FOIR Cap") == "foir_cap"

    def test_special_chars_replaced(self):
        assert kb._slug("pool — not free") == "pool_not_free"

    def test_leading_trailing_underscores_stripped(self):
        result = kb._slug("  -- hello -- ")
        assert not result.startswith("_")
        assert not result.endswith("_")

    def test_max_length(self):
        long_str = "a" * 200
        assert len(kb._slug(long_str)) <= 64
