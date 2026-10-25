"""Route -> permission, declared in one table and asserted at boot.

Why a central registry rather than a decorator on each route: a decorator can be omitted and nothing
notices. A registry can be diffed against the router at startup, so an omission is a crash. That one
property is the whole difference between this and a codebase where every guard is hand-called and
nothing checks coverage, which is how routes end up open by accident.

The table below was GENERATED from the live router rather than typed, and the tests in
tests/test_default_deny.py check it in both directions: every route declared, and every declaration
still naming a live route.
"""

from dataclasses import dataclass

from api.deps import get_optional_user
from auth.acting_user import set_acting_user
from auth.roles import Level, Role
from fastapi import Depends, HTTPException, Request

# The twelve keys. Adding one is a deliberate act; the catalogue is meant to stay small enough that
# a person configuring a family member can hold it in their head.
KEYS = (
    "spending", "ledger.import", "ledger.housekeeping", "budget", "categories",
    "plan.lines", "debt", "wealth", "commitments", "windfall", "household.admin", "advisor",
)


@dataclass(frozen=True)
class PermissionRule:
    key: str
    level: Level

    def __post_init__(self) -> None:
        if self.key not in KEYS:
            raise ValueError(f"unknown permission key {self.key!r}; known keys are {KEYS}")


def rule(key: str, level: Level) -> PermissionRule:
    return PermissionRule(key, level)


# No session at all. Each entry states why, because in a year the reason is the only thing that lets
# someone judge whether it is still true.
PUBLIC: dict[tuple[str, str], str] = {
    ("POST", "/api/auth/login"): "no session exists yet",
    ("POST", "/api/auth/logout"): "clears a cookie; nothing read",
    ("POST", "/api/auth/forgot-password"): "identified by email, pre-session",
    ("POST", "/api/auth/reset-password"): "identified by token, pre-session",
    ("GET", "/api/config"): "brand, currency, locale. The login screen renders it before a session",
    ("GET", "/api/liveness"): "process healthcheck, no data",
}

# A session, but no permission: a person always manages their own login and their own details. Kept
# separate from REGISTRY so "always allowed" is visibly a different category from "allowed if
# granted", and so nobody can quietly move one into the other.
SELF_SERVICE: dict[tuple[str, str], str] = {
    ("GET", "/api/auth/me"): "the caller's own identity",
    ("POST", "/api/auth/change-password"): "own password, no target parameter",
    ("GET", "/api/profile"): "own profile",
    ("PATCH", "/api/profile"): "own profile, no target parameter",
    ("GET", "/api/profile/household"): "the roster a person needs to see who else is here",
    # Was on the PUBLIC list, which claimed no session was needed. Untrue: its router carries
    # get_current_user, so it has always answered 401 signed out. Nothing depended on the claim —
    # the only caller is the upload screen — but a declaration that does not match what the route
    # does is worse than no declaration, because the next person reads it instead of the code.
    ("GET", "/api/supported"): "which file formats parse; no household data in the answer",
}

# GET -> view, everything else -> edit. The one exception is POST /api/afford, which simulates an
# affordability question and writes nothing; tests/test_default_deny.py names it explicitly so the
# exception is stated rather than tolerated.
# Guarded by a ROLE rather than a permission key. Managing people is deliberately not grantable — a
# permission that can hand out permissions is one that escalates itself — so these routes carry
# `require_super_admin` instead, and the gate steps aside for them.
#
# A fourth category rather than a quiet exemption, because every route has to be in exactly one of
# these four and the reason has to be readable.
ROLE_GATED: dict[tuple[str, str], str] = {
    ("GET", "/api/admin/users"): "super admin only, via require_super_admin",
    ("PATCH", "/api/admin/users/{user_id}/role"): "super admin only, via require_super_admin",
    ("PATCH", "/api/admin/users/{user_id}/permissions"): "super admin only, via require_super_admin",
    ("POST", "/api/admin/users/{user_id}/deactivate"): "super admin only, via require_super_admin",
}

