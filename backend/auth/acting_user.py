"""Who is making the current request, available to code far below the endpoint signature.

The audit log wants a user id. The twelve places that write audit rows include `services/dedup.py`
and `services/merchant_rules.py`, which are three or four calls below any request handler and take
no user today. Threading a `user_id` argument through all of them would change signatures in code
that has nothing to do with identity, and would still miss the next writer someone adds.

So identity is ambient for the duration of one request, exactly like the database session: set once
by the gate that already resolves the user, read by whoever needs it.

**A contextvar is safe here specifically because Starlette handles each request in its own task.**
A task starts from a COPY of the context, so a value set inside one request is invisible to every
other, and sync endpoints run through `run_in_threadpool`, which copies the context across too. Set
it in a module-level global instead and two simultaneous requests would attribute each other's
edits.

This is NOT an authorisation input. Nothing may read it to decide whether an action is allowed —
that answer comes from `enforce_permission` and the registry, per route. This only records who.
"""

from __future__ import annotations

from contextvars import ContextVar

_acting_user_id: ContextVar[int | None] = ContextVar("acting_user_id", default=None)


def set_acting_user(user_id: int | None) -> None:
    """Called once per request by the permission gate. Not for application code."""
    _acting_user_id.set(user_id)


def acting_user_id() -> int | None:
    """The signed-in user's id, or None outside a request (a scheduled sync, a test, a script).

    None is a real answer and rows keep it. A background sync genuinely has no person behind it,
    and inventing one would put a name on a change nobody made.
    """
    return _acting_user_id.get()
