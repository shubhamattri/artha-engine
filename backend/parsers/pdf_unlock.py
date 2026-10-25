"""Find the password that opens an encrypted statement PDF.

Banks like HDFC/Axis email password-protected PDFs. pdfplumber (which the parsers use)
opens encrypted PDFs natively when given the right password — so rather than re-saving a
decrypted copy, we just find which configured password works and pass it through to the
parsers and the detector. Passwords come from settings.pdf_passwords (gitignored .env) and
are never logged, stored, or returned anywhere else.
"""

from __future__ import annotations


def find_password(path: str, passwords: list[str], last4: str | None = None,
                  db=None, household_id: int | None = None) -> str | None:
    """Return the password that opens this PDF:
      - "" if it's not encrypted (or empty password works),
      - the working password if encrypted,
      - None if encrypted and none of the configured passwords work (or it's unreadable).
    """
    try:
        import pdfplumber  # noqa: PLC0415
    except Exception:
        return ""
    # Configured statics first (cheap, known-good), then anything derivable from the household
    # profile. A wrong candidate simply fails to open the file, so a longer list costs milliseconds.
    derived: list[str] = []
    try:
        # Member identity first (collected at onboarding, per holder), then any .env profile.
        if db is not None:
            from parsers.pdf_password_rules import candidates_for_card
            derived += candidates_for_card(db, last4, household_id)
        from parsers.pdf_password_rules import candidates_from_settings
        derived += [c for c in candidates_from_settings(last4) if c not in derived]
    except Exception:  # noqa: BLE001
        pass
    for pwd in ["", *passwords, *derived]:
        try:
            with pdfplumber.open(path, password=pwd) as pdf:
                _ = pdf.pages  # force the password check / parse
            return pwd
        except Exception:
            continue
    return None
