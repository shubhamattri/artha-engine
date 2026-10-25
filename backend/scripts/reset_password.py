#!/usr/bin/env python
"""Set any user's password directly. Run ON the server, where shell access IS the authentication.

Works for every account in the database, whichever household they belong to — the usernames are
discovered with --list, never assumed. Nothing here is specific to the first household.

Email-based reset can't work on a deployment with no SMTP host configured, or one reached over a
private URL rather than a public one. For a self-hosted app the honest recovery path is an operator
running a command on the box, not a mail round-trip.

    ./.venv/bin/python scripts/reset_password.py --list                  # who exists
    ./.venv/bin/python scripts/reset_password.py --user <username>       # prompt for a new one
    ./.venv/bin/python scripts/reset_password.py --user <username> --generate

The password is read from a prompt (never echoed, never in shell history). --generate instead prints
a memorable passphrase you can actually type on a phone ("copper-14-meadow-quartz-owl"); --random
prints a character string for a password manager.
"""

from __future__ import annotations

import argparse
import getpass
import os
import secrets
import string
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MIN_LEN = 8
_SYSRAND = secrets.SystemRandom()   # cryptographically secure, and gives us .sample()


# Short, common, unambiguous words — no homophones, no letters that read alike, nothing that needs a
# shift key. A passphrase built from these is typed easily on a phone keyboard, which matters because
# this app is used on phones. A 20-char symbol soup is strong and unusable; people write it on paper.
_WORDS = """
able acid acorn amber anchor angle ankle apple april arbor arrow aspen atlas autumn axis bacon badge
baker balance bamboo banjo barley basil basin beach beacon beam bean bear bell belt bench berry birch
bird bison black blade bloom blue board boat bolt bonus book boot brass brave bread brick bridge
brief bright bronze brook brown brush bubble bucket buffalo bunny cabin cable cactus camel candle
canoe canvas canyon cargo carpet carrot castle cedar cello chain chair chalk charm cheese cherry
chess chief chili china cider cinema circle citrus clay clever cliff cloud clover coach coal coast
cobra cocoa coffee coin comet compass copper coral cotton cougar county cover coyote crane crayon
cream creek crest cricket crown crystal cube curve cycle daisy dance dawn deer delta denim desert
diamond diesel dime dinner dolphin domino donkey dragon dream dress drift drum dune eagle earth east
echo eclipse elbow elder elk ember emerald engine equal ever fabric falcon farm feather fern ferry
fiber field fig finch fire flame flint float flower flute focus forest fox frame frost fruit galaxy
garden garlic gate gear ginger glacier glass globe glove gold goose grain granite grape grass gravel
green grove guitar hammer harbor harvest hawk hazel heart hedge helmet hero hickory hill hollow honey
hoodie horizon horse hotel ice indigo iris island ivory jacket jade jasmine jelly jewel judge juniper
kayak kettle key kite kiwi koala ladder lagoon lake lamp lantern laurel lava leaf lemon lentil level
lever light lilac lily lime linen lion lobby locket lotus lumber lunar lynx magnet mango maple marble
market marsh meadow melon mercy metal meteor midnight mint mirror mist model monkey moon moss motor
mountain mouse muffin mulberry museum music nectar needle nest noble north nutmeg oak oasis ocean
olive onion opal orange orbit orchard otter oyster paddle palm panda paper parrot pastel peach peak
pearl pebble pecan pepper perch petal piano pigeon pilot pine pink pixel planet plum pocket pond
poppy portal potato prairie prism puzzle quail quartz quiet quilt rabbit radar radish raft rain
rattle raven razor reef ribbon rice ridge rifle river robin rocket rose ruby rug saddle sage salmon
salt sand sapphire satin scarf school seal season shadow shell shore silk silver sketch sky slate
sleeve smoke snow socket solar sonnet spark sparrow spice spider spring spruce square squid stable
star steam steel stone stork storm stream studio sugar summer sunset swan sweater table talon tango
teal temple thistle thunder tiger timber toast tomato topaz torch tower trail train trout trumpet
tulip tundra turtle twilight umbrella unicorn valley vanilla velvet vine violet vision volcano
walnut walrus water wave weasel whale wheat wheel willow window winter wolf wonder wood wool yarn
yellow zebra zinnia
""".split()


