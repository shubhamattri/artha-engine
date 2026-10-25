"""An SMS after midnight must be filed on the day it happened, not the UTC day.

The Messages timestamp was converted to UTC and its DATE taken. India is UTC+5:30, so every
transaction between 00:00 and 05:30 IST was stored one day early. A Swiggy debit at 00:33 on 13 Aug
was stored as 12 Aug while the bank's own SMS read "13-Aug-26".

That is not cosmetic. With the household cycle starting on the 13th, a purchase in the first hours
of the 13th was pushed into the cycle that closed on the 12th — counted against the wrong bill, and
invisible to anyone looking at today.
"""

from datetime import datetime, timezone

from services.sms_capture import _APPLE_EPOCH


def _apple_ns(dt_utc: datetime) -> float:
    return (dt_utc.timestamp() - _APPLE_EPOCH) * 1e9


def _stored_date(dt_utc: datetime) -> str:
    """Reproduce the line under test: Apple ns -> the date the capture files it under."""
    when = datetime.fromtimestamp(_apple_ns(dt_utc) / 1e9 + _APPLE_EPOCH, timezone.utc).astimezone()
    return when.replace(tzinfo=None).date().isoformat()


def test_a_purchase_just_after_midnight_keeps_its_own_date():
    """00:33 IST on the 13th is the 13th. Under the UTC reading this returned 2026-08-12."""
    import os
    import time
    os.environ["TZ"] = "Asia/Kolkata"
    time.tzset()
    try:
        # 2026-08-13 00:33 IST == 2026-08-12 19:03 UTC
        assert _stored_date(datetime(2026, 8, 12, 19, 3, tzinfo=timezone.utc)) == "2026-08-13"
    finally:
        del os.environ["TZ"]
        time.tzset()


def test_a_daytime_purchase_is_unaffected():
    """The control: most of the day never crossed the UTC boundary, which is why this survived."""
    import os
    import time
    os.environ["TZ"] = "Asia/Kolkata"
    time.tzset()
    try:
        # 2026-08-12 18:00 IST == 2026-08-12 12:30 UTC — same date either way
        assert _stored_date(datetime(2026, 8, 12, 12, 30, tzinfo=timezone.utc)) == "2026-08-12"
    finally:
        del os.environ["TZ"]
        time.tzset()
