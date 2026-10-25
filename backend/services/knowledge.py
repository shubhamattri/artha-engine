"""Knowledge Base — T5 of the Artha Advisor architecture.

Provides ground(query, k=4) -> {facts, summary}: keyword retrieval over *.md
files in backend/knowledge/. Each ## heading section is one retrievable
fact. No new pip deps, no LLM, no DB, no household scope (definitions are
universal — they apply equally to every household).

T3 (advisor_tools.py) wraps this as the Anthropic `ground` tool. household_id
is intentionally ABSENT from the tool schema because these definitions do not
vary by tenant.

T9 (coach/draft.py) appends kb_draft proposals as new ## sections to files in
backend/knowledge/. A human reviews and merges them — nothing auto-applies.
After merging, the coach calls knowledge.reload() so the next advisor turn picks
up the new facts without a process restart.

WHY THIS FILE EXISTS:
The two locked P0 advisor failures are definitional errors, not arithmetic
errors:
  (1) Presenting the gross leftover pool as \"free headroom\" while the household
      was already over budget.
  (2) Saying \"over budget BUT plenty free\" — a logical contradiction (if
      actually_free is negative there is nothing free).
Both failures stem from the model reasoning from training-data intuitions about
\"leftover = free cash\" rather than from Artha's actual definitions. Grounding
via this KB replaces that reasoning path with authoritative, tested facts.
T7's number-verifier enforces the arithmetic side; this file enforces the
definitional side.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

log = logging.getLogger("artha.knowledge")

# Location of the *.md files (resolved relative to this module's location).
# backend/services/knowledge.py  →  ../knowledge/
# This path is NOT under data/ so it is committed and ships with the codebase.
_KB_DIR: Path = Path(__file__).parent.parent / "knowledge"

# Module-level cache (None = not yet loaded; [] = loaded but empty)
# Use reload() to force a re-read (T9 calls this after merging a kb_draft).
_facts_cache: list[dict] | None = None

# ── Stop-words: remove common English glue words during keyword scoring.
# Deliberately keep financial terms (free, pool, spend, cycle, month, foir, emi,
# loan, capital, actually, gross) OUT of this set so they contribute to scoring.
_STOP: frozenset[str] = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "or", "and", "but", "not",
    "it", "its", "this", "that", "they", "them", "their", "what", "which",
    "who", "how", "if", "so", "then", "when", "than", "more", "less",
    "up", "out", "use", "used", "all", "any", "each", "also", "just",
    "only", "very", "per", "i", "my", "your", "we", "our", "me", "you",
})


# ── Parsing ────────────────────────────────────────────────────────────────────

def _slug(text: str) -> str:
    """Stable, lowercase id fragment from a heading or filename.

    >>> _slug("Leftover is a pool — not free cash")
    'leftover_is_a_pool_not_free_cash'
    """
    s = re.sub(r"[^a-z0-9]+", "_", text.lower().strip())
    return s.strip("_")[:64]


def _parse_md(path: Path) -> list[dict]:
    """Parse one *.md file into a list of fact dicts — one per ## heading.

    File format::

        # Optional H1 title (skipped)

        ## Fact title

        Fact text (may span multiple paragraphs).
        May include code, lists, formulas.

        ## Another fact title

        ...

    The fact id is derived from the source filename + heading text so it is
    stable across process restarts (T7 and T9 reference facts by id).

    Lines that are purely --- separators are stripped from fact text.
    """
    source = path.name
    raw = path.read_text(encoding="utf-8")

    # Prepend newline so the first ## is reliably caught by the split pattern.
    parts = re.split(r"\n##[ \t]+", "\n" + raw)
    source_slug = _slug(source.replace(".md", ""))

    facts: list[dict] = []
    for part in parts[1:]:  # parts[0] = preamble before first ##
        lines = part.splitlines()
        if not lines:
            continue
        title = lines[0].strip()
        text_lines = lines[1:]

        # Strip trailing --- separator lines and excess blank lines
        while text_lines and text_lines[-1].strip() in ("", "---", "---" * 3):
            text_lines.pop()
        text = "\n".join(text_lines).strip()

        if not title or not text:
            continue

        fact_id = f"{source_slug}_{_slug(title)}"
        facts.append({
            "id": fact_id,
            "title": title,
            "text": text,
            "source": source,
        })
    return facts


# ── Loading & cache ────────────────────────────────────────────────────────────

def _load_from_disk() -> list[dict]:
    """Read and parse every *.md file in _KB_DIR. Not cached — use _load_all_facts()."""
    if not _KB_DIR.exists():
        log.warning("[knowledge] KB directory not found: %s", _KB_DIR)
        return []
    facts: list[dict] = []
    for md_file in sorted(_KB_DIR.glob("*.md")):
        try:
            file_facts = _parse_md(md_file)
            facts.extend(file_facts)
            log.debug("[knowledge] loaded %d facts from %s", len(file_facts), md_file.name)
        except Exception as exc:  # noqa: BLE001 — bad KB file must not crash the advisor
            log.warning("[knowledge] failed to parse %s: %s", md_file.name, exc)
    log.info("[knowledge] %d total facts loaded from %s", len(facts), _KB_DIR)
    return facts


def _load_all_facts() -> list[dict]:
    """Return the cached fact list, loading from disk on first call."""
    global _facts_cache
    if _facts_cache is None:
        _facts_cache = _load_from_disk()
    return _facts_cache


def reload() -> None:
    """Force-reload knowledge files on the next ground() call.

    T9 (coach/draft.py) calls this after merging a kb_draft into a *.md file
    so the advisor picks up the new facts without a process restart.
    """
    global _facts_cache
    _facts_cache = None
    log.info("[knowledge] cache cleared — next ground() call will re-read %s", _KB_DIR)


# ── Retrieval ──────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens with stop-words removed.

    Keeps alphanumeric tokens only (strips punctuation).
    Short tokens (< 2 chars) are dropped unless they are clearly financial
    acronyms — but those are 3+ chars anyway (emi, roi, ltv, dti), so the
    2-char floor is safe.
    """
    tokens = re.findall(r"[a-z][a-z0-9_]*", text.lower())
    return [t for t in tokens if t not in _STOP and len(t) >= 2]


