"""Nothing that writes a household's transactions may run without a preview, and nothing may move
their totals by more than it can explain.

An import ran against live data and reported "0 new transactions" while inserting 43 rows. The caller
read result.get("added", result.get("inserted", 0)); the real key is transactions_new, so both
lookups missed and the default won. A wrong key name became a reassuring zero and the write was
committed on the strength of it.
"""

import pytest

from services.ingest_result import IngestGuardTripped, IngestResult


def test_a_missing_field_raises_instead_of_defaulting_to_zero():
    """The whole failure in one assertion: .get() on a dict invents a default, attribute access on a
    dataclass does not."""
    r = IngestResult(ok=True, filename="x.pdf", parsed=43, inserted=43)
    with pytest.raises(AttributeError):
        _ = r.added                      # the name the caller guessed
    assert r.inserted == 43


def test_changed_rows_is_the_one_number_a_caller_should_read():
    live = IngestResult(ok=True, filename="x.pdf", parsed=43, inserted=43)
    preview = IngestResult(ok=True, filename="x.pdf", parsed=43, would_insert=43, dry_run=True)
    assert live.changed_rows == 43
    assert preview.changed_rows == 43     # same question, both modes
    assert preview.inserted == 0          # ...but a dry run wrote nothing


def test_a_failed_result_reports_zero_change():
    r = IngestResult(ok=False, filename="x.pdf", error="Password-protected")
    assert r.failed and r.changed_rows == 0


def test_the_guard_is_an_exception_not_a_flag():
    """A returned flag can be ignored the same way a zero was. This cannot."""
    assert issubclass(IngestGuardTripped, RuntimeError)


def test_as_dict_still_carries_the_legacy_keys():
    """The API response shape is a contract; the typed object is for internal callers."""
    d = IngestResult(ok=True, filename="x.pdf", parsed=9, inserted=9).as_dict()
    assert d["transactions_parsed"] == 9
    assert d["transactions_new"] == 9