REGISTRY: dict[tuple[str, PermissionRule]] = {
    ("GET", "/api/accounts"): rule("ledger.import", Level.view),
    ("POST", "/api/accounts"): rule("ledger.import", Level.edit),
    ("GET", "/api/accounts/statement-schedule/discover"): rule("ledger.import", Level.view),
    ("GET", "/api/accounts/unknown-senders"): rule("ledger.import", Level.view),
    ("DELETE", "/api/accounts/{account_id}"): rule("ledger.import", Level.edit),
    ("PATCH", "/api/accounts/{account_id}"): rule("ledger.import", Level.edit),
    ("PATCH", "/api/accounts/{account_id}/statement-schedule"): rule("ledger.import", Level.edit),
    ("POST", "/api/advisor"): rule("advisor", Level.edit),
    ("POST", "/api/afford"): rule("spending", Level.view),
    ("GET", "/api/analytics/category-matrix"): rule("spending", Level.view),
    ("GET", "/api/analytics/cycle-matrix"): rule("spending", Level.view),
    ("GET", "/api/analytics/recommendations"): rule("spending", Level.view),
    ("GET", "/api/analytics/trend"): rule("spending", Level.view),
    ("GET", "/api/annual"): rule("spending", Level.view),
    ("POST", "/api/assets/{asset_id}/dispose"): rule("wealth", Level.edit),
    ("GET", "/api/audit/corrections"): rule("ledger.housekeeping", Level.view),
    ("GET", "/api/audit/history"): rule("ledger.housekeeping", Level.view),
    ("POST", "/api/audit/undo/{event_id}"): rule("ledger.housekeeping", Level.edit),
    ("GET", "/api/budget"): rule("budget", Level.view),
    ("POST", "/api/budget"): rule("budget", Level.edit),
    ("GET", "/api/budget/category-weeks"): rule("budget", Level.view),
    ("GET", "/api/cashflow"): rule("plan.lines", Level.view),
    ("POST", "/api/cashflow"): rule("plan.lines", Level.edit),
    ("GET", "/api/cashflow/summary"): rule("plan.lines", Level.view),
    ("DELETE", "/api/cashflow/{line_id}"): rule("plan.lines", Level.edit),
    ("PUT", "/api/cashflow/{line_id}"): rule("plan.lines", Level.edit),
    ("POST", "/api/categories"): rule("categories", Level.edit),
    ("GET", "/api/categories/all"): rule("categories", Level.view),
    ("PATCH", "/api/categories/by-name/{name}/properties"): rule("categories", Level.edit),
    ("POST", "/api/categories/order"): rule("categories", Level.edit),
    ("DELETE", "/api/categories/{cat_id}"): rule("categories", Level.edit),
    ("PATCH", "/api/categories/{cat_id}"): rule("categories", Level.edit),
    ("POST", "/api/categories/{cat_id}/merge"): rule("categories", Level.edit),
    ("GET", "/api/categories/{cat_id}/rules"): rule("categories", Level.view),
    ("POST", "/api/categories/{cat_id}/split"): rule("categories", Level.edit),
    ("GET", "/api/cockpit"): rule("spending", Level.view),
    ("GET", "/api/commitments"): rule("commitments", Level.view),
    ("GET", "/api/commitments/candidates"): rule("commitments", Level.view),
    ("POST", "/api/commitments/link"): rule("commitments", Level.edit),
    ("GET", "/api/commitments/reconcile"): rule("commitments", Level.view),
    ("POST", "/api/commitments/unlink"): rule("commitments", Level.edit),
    ("GET", "/api/consistency"): rule("household.admin", Level.view),
    ("GET", "/api/debt"): rule("debt", Level.view),
    ("POST", "/api/debt"): rule("debt", Level.edit),
    ("GET", "/api/debt/move-narration"): rule("debt", Level.view),
    ("DELETE", "/api/debt/{loan_id}"): rule("debt", Level.edit),
    ("GET", "/api/debt/{loan_id}"): rule("debt", Level.view),
    ("PUT", "/api/debt/{loan_id}"): rule("debt", Level.edit),
    ("POST", "/api/debt/{loan_id}/disburse"): rule("debt", Level.edit),
    ("DELETE", "/api/debt/{loan_id}/disburse/{index}"): rule("debt", Level.edit),
    ("POST", "/api/debt/{loan_id}/payments"): rule("debt", Level.edit),
    ("DELETE", "/api/debt/{loan_id}/payments/{payment_id}"): rule("debt", Level.edit),
    ("POST", "/api/debt/{loan_id}/preclose"): rule("debt", Level.edit),
    ("POST", "/api/debt/{loan_id}/skip"): rule("debt", Level.edit),
    ("GET", "/api/disposals"): rule("wealth", Level.view),
    ("DELETE", "/api/disposals/{disposal_id}"): rule("wealth", Level.edit),
    ("PATCH", "/api/disposals/{disposal_id}"): rule("wealth", Level.edit),
    ("POST", "/api/disposals/{disposal_id}/receipts"): rule("wealth", Level.edit),
    ("DELETE", "/api/disposals/{disposal_id}/receipts/{receipt_id}"): rule("wealth", Level.edit),
    ("GET", "/api/gmail/status"): rule("ledger.import", Level.view),
    ("POST", "/api/gmail/sync"): rule("ledger.import", Level.edit),
    ("GET", "/api/health"): rule("spending", Level.view),
    ("GET", "/api/household/cycle"): rule("household.admin", Level.view),
    ("PUT", "/api/household/cycle"): rule("household.admin", Level.edit),
    ("GET", "/api/household/settings"): rule("household.admin", Level.view),
    ("PUT", "/api/household/settings"): rule("household.admin", Level.edit),
    ("GET", "/api/insights"): rule("spending", Level.view),
    ("GET", "/api/insurance"): rule("wealth", Level.view),
    ("POST", "/api/insurance"): rule("wealth", Level.edit),
    ("DELETE", "/api/insurance/{pid}"): rule("wealth", Level.edit),
    ("PUT", "/api/insurance/{pid}"): rule("wealth", Level.edit),
    ("GET", "/api/members"): rule("household.admin", Level.view),
    ("PATCH", "/api/members/{member_id}/identity"): rule("household.admin", Level.edit),
    ("GET", "/api/networth"): rule("wealth", Level.view),
    ("POST", "/api/networth"): rule("wealth", Level.edit),
    ("GET", "/api/networth/composition"): rule("wealth", Level.view),
    ("GET", "/api/networth/summary"): rule("wealth", Level.view),
    ("DELETE", "/api/networth/{asset_id}"): rule("wealth", Level.edit),
    ("PUT", "/api/networth/{asset_id}"): rule("wealth", Level.edit),
    ("GET", "/api/reconcile/duplicates"): rule("ledger.housekeeping", Level.view),
    ("POST", "/api/reconcile/duplicates"): rule("ledger.housekeeping", Level.edit),
    ("POST", "/api/reconcile/duplicates/undo"): rule("ledger.housekeeping", Level.edit),
    ("GET", "/api/recurring"): rule("wealth", Level.view),
    ("POST", "/api/recurring"): rule("wealth", Level.edit),
    ("GET", "/api/recurring/schemes"): rule("wealth", Level.view),
    ("DELETE", "/api/recurring/{inv_id}"): rule("wealth", Level.edit),
    ("PUT", "/api/recurring/{inv_id}"): rule("wealth", Level.edit),
    ("POST", "/api/recurring/{inv_id}/contribute"): rule("wealth", Level.edit),
    ("DELETE", "/api/recurring/{inv_id}/contribute/{index}"): rule("wealth", Level.edit),
    ("POST", "/api/recurring/{inv_id}/pause"): rule("wealth", Level.edit),
    ("POST", "/api/recurring/{inv_id}/skip"): rule("wealth", Level.edit),
    ("GET", "/api/sinking"): rule("plan.lines", Level.view),
    ("POST", "/api/sinking"): rule("plan.lines", Level.edit),
    ("DELETE", "/api/sinking/{fund_id}"): rule("plan.lines", Level.edit),
    ("PUT", "/api/sinking/{fund_id}"): rule("plan.lines", Level.edit),
    ("POST", "/api/sinking/{fund_id}/withdraw"): rule("plan.lines", Level.edit),
    ("GET", "/api/transactions"): rule("spending", Level.view),
    ("GET", "/api/transactions/categories"): rule("spending", Level.view),
    ("POST", "/api/transactions/categorize-ai"): rule("ledger.housekeeping", Level.edit),
    ("GET", "/api/transactions/category-consistency"): rule("ledger.housekeeping", Level.view),
    ("GET", "/api/transactions/duplicate-review"): rule("ledger.housekeeping", Level.view),
    ("POST", "/api/transactions/duplicate-review/dismiss"): rule("ledger.housekeeping", Level.edit),
    ("POST", "/api/transactions/duplicate-review/merge"): rule("ledger.housekeeping", Level.edit),
    ("GET", "/api/transactions/facets"): rule("spending", Level.view),
    ("POST", "/api/transactions/merchant-category"): rule("ledger.housekeeping", Level.edit),
    ("POST", "/api/transactions/normalize-banks"): rule("ledger.housekeeping", Level.edit),
    ("POST", "/api/transactions/normalize-merchants"): rule("ledger.housekeeping", Level.edit),
    ("POST", "/api/transactions/quick"): rule("spending", Level.edit),
    ("POST", "/api/transactions/recategorize"): rule("ledger.housekeeping", Level.edit),
    ("POST", "/api/transactions/reconcile"): rule("ledger.housekeeping", Level.edit),
    ("POST", "/api/transactions/sync"): rule("ledger.import", Level.edit),
    ("POST", "/api/transactions/sync-gmail-alerts"): rule("ledger.import", Level.edit),
    ("POST", "/api/transactions/sync-sms"): rule("ledger.import", Level.edit),
    ("DELETE", "/api/transactions/{txn_id}"): rule("spending", Level.edit),
    ("PATCH", "/api/transactions/{txn_id}"): rule("spending", Level.edit),
    ("POST", "/api/upload"): rule("ledger.import", Level.edit),
    ("GET", "/api/wallet"): rule("plan.lines", Level.view),
    ("POST", "/api/wallet/spend"): rule("plan.lines", Level.edit),
    ("GET", "/api/windfall"): rule("windfall", Level.view),
    ("DELETE", "/api/windfall/measured-return"): rule("windfall", Level.edit),
    ("GET", "/api/windfall/measured-return"): rule("windfall", Level.view),
    ("PUT", "/api/windfall/measured-return"): rule("windfall", Level.edit),
}

