"""Application configuration, loaded from environment / .env.

No credentials are hardcoded. Local-dev defaults cover host/port/db name and the
throwaway Docker username; the password and any production URL come from the
environment. Set DB_URL to override the constructed URL entirely.
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database connection parts (override any of these via env / .env).
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "artha"
    db_user: str = "artha"          # local Docker username; not a secret
    db_password: str = ""            # supplied via .env (see .env.example)
    # Set DB_URL in the environment to bypass the parts above entirely.
    db_url_override: str = Field(default="", validation_alias="DB_URL")


    # ── Product/brand (NO hardcoded brand or currency in code — surfaced via /api/config). ──
    brand_name: str = "Artha"            # the product name (white-labelable per deploy)
    brand_wordmark: str = "अर्थ"          # Devanagari signature mark
    currency: str = "INR"
    currency_symbol: str = "₹"
    locale: str = "en-IN"
    # Household billing/budget cycle anchor day (the first household's runs 12th→12th). Every "this month"
    # calibrates to this, not the calendar month. Per-household later; one knob for now.
    cycle_start_day: int = 12
    # ONE household cycle window (cycle_start_day → same day next month) for EVERY instrument. This keeps
    # every screen (Plan / Analytics / Cockpit / Spending) on the identical window so their totals always
    # agree. Set per_card_cycles=True to instead bill each credit card on its own statement day
    # (card_cycle_days below) — more statement-accurate, but Plan and Analytics then window past cycles
    # differently and their totals diverge. Default OFF (unified window) per the household's choice.
    per_card_cycles: bool = False
    # Each CREDIT CARD's own billing day, used ONLY when per_card_cycles is True. Derived from statements
    # (ICICI 12th; HDFC/Axis/Kotak 22nd). Keyed by bank substring; default covers any other credit card.
    card_cycle_days: dict[str, int] = {"icici": 12, "hdfc": 22, "axis": 22, "kotak": 22}
    card_cycle_default: int = 22
    # Cards/accounts that are NOT household money (e.g. a corporate card, or a relative's bank account
    # whose alerts land in the same inbox). Transactions on these last-4s are never ingested and never
    # counted. e.g. "1234" = an employer card; "5678" = a relative's account whose alerts copy here.
    # Cards that reach this mailbox but are NOT this household's: an employer card, a parent's card
    # on a shared email. Excluding one is data (register it under Settings, set inactive) so no real
    # card number belongs in source. This stays as an escape hatch for a household that has not
    # registered anything yet — an EMPTY default is correct for a product, but the exclusion must
    # then be carried by the accounts table, and BOTH are checked. Emptying this without migrating
    # the values into the table is what let an office card's spend into the ledger.
    excluded_card_last4: list[str] = []
    # ── Gold-loan collateral (house-buying plan). LTV a lender advances against gold melt value; and any
    #    gold already pledged OUTSIDE Artha — e.g. a parent's existing gold loan that isn't (and shouldn't
    #    be) a tracked household Loan because its EMI isn't your FOIR. Free gold + capacity are then
    #    COMPUTED live: free = total gold assets − (active tracked 'Gold Loan' balances ÷ LTV) − external.
    #    Mark a tracked gold loan paid → it leaves the active set → free gold rises automatically.
    gold_ltv: float = 0.75
    # Indian physical gold trades ~16% above international COMEX spot (import duty + 3% GST + local
    # premium). Scale the spot rate by this to value gold at the INDIAN market price (set 1.0 for raw melt).
    gold_india_premium: float = 1.16
    # Fallback pledge held outside Artha with NO loan record. Prefer adding the loan in the Debt section
    # as a 'Gold Loan' flagged foir_exempt (then it self-corrects on payoff); this stays 0 to avoid
    # double-counting once that loan exists.
    gold_external_pledged_inr: float = 0.0
    # ── Planning assumptions — MARKET and LENDER numbers the engines reason with. They are guesses
    #    about the world, not household facts, so they go stale on a rate cycle or with a new lender.
    home_loan_rate: float = 0.0875   # assumption: the home-loan rate p.a. this household would be quoted today
    home_loan_years: int = 20        # assumption: the tenure a lender grants — with the rate it fixes EMI per rupee
    equity_return: float = 0.11      # assumption: long-run equity return; decides prepay-the-loan vs stay-invested
    home_ltv: float = 0.80           # assumption: the share of a property's price a lender will finance
    foir_cap: float = 0.50           # assumption: the share of income lenders let TOTAL EMIs reach

    # ── Spend-projection tuning — BEHAVIOURAL, fitted on one household's backtest (services/projection.py).
    #    A household that spends in a different shape should refit these rather than inherit them.
    lump_floor_inr: float = 2_000.0  # assumption: below this nothing counts as a one-off, whatever the category
    lump_multiple: float = 3.0       # assumption: a one-off stands this many times above the category's normal txn
    surprise_damp: float = 0.5       # assumption: how much of the historical lump rate is still to land this cycle
    projection_warmup_days: int = 5  # assumption: days into a cycle before its daily rate is worth trusting

    # ── De-duplication thresholds — assumptions about how noisily the SAME debit arrives twice. Banks
    #    differ in how far apart, and how differently dated, their two captures of one debit land.
    dedup_date_tol_days: int = 3     # assumption: how far two channels' dates for one debit can drift apart
    dedup_ts_tol_sec: int = 900      # assumption: how far two captures' timestamps drift for one debit

    # Messages DBs to read bank SMS from. Empty = the running user's ~/Library/Messages/chat.db.
    # For two spouses on two Apple IDs, add the 2nd macOS user's chat.db path (comma-separated).
    chat_db_paths: str = ""

    @property
    def chat_dbs(self) -> list[str]:
        import os as _os
        if self.chat_db_paths.strip():
            return [p.strip() for p in self.chat_db_paths.split(",") if p.strip()]
        return [_os.path.expanduser("~/Library/Messages/chat.db")]

    # ── Auth ─────────────────────────────────────────────────
    # JWT signing secret. MUST be set via env in any non-local deploy.
    jwt_secret: str = "dev-only-change-me"
    jwt_ttl_hours: int = 168                     # session length (7 days)
    reset_token_ttl_minutes: int = 30            # password-reset link validity
    invite_token_ttl_hours: int = 48             # first-time set-password link validity
    cookie_secure: bool = False                  # True behind HTTPS
    cookie_name: str = "artha_session"

    # Frontend origin(s) allowed by CORS (comma-separated). Credentials require
    # explicit origins — never "*".
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Base URL the password-reset link points at (the frontend).
    frontend_url: str = "http://localhost:5173"

    # ── AI layer (optional). Key in .env ONLY, never committed. If unset, every AI feature
    #    falls back to the deterministic/rules path and the app works exactly as before. ──
    anthropic_api_key: str = ""
    llm_model_fast: str = "claude-haiku-4-5-20251001"   # categorization (high volume, cheap)
    llm_model_smart: str = "claude-opus-4-8"            # Ask Artha / advice / explanations — the reasoning tier
    # Grounded advisor (Slice 2): False (default) → /advisor uses the legacy prompt-based path
    # byte-for-byte; True → routes through the tool-calling advisor_agent. Flip via env
    # ADVISOR_GROUNDED=true only after the P0 eval gate is green — lets us shadow-run first.
    advisor_grounded: bool = False

    # ── Email (password reset). If unset, links are logged for dev. ──
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "Artha <no-reply@artha.local>"

    # ── Passwords to auto-unlock encrypted statement PDFs (e.g. HDFC). Comma-separated.
    # Supplied via .env (gitignored) ONLY — never committed, never logged. ──
    statement_pdf_passwords: str = ""
    # Profile used to DERIVE statement passwords (see parsers/pdf_password_rules). Banks publish the
    # rule in the statement email itself and every rule is a function of these, so deriving beats
    # maintaining a list that needs a human edit per card. Same gitignored .env as the passwords above.
    statement_holder_name: str = ""
    statement_dob: str = ""          # YYYY-MM-DD or DD-MM-YYYY
    statement_mobile: str = ""
    statement_pan: str = ""
    statement_pincode: str = ""

    # ── Gmail auto-pull (read-only). OAuth files are gitignored; created via scripts/gmail_auth.py.
    gmail_credentials_path: str = "secrets/gmail_credentials.json"   # OAuth client (from Google Cloud)
    gmail_token_path: str = "secrets/gmail_token.json"               # saved after first authorization
    # Gmail search that selects statement emails. Tune sender/subject/day per your banks.
    gmail_query: str = ("has:attachment newer_than:45d "
                        "(subject:statement OR subject:e-statement OR from:estatement OR from:statements)")

    @property
    def pdf_passwords(self) -> list[str]:
        return [p.strip() for p in self.statement_pdf_passwords.split(",") if p.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def db_url(self) -> str:
        if self.db_url_override:
            return self.db_url_override
        auth = self.db_user
        if self.db_password:
            auth = f"{self.db_user}:{self.db_password}"
        return f"postgresql+psycopg2://{auth}@{self.db_host}:{self.db_port}/{self.db_name}"


settings = Settings()
