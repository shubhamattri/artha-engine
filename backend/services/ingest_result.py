"""A typed answer from an ingest, so a caller cannot quietly misread it.

An import ran against live data and reported "0 new transactions" while inserting 43 rows. The
caller read `result.get("added", result.get("inserted", 0))`; the key is `transactions_new`, so both
lookups missed and the default won. A wrong key name silently became a reassuring zero, and the write
was committed on the strength of it.

Attribute access on a dataclass raises for a name that does not exist. `.get()` on a dict invents a
default. That difference is the whole reason this exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class IngestResult:
    ok: bool
    filename: str
    error: str | None = None

    parsed: int = 0            # rows the parser produced
    inserted: int = 0          # rows actually written (0 in a dry run)
    would_insert: int = 0      # rows a dry run WOULD have written
    duplicates_merged: int = 0
    superseded: int = 0

    bank: str | None = None
    card_type: str | None = None
    card_last4: str | None = None
    stmt_month: str | None = None
    dry_run: bool = False
    statement_id: int | None = None
    sample: list[dict] = field(default_factory=list)   # first few rows, for a preview

    @property
    def changed_rows(self) -> int:
        """What this call actually did, or would do. The number a caller means when it asks
        'did anything happen', and the one that must never be inferred from a missing key."""
        return self.would_insert if self.dry_run else self.inserted

    @property
    def failed(self) -> bool:
        return not self.ok

    def as_dict(self) -> dict:
        """For API responses. Internal callers should use the attributes."""
        return {
            "ok": self.ok, "filename": self.filename, "error": self.error,
            "transactions_parsed": self.parsed, "transactions_new": self.inserted,
            "would_insert": self.would_insert, "auto_merged": self.duplicates_merged,
            "superseded_provisional": self.superseded, "bank": self.bank,
            "card_type": self.card_type, "card_last4": self.card_last4,
            "stmt_month": self.stmt_month, "dry_run": self.dry_run,
            "statement_id": self.statement_id, "sample": self.sample,
        }


class IngestGuardTripped(RuntimeError):
    """An ingest moved a household's totals by more than the rows it added can explain.

    Raised AFTER the transaction is rolled back, so the household's data is unchanged. The number
    that matters to a person is the cycle's net spend, and if that moves for a reason the row count
    cannot account for, the safe assumption is that something was double-counted.
    """
