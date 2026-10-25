"""Gmail auto-pull endpoints — fetch statement attachments and ingest them.

  GET  /gmail/status   is Gmail connected? + the active search query
  POST /gmail/sync     pull matching statement attachments → ingest (auto-detect + dedup)
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from api.deps import get_current_household
from config import settings
from database import get_db
from models.tenant import Household
from services import gmail_sync
from services.ingest import ingest_statement

router = APIRouter()


@router.get("/gmail/status")
def gmail_status(household: Household = Depends(get_current_household)):
    """The mailbox search string is machine config rather than household data, but it names the
    sender addresses this household's statements arrive from, so it sits behind the same gate as
    everything else."""
    return {"connected": gmail_sync.is_connected(), "query": settings.gmail_query}


@router.post("/gmail/sync")
def gmail_sync_now(db: Session = Depends(get_db),
                   household: Household = Depends(get_current_household)):
    if not gmail_sync.is_connected():
        raise HTTPException(
            status_code=400,
            detail=("Gmail isn't connected yet. From backend/, run "
                    "`python scripts/gmail_auth.py` once to authorize (read-only)."),
        )
    attachments = gmail_sync.fetch_statement_attachments()
    # household_id was omitted here, so every fetched statement fell to ingest_statement's
    # `household_id=1` default regardless of who ran the sync.
    results = [ingest_statement(db, fn, data, household_id=household.id)
               for fn, data in attachments]
    new_total = sum(r.get("transactions_new", 0) for r in results if r.get("ok"))
    return {
        "fetched": len(attachments),
        "ingested_ok": sum(1 for r in results if r.get("ok")),
        "transactions_new": new_total,
        "results": results,
    }
