"""Passwords are derived from the household's own details, not stored per card.

Each assertion below is a rule quoted verbatim from a statement email, using that email's own worked
example — so the test fails if we ever drift from what the bank actually says.
"""

from parsers.pdf_password_rules import candidates


def test_kotak_worked_example_from_its_own_email():
    """Kotak: "If your name is Raj Bhargava and date of birth is 28-08-1992 your password is rajb2808"."""
    assert "rajb2808" in candidates(name="Raj Bhargava", dob="1992-08-28")


def test_sbi_worked_example_from_its_own_email():
    """SBI: "last five digits of registered mobile number and DOB in DDMMYY format"."""
    got = candidates(name="Raj Bhargava", dob="1982-09-16", mobile="9876512345")
    assert "12345160982" in got


def test_cdsl_pan_in_capitals():
    """CDSL eCAS: "The password is PAN (in capital) of the first holder"."""
    assert "ABCDE1234F" in candidates(pan="abcde1234f")


def test_cams_pincode_fallback():
    """CAMS: "first 4 letters of Name + last four letters of your PINCODE"."""
    assert "RAJB0001" in candidates(name="Raj Bhargava", pincode="560001")


def test_date_is_accepted_in_the_formats_people_actually_type():
    for fmt in ("1992-08-28", "28-08-1992", "28/08/1992", "28081992"):
        assert "rajb2808" in candidates(name="Raj Bhargava", dob=fmt), fmt


def test_a_short_first_name_still_yields_the_whole_name_reading():
    """"Raj Bhargava" -> "rajb" needs the spaces-removed reading; the first word alone is only 3."""
    got = candidates(name="Raj Bhargava", dob="1992-08-28")
    assert "rajb2808" in got
    assert not any(c.startswith("raj2") for c in got)   # 3-letter stub is not a valid candidate


def test_no_profile_means_no_candidates():
    """Behaviour must be identical to before this module existed when nothing is configured."""
    assert candidates() == []
    assert candidates(name="", dob="", mobile="", pan="", pincode="") == []


def test_garbage_date_does_not_explode():
    assert candidates(name="Raj Bhargava", dob="not a date") == []


def test_candidates_are_unique():
    got = candidates(name="Raj Bhargava", dob="1992-08-28", mobile="9876512345",
                     pan="ABCDE1234F", pincode="560001", last4="9911")
    assert len(got) == len(set(got))
