"""Who signed — the identity side of a ratification record (SR-0156, SR-0157).

A ratification is evidence that a named human took accountability, so the value in
``ratified_by`` matters more than most fields the tool writes. Two problems live
here.

The first is that the tool used to offer the operating-system account name as the
default ratifier, which is rarely how anyone identifies themselves and differs from
the name their commits already carry. The estate's own graphs show the cost: the
same two people appear under five spellings, because the default and the name
people typed by hand were never the same string. The repository already knows who
is working in it — it is signing their commits — so that is the identity to offer
(SR-0156).

The second is that a name is not stable. People are renamed, and two people share a
name. A record may therefore carry a scheme-qualified identifier beside the name,
in its own field, never conflated with it (SR-0157). It is optional, it is never
invented, and obtaining it never costs a network call or a platform credential —
a ratification must be writable on a train.
"""

from __future__ import annotations

import getpass
import re
import subprocess
from pathlib import Path

RATIFIED_BY_ATTR = "ratified_by"
RATIFIED_ID_ATTR = "ratified_id"

# The withdrawal record (SR-0197): a signature that should no longer stand is
# removed, and what replaces it says who removed it, why, and whose signature it
# was. Held once — a later withdrawal overwrites an earlier one, as SR-0196 keeps
# only the immediately preceding identity; the graph is plain text under version
# control, so the succession is recoverable from history.
WITHDRAWN_BY_ATTR = "withdrawn_by"
WITHDRAWN_ID_ATTR = "withdrawn_id"
WITHDRAWN_REASON_ATTR = "withdrawn_reason"
WITHDRAWN_RATIFIER_ATTR = "withdrawn_ratifier"

# The ratification record, and the one command entitled to write each part of it
# (SR-0170). Any operation that sets attributes generally reaches these too unless
# it is stopped, which would make correcting a title and signing a name nobody gave
# the same keystroke. The value is the command that owns the attribute, so a refusal
# can name the route rather than only shut the door.
RATIFICATION_ATTRS = {
    RATIFIED_BY_ATTR: "ratify",
    RATIFIED_ID_ATTR: "ratify",
    "ratified_fingerprint": "ratify",
    # Written only by the format migration that binds a pre-stamp record, and
    # marked as attesting to content the ratifier never read (SR-0152).
    "ratified_backfilled": "migrate",
    # The revision whose content reproduces the stamp, cached so that what changed
    # since a signature can be shown without walking history (SR-0166). Migration
    # writes it, because when ratify runs the content being signed is not yet
    # committed. Guarded here like the rest of the record: it is re-verified
    # against the stamp before it is ever used, so a planted value cannot produce
    # a false difference — but a hand-written one would still be a claim about an
    # accountability record made by something other than the verb that owns it.
    "ratified_revision": "migrate",
    # The content the stamp was taken over, recorded so that what changed since a
    # signature can be shown with no version control at all (SR-0215, SR-0219).
    # Ratify writes it beside every stamp; migration also completes it for a
    # record made before it existed (SR-0218). Owned like the stamp, because a
    # hand-made copy could otherwise pass as the words someone agreed to — and so
    # withdrawal, which clears every attribute it does not own, removes it with
    # the signature.
    "ratified_content": "ratify",
    # The identity a correction replaced (SR-0196). Ratify writes it, because a
    # correction is itself a ratification; it is guarded like the rest of the
    # record because a hand-written one would claim that a signature succeeded
    # another when none did.
    "ratified_supersedes": "ratify",
    # Withdrawing a signature is as much a claim about people as giving one — who
    # withdrew it, whose it was — so the record is guarded like the rest (SR-0197).
    # Withdraw is also the verb that clears the fields above; a hand edit that
    # removed a signature would leave nothing saying one was ever taken.
    WITHDRAWN_BY_ATTR: "withdraw",
    WITHDRAWN_ID_ATTR: "withdraw",
    WITHDRAWN_REASON_ATTR: "withdraw",
    WITHDRAWN_RATIFIER_ATTR: "withdraw",
}

# A scheme-qualified identifier: 'github:octocat', 'email:ada@example.com'. The
# scheme is required — an identifier that does not say what kind of thing it is
# cannot be resolved later by anything but guesswork — but the set of schemes is
# deliberately open. Closing it would mean a project on a forge the tool has not
# heard of has to misfile its people under one it has.
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9+.\-]*:\S.*$")

_FALLBACK = "unknown"


class IdentityError(ValueError):
    """A supplied ratifier identifier is not usable as written."""


def git_identity(path: str | Path | None = None) -> tuple[str | None, str | None]:
    """The (name, email) the repository at ``path`` is configured to sign with.

    Reads local git configuration only — no network, no credential, no remote — so
    it is as available as the working copy itself. Returns ``(None, None)`` when git
    is absent, the directory is not a repository, or nothing is configured; the
    caller decides what to do about that rather than being handed a guess."""
    return _git_config("user.name", path), _git_config("user.email", path)


def _git_config(key: str, path: str | Path | None) -> str | None:
    try:
        out = subprocess.run(
            ["git", "config", "--get", key],
            cwd=str(path) if path else None,
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):   # git missing or unrunnable
        return None
    value = out.stdout.strip()
    return value or None


def default_ratifier(path: str | Path | None = None, *, identity=None) -> str:
    """The ratifier to *offer* when none was named (SR-0156).

    The identity the repository already signs commits with, falling back to the
    operating-system account name only where none is configured. This is a default a
    human may overrule, never a value written without their assent — the caller is
    responsible for that, and must not write this straight to an item.

    ``identity`` is the work the host environment does, taken as a parameter
    (SR-0226): a callable with :func:`git_identity`'s shape, which it defaults to.
    A caller with no subprocess — a browser runtime — supplies its own and gets the
    same operation."""
    name, _ = (identity or git_identity)(path)
    if name:
        return name
    try:
        return getpass.getuser()
    except Exception:                               # pragma: no cover — odd envs
        return _FALLBACK


def normalise_identifier(raw: str | None) -> str | None:
    """Check a ratifier identifier and return it, or ``None`` when none was given.

    Identifiers are optional and are never invented, so an absent one stays absent;
    what is refused is a *malformed* one, because an identifier that does not say
    which scheme it belongs to is not stable, merely opaque."""
    if raw is None:
        return None
    value = raw.strip()
    if not value:
        return None
    if not _IDENTIFIER.match(value):
        raise IdentityError(
            f"ratifier identifier {raw!r} must state its scheme, as "
            "'github:octocat' or 'email:ada@example.com'")
    return value
