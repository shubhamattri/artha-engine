from api.accounts_api import router as accounts_router
from api.afford import router as afford_router
from api.analytics import router as analytics_router
from api.annual import router as annual_router
from api.audit import router as audit_router
from api.auth import router as auth_router
from api.budget import router as budget_router
from api.cashflow import router as cashflow_router
from api.categories import router as categories_router
from api.cockpit import router as cockpit_router
from api.commitments import router as commitments_router
from api.debt import router as debt_router
from api.deps import get_current_user
from api.gmail import router as gmail_router
from api.insurance import router as insurance_router
from api.meta import router as meta_router
from api.networth import router as networth_router
from api.profile import router as profile_router
from api.recurring import router as recurring_router
from api.sinking import router as sinking_router
from api.surplus import router as surplus_router
from api.transactions import router as txn_router
from api.windfall import router as windfall_router
from api.admin import router as admin_router
from config import settings
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Artha API", version="1.0.0")

# Credentialed CORS requires explicit origins (never "*").
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# The permission gate. Mounted on every router below EXCEPT auth, whose routes all pre-date a
# session (login, logout, forgot, reset) or act only on the caller (/me, change-password) and are
# listed in PUBLIC / SELF_SERVICE. The gate resolves the user itself, so it can sit on the router
# carrying both /api/config (public) and /api/members (not).
from auth.permissions import enforce_permission  # noqa: E402

_gate = [Depends(enforce_permission)]

# Public: login / logout / forgot / reset (auth router guards /me itself).
app.include_router(auth_router, prefix="/api")
# Public /config + self-guarded /members (the latter requires a session via its household dep).
app.include_router(meta_router, prefix="/api", dependencies=_gate)

# Protected: every data router requires a valid session.
_auth = [Depends(get_current_user), Depends(enforce_permission)]
app.include_router(txn_router, prefix="/api", dependencies=_auth)
app.include_router(analytics_router, prefix="/api", dependencies=_auth)
app.include_router(annual_router, prefix="/api", dependencies=_auth)
app.include_router(debt_router, prefix="/api", dependencies=_auth)
app.include_router(budget_router, prefix="/api", dependencies=_auth)
app.include_router(categories_router, prefix="/api", dependencies=_auth)
app.include_router(cashflow_router, prefix="/api", dependencies=_auth)
app.include_router(networth_router, prefix="/api", dependencies=_auth)
app.include_router(recurring_router, prefix="/api", dependencies=_auth)
app.include_router(sinking_router, prefix="/api", dependencies=_auth)
app.include_router(commitments_router, prefix="/api", dependencies=_auth)
app.include_router(gmail_router, prefix="/api", dependencies=_auth)
app.include_router(insurance_router, prefix="/api", dependencies=_auth)
app.include_router(cockpit_router, prefix="/api", dependencies=_auth)
app.include_router(afford_router, prefix="/api", dependencies=_auth)
app.include_router(audit_router, prefix="/api", dependencies=_auth)
app.include_router(accounts_router, prefix="/api", dependencies=_auth)
app.include_router(profile_router, prefix="/api", dependencies=_auth)
app.include_router(surplus_router, prefix="/api", dependencies=_auth)
app.include_router(windfall_router, prefix="/api", dependencies=_auth)
app.include_router(admin_router, prefix="/api", dependencies=_auth)


# NOT /api/health: cockpit_router already registers GET /health under the same /api prefix, and
# FastAPI matches in registration order, so the routers won and this was unreachable. Any uptime
# probe pointed at /api/health was hitting the financial-health endpoint and getting 401.
@app.get("/api/liveness")
def liveness():
    from database import check_db

    try:
        check_db()
        return {"status": "ok", "db": "connected"}
    except Exception as e:
        return {"status": "ok", "db": f"error: {e}"}


# ── Serve the built frontend from this same process (production / server) ──
# In dev the UI runs under Vite on :5173 and this block is inert (no dist/ yet).
# On the server we `npm run build` → frontend/dist, and one uvicorn serves both API and UI.
from pathlib import Path  # noqa: E402

from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if (_DIST / "index.html").is_file():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    # The shell + service worker must NEVER be cached by the browser, or a stale index.html keeps
    # loading old (deleted) JS hashes → blank/old UI. Hashed /assets are safe to cache forever.
    _NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"}

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        # /api/* is matched by the routers above (registered first); never shadow it.
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = _DIST / full_path
        if full_path and candidate.is_file():  # real static file (sw.js, manifest, icons, favicon)
            # sw.js must always revalidate so the kill-switch reaches stuck devices.
            headers = _NO_CACHE if full_path == "sw.js" else None
            return FileResponse(candidate, headers=headers)
        return FileResponse(_DIST / "index.html", headers=_NO_CACHE)  # SPA shell → always fresh


# Every route must declare its permission, and the app refuses to start if one does not.
#
# At module scope on purpose, not in a startup event: a misconfiguration should stop the import, so
# a bad deploy fails immediately and visibly rather than serving traffic while logging a warning.
# The alternative is a route that answers everyone because someone forgot, which is what happens in
# codebases where every guard is hand-called and nothing checks coverage.
from auth.startup_check import assert_every_route_declared  # noqa: E402

assert_every_route_declared(app)