def strong_password(words: int = 4) -> str:
    """A memorable passphrase: words + a 2-digit number, hyphen-separated.

    Words are drawn WITHOUT replacement — a repeat ("opal-bunny-...-bunny") both looks like a bug and
    costs entropy. With 448 distinct words, 4 of them and a 2-digit number that is ~2^42, against a
    bcrypt-cost-12 hash (~0.25s per guess), so an offline attack is far beyond practical. It is also
    far stronger than what someone actually picks when the generated option is unusable.
    """
    if words > len(_WORDS):
        raise ValueError("more words requested than the list holds")
    picks = list(_SYSRAND.sample(_WORDS, words))
    picks.insert(secrets.randbelow(len(picks) + 1), str(secrets.randbelow(90) + 10))
    return "-".join(picks)


def random_password(n: int = 20) -> str:
    """Old behaviour — a random character soup, for a password manager that will type it for you."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
    return "".join(secrets.choice(alphabet) for _ in range(n))


def main() -> int:
    ap = argparse.ArgumentParser(description="Set an Artha user's password.")
    ap.add_argument("--user", help="username to reset (see --list; not needed with --list)")
    ap.add_argument("--generate", action="store_true",
                    help="generate a memorable passphrase (typeable on a phone) and print it")
    ap.add_argument("--random", action="store_true",
                    help="generate a random character string instead (for a password manager)")
    ap.add_argument("--words", type=int, default=4, help="words in the passphrase (default 4)")
    ap.add_argument("--list", action="store_true", help="list usernames and exit")
    args = ap.parse_args()
    if not args.list and not args.user:
        ap.error("--user is required (or use --list to see the usernames)")

    import main as _app  # noqa: F401  — registers models/metadata
    from auth.security import hash_password
    from database import SessionLocal
    from models.user import User

    db = SessionLocal()
    try:
        if args.list:
            for u in db.query(User).all():
                print(f"  {u.username}  (active={u.is_active})")
            return 0

        user = db.query(User).filter(User.username == args.user).first()
        if user is None:
            print(f"No such user: {args.user!r}. Known users:", file=sys.stderr)
            for u in db.query(User).all():
                print(f"  {u.username}", file=sys.stderr)
            return 1

        if args.generate or args.random:
            pw = random_password() if args.random else strong_password(args.words)
            print(f"\n  new password for {user.username}: {pw}\n  (store it now — it is not recoverable)\n")
        else:
            pw = getpass.getpass(f"New password for {user.username}: ")
            if pw != getpass.getpass("Repeat: "):
                print("Passwords do not match.", file=sys.stderr)
                return 1

        if len(pw) < MIN_LEN:
            print(f"Password must be at least {MIN_LEN} characters.", file=sys.stderr)
            return 1

        user.password_hash = hash_password(pw)
        user.is_active = True          # a locked-out account is the usual reason for running this

        # Burn any outstanding reset tokens. Without this an unused, unexpired link minted before this
        # reset still works afterwards, so the password you just chose could be changed again by
        # whoever holds that link — which is the opposite of what someone running this expects.
        from datetime import datetime

        from models.user import PasswordResetToken
        killed = (db.query(PasswordResetToken)
                  .filter(PasswordResetToken.user_id == user.id,
                          PasswordResetToken.used_at.is_(None))
                  .update({"used_at": datetime.utcnow()}, synchronize_session=False))
        db.commit()
        print(f"Password updated for {user.username}.")
        if killed:
            print(f"Invalidated {killed} outstanding reset link(s).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