# Mutating routes that legitimately need only `view`. One entry, and it has to earn its place.
READ_ONLY_POSTS = {("POST", "/api/afford")}


def resolve(role: Role, grants: dict[str, Level]) -> dict[str, Level]:
    """One dict, every key present, so no caller ever handles a missing key.

    An elevated role is `edit` everywhere and ignores grants entirely — a stale `none` row must not
    lock an admin out of their own household, and a stale `view` row must not be read as a demotion.
    A member is exactly what they were granted, and the default is `none`: a new person sees nothing
    until someone decides otherwise, and that decision is a visible row rather than an absence.

    Managing PEOPLE is deliberately not a key here. It is `Role.manages_users`, checked on the two
    admin routes, so it cannot drift into something grantable.
    """
    if role.is_elevated:
        return {k: Level.edit for k in KEYS}
    return {k: grants.get(k, Level.none) for k in KEYS}


def user_grants(user) -> dict[str, Level]:
    """This user's grants as a {key: Level} map, from rows already loaded by the selectin
    relationship — so resolving permissions costs no extra query."""
    out: dict[str, Level] = {}
    for row in getattr(user, "permissions", None) or []:
        try:
            out[row.key] = Level(row.level)
        except ValueError:
            # A level this code does not recognise is a corrupt row, not a reason to grant
            # something. Skipping leaves the key at its default of `none`.
            continue
    return out


