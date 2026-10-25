"""The email that carries a locked statement also carries the key.

3 real card statements sat unopened while the app told the user to configure passwords the bank had
already written in the covering email. The fetcher returned only (filename, bytes) and dropped the
body.
"""

from services.statement_email import card_issuer, passwords_from_email, sender_of


def test_an_explicit_password_is_read_out_of_the_body():
    body = "Name Raj Bhargava Date of Birth 28-08-1992 Password abcd1234 Space after Raj is NOT typed"
    assert passwords_from_email(body) == ["abcd1234"]


def test_colon_and_is_forms_both_work():
    assert passwords_from_email("Password: xy991122") == ["xy991122"]
    assert passwords_from_email("your password is xy991122 to open") == ["xy991122"]


def test_a_rule_only_email_yields_nothing_here():
    """'Use your PAN in capital letters' is a rule, not a value. Deriving it belongs to
    pdf_password_rules, which already builds candidates from the household's identity fields.
    Restating that logic here would leave two implementations to keep in step."""
    assert passwords_from_email("The statement is password protected. Use your PAN in capitals.") == []


def test_prose_around_the_word_password_is_not_mistaken_for_one():
    for body in ("This document is password protected.",
                 "Your password-protected statement is attached.",
                 "password the same as before"):
        assert passwords_from_email(body) == [], body


def test_only_card_issuers_are_treated_as_card_statements():
    """The statement query also returns broker, depository and tax statements. Counting those as
    failures made the step look broken when it was working."""
    assert card_issuer("cc.statements@axis.bank.in") == ("Axis", "Credit Card")
    assert card_issuer("<credit_cards@icici.bank.in>") == ("ICICI", "Credit Card")
    assert card_issuer("Groww <noreply@groww.in>") is None
    assert card_issuer("eCAS@cdslstatement.com") is None
    assert card_issuer("Upstox <donotreply@transactions.upstox.com>") is None


def test_sender_is_extracted_from_a_display_name_header():
    assert sender_of("Kotak Bank <cardstatement@kotak.bank.in>") == "cardstatement@kotak.bank.in"
    assert sender_of("cc.statements@axis.bank.in") == "cc.statements@axis.bank.in"


def test_several_passwords_in_one_body_keep_their_order():
    assert passwords_from_email("Password aaaa1111 ... or Password bbbb2222") == ["aaaa1111", "bbbb2222"]


def test_the_fetcher_uses_the_configured_query_not_an_invented_constant():
    """A rewrite referenced STATEMENT_QUERY, which never existed. It imported fine and only failed
    when the function ran, so the sync reported an error string instead of importing anything."""
    import inspect

    from services import gmail_sync
    src = inspect.getsource(gmail_sync.fetch_statement_attachments)
    assert "settings.gmail_query" in src
    assert "STATEMENT_QUERY" not in src


def test_fetch_statement_attachments_accepts_the_context_flag():
    """The sync calls it with with_context=True. A signature change would fail only at runtime."""
    import inspect

    from services import gmail_sync
    params = inspect.signature(gmail_sync.fetch_statement_attachments).parameters
    assert "with_context" in params