def _score_fact(fact: dict, tokens: list[str]) -> float:
    """Weighted keyword overlap score.

    Title match = 3× (title words are high-signal summaries of the fact).
    Text match = 1× (body provides evidence).
    Token frequency in the fact text is counted, so a fact that mentions the
    query term 5 times outscores one that mentions it once.
    """
    if not tokens:
        return 0.0
    title_l = fact["title"].lower()
    text_l = fact["text"].lower()
    score = 0.0
    for token in tokens:
        score += title_l.count(token) * 3.0
        score += text_l.count(token) * 1.0
    return score


# ── Public API ─────────────────────────────────────────────────────────────────

def ground(query: str, k: int = 4) -> dict:
    """Retrieve the top-k financial definitions/rules relevant to *query*.

    Args:
        query: Natural-language question or phrase from the advisor.
               Examples: "what is actually free to spend?",
                         "FOIR limit for a new loan",
                         "difference between cycle and month lens".
        k:     Maximum number of facts to return (default 4, contract minimum).

    Returns a dict shaped::

        {
            "facts": [
                {
                    "id":     str,   # stable slug, e.g. "definitions_leftover_is_a_pool"
                    "title":  str,   # heading text, e.g. "Leftover is the variable-spend pool"
                    "text":   str,   # full body text of the section
                    "source": str,   # filename, e.g. "definitions.md"
                },
                ...                 # ≤ k facts, best-match first
            ],
            "summary": str,         # one-liner the model can quote verbatim
        }

    SAFETY NOTES:
    - Returns an empty facts list (not an exception) when the KB is missing,
      empty, or when no fact scores above zero for the query, so the advisor
      degrades gracefully rather than raising.
    - Only facts with a positive relevance score are returned. No zero-score
      padding — surfacing an arbitrary irrelevant fact as \"grounding context\"
      would be worse than returning nothing.
    - This function has no DB, no LLM, no household_id. It is pure text lookup.
    """
    facts = _load_all_facts()
    if not facts:
        return {
            "facts": [],
            "summary": "Knowledge base is empty — no facts available for grounding.",
        }

    tokens = _tokenize(query)
    scored: list[tuple[float, int, dict]] = [
        (_score_fact(f, tokens), idx, f)
        for idx, f in enumerate(facts)
    ]
    # Primary sort: score descending. Secondary: original order (stable tie-break).
    scored.sort(key=lambda x: (-x[0], x[1]))

    # Collect up to k facts with a positive relevance score.
    # Once we hit a zero-score entry every subsequent entry also scores zero
    # (sorted descending), so we break immediately rather than padding with
    # irrelevant definitions that could mislead the advisor.
    top_k: list[dict] = []
    for score, _, fact in scored:
        if len(top_k) >= k:
            break
        if score == 0.0:
            break
        top_k.append(fact)

    if not top_k:
        return {
            "facts": [],
            "summary": "No relevant facts found for the query.",
        }

    # Summary: a one-liner the model can quote verbatim in a tool_result.
    if len(top_k) == 1:
        summary = f"{top_k[0]['title']} (from {top_k[0]['source']})"
    else:
        titles = "; ".join(f["title"] for f in top_k[:3])
        extra = f" (+{len(top_k) - 3} more)" if len(top_k) > 3 else ""
        summary = f"Retrieved {len(top_k)} fact(s): {titles}{extra}."

    return {"facts": top_k, "summary": summary}
