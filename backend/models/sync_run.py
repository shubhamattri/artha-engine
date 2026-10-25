"""A record that the sync actually ran, so silence can be told apart from health.

The sync had no heartbeat. When it stopped there was nothing to notice: the app kept serving the
last figures it had, and the only symptom was a person making a purchase and not seeing it. That is
the same shape as a scheduled job failing unnoticed for months — the absence of new rows looks
exactly like a quiet week.

One row per run. `ok` is False when the run raised, so a job that runs but fails every time is
distinguishable from one that is not running at all; those need different fixes.
"""

from datetime import datetime

from sqlalchemy import ForeignKey, JSON, Boolean, Column, DateTime, Integer, String

from database import Base


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id = Column(Integer, primary_key=True)
    household_id = Column(Integer, ForeignKey("households.id"), nullable=False, index=True)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    finished_at = Column(DateTime, nullable=True)
    ok = Column(Boolean, nullable=False, default=False)
    error = Column(String, nullable=True)
    summary = Column(JSON, nullable=True)
