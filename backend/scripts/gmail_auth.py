"""One-time Gmail authorization (READ-ONLY).

Prereq: create an OAuth 2.0 Client (Desktop app) in Google Cloud Console, download the JSON,
and save it to backend/secrets/gmail_credentials.json (gitignored).

Run from backend/:   python scripts/gmail_auth.py
It opens a browser, you consent to read-only Gmail access, and a token is saved to
secrets/gmail_token.json. After that, POST /api/gmail/sync pulls statements automatically.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings  # noqa: E402
from services.gmail_sync import SCOPES  # noqa: E402


def main() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: PLC0415

    creds_path = settings.gmail_credentials_path
    if not os.path.exists(creds_path):
        print(f"Missing {creds_path}.")
        print("Create an OAuth Desktop client in Google Cloud Console → download JSON → save it there.")
        raise SystemExit(1)

    os.makedirs(os.path.dirname(settings.gmail_token_path), exist_ok=True)
    flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
    creds = flow.run_local_server(port=0)
    with open(settings.gmail_token_path, "w") as f:
        f.write(creds.to_json())
    print(f"Authorized. Token saved to {settings.gmail_token_path}.")
    print("Now use the 'Sync from Gmail' button (POST /api/gmail/sync).")


if __name__ == "__main__":
    main()
