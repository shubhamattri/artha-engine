"""Resolving which household a background job runs for.

Request handlers get their household from the session. Background jobs — the launchd sync, the SMS
reader, the Gmail alert ingest — have no session, so they take `household_id` as an argument. Several
of them typed it `int | None = None` and then passed the `None` straight into their queries, which
silently means "every household in the database".

`None` has to mean something explicit. Here it means "the only household", and it raises if that is
not true, because a background job quietly picking one of several tenants is the failure this whole
piece of work exists to remove.
"""

from sqlalchemy.orm import Session


class AmbiguousHousehold(RuntimeError):
    """Raised when a job was not told which household to run for and there is more than one."""


def resolve_household_id(db: Session, household_id: int | None) -> int:
    """The household this job is for. Explicit wins; otherwise the sole one; otherwise raise."""
    if household_id is not None:
        return household_id

    from models.tenant import Household

    ids = [h.id for h in db.query(Household.id).order_by(Household.id).all()]
    if len(ids) == 1:
        return ids[0]
    if not ids:
        raise AmbiguousHousehold("no household exists to sync for")
    raise AmbiguousHousehold(
        f"{len(ids)} households exist and this job was not told which one to sync. "
        "Pass household_id explicitly, or loop over them — do not let it default."
    )