# Route identity -> {method: PermissionRule | EXEMPT}, filled once at boot by
# auth.startup_check._stamp_rules. Keyed by id() of the route object because that is the one thing
# the request path is handed directly.
EXEMPT = object()
_rules_by_route: dict[int, dict[str, object]] = {}


async def enforce_permission(request: Request, user=Depends(get_optional_user)) -> None:
    """The gate. One dependency, mounted on every router, checked against the registry.

    It resolves the user ITSELF rather than taking `Depends(get_current_user)`, because one gate has
    to cover public routes too. `/api/config` and `/api/members` sit on the same router: the first
    must answer before a session exists, the second must not. A gate that demanded a user up front
    would 401 the login screen's branding; one that skipped that router entirely would leave the
    second ungated.

    Runs BEFORE the endpoint body, so a denied caller never reaches a database lookup and cannot
    tell a missing row from a forbidden one by reading 404-versus-403 off the response.

    **`async` on purpose, and it must stay that way.** FastAPI runs a SYNC dependency in a worker
    thread, and a thread gets a COPY of the context — so `set_acting_user` inside a sync gate would
    write into a copy that is discarded the moment the dependency returns, and every audit row would
    silently record nobody. As a coroutine it runs in the request's own task, and the endpoint (async
    in the same task, or sync in a threadpool that copies FROM it) sees the value. Nothing here
    blocks, so there is no cost to it.

    An unknown route denies rather than allows. The boot assertion should make that unreachable, but
    a guard whose fallback is "allow" is one refactor away from being the hole it was written to
    close.
    """
    # Stamped before any early return, so an audit row written by a self-service or role-gated
    # route still names the person. Recording who acted is not the same decision as whether they
    # may, and this line makes none of the latter.
    set_acting_user(getattr(user, "id", None))

    route = request.scope.get("route")
    required = _rules_by_route.get(id(route), {}).get(request.method) if route else None

    if required is EXEMPT:      # public, self-service, or role-gated — no key to check here
        return
    if required is None:
        raise HTTPException(
            status_code=403,
            detail=("This endpoint has no permission declared, so it is denied. "
                    "Declare it in auth/permissions.py."),
        )

    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    role = Role(user.role)
    held = resolve(role, user_grants(user))[required.key]
    if held < required.level:
        raise HTTPException(
            status_code=403,
            detail=(f"You need {required.level.name} on {required.key} to do this. "
                    f"Ask whoever administers this household."),
        )

