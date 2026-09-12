# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""What changed since an item was ratified (SR-0165, SR-0166).

A ratification stamp is a one-way digest: it proves that content moved and says
nothing about *what* moved. Recovering the difference means finding the revision
whose content reproduces the stamp and comparing it, field by field, with the
content as it now stands.

Two rules govern that search, and both are the point rather than details of it.

**The stamp is the only identifier of the earlier content.** A revision is a
candidate because its fingerprint reproduces the stamp exactly, never because of
when it was made. Taking the revision nearest the ratification would be a guess
wearing the clothes of an answer, and wrong precisely when history is untidy —
which is when it is being asked.

**Refusing beats approximating, and "cannot say" is not "no change".** An empty
difference is itself a claim: that the content still stands as it was ratified.
It is the one claim that would send a reader past exactly the change this module
exists to put in front of them, so the unresolvable case is its own outcome and a
graver one than either answer. It is reachable honestly — a stamp backfilled over
content that was never a revision, rewritten history, an item not yet committed —
and each of those deserves to be named rather than smoothed over.

Results are returned as structured data, with rendering kept separate
(:func:`render_change`). Every other surface that must show this — a composing
consumer, a review cockpit — would otherwise re-parse formatted text, which is a
second copy of the rule, drifting from the first.
"""
from __future__ import annotations

import difflib
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .fingerprint import fingerprint
from .model import Item
from .schema import Schema

# The attribute holding the revision resolved for a stamp (SR-0166). Written by
# `tl migrate`, never by ratify: the content being ratified is still uncommitted
# when ratify runs, so the revision that holds it does not exist yet, and
# recording HEAD then would anchor to the state before the change.
REVISION_ATTR = "ratified_revision"

STAMP_ATTR = "ratified_fingerprint"

#: Outcomes. Kept as four distinct values because collapsing any pair of them
#: loses a claim a reader acts on differently (SR-0165).
CHANGED = "changed"
UNCHANGED = "unchanged"
UNRESOLVABLE = "unresolvable"
UNRATIFIED = "unratified"

# The fingerprint's own scalar inputs, in the order it hashes them. Only these
# and the normative attributes are reported: they are the fields whose change
# made the item stale, so naming a moved title or status here would report
# something that did not cause what the reader is being asked to accept.
FINGERPRINT_SCALARS = ("uid", "type", "text", "normative", "derived")


@dataclass(frozen=True)
class FieldChange:
    """One fingerprint input that differs between the stamped content and the
    current content. ``field`` is the item's own field name, or ``attrs.<name>``
    for a normative attribute."""
    field: str
    was: object
    now: object

    @property
    def multiline(self) -> bool:
        return any(isinstance(v, str) and "\n" in v for v in (self.was, self.now))


@dataclass(frozen=True)
class RatificationChange:
    """The difference between an item's current normative content and the content
    its ratification stamp was taken over.

    ``outcome`` is one of :data:`CHANGED`, :data:`UNCHANGED`, :data:`UNRESOLVABLE`
    or :data:`UNRATIFIED`. ``revision`` is the commit whose content reproduced the
    stamp, present only when the difference could be resolved. ``reason`` says why
    it could not, and is empty otherwise.
    """
    uid: str
    outcome: str
    stamp: str | None = None
    revision: str | None = None
    changes: tuple[FieldChange, ...] = ()
    reason: str = ""
    #: True when the resolved revision came from the cached attribute rather than
    #: a walk. Diagnostic only — the stamp is what made it acceptable either way.
    cached: bool = False

    @property
    def stale(self) -> bool:
        """Whether the signature no longer covers the content — the condition
        SR-0167 gates a re-ratification on. True for an unresolvable difference
        too: that the change cannot be described does not make it absent."""
        return self.outcome in (CHANGED, UNRESOLVABLE)

    def as_dict(self) -> dict:
        """The structured form for a caller that serialises rather than renders."""
        return {
            "uid": self.uid,
            "outcome": self.outcome,
            "stamp": self.stamp,
            "revision": self.revision,
            "reason": self.reason,
            "cached": self.cached,
            "changes": [{"field": c.field, "was": c.was, "now": c.now}
                        for c in self.changes],
        }


# --------------------------------------------------------------------- plumbing

def _git(top: Path, *args: str) -> str | None:
    """Run a read-only git command, returning stdout or None on any failure.
    Every caller treats None as "history is unavailable", which is a legitimate
    answer here and never an error to raise."""
    try:
        done = subprocess.run(["git", "-C", str(top), *args],
                              capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    return done.stdout


def repo_top(path: str | Path) -> Path | None:
    """The work tree containing ``path``, or None when it is not in one."""
    out = _git(Path(path), "rev-parse", "--show-toplevel")
    return Path(out.strip()) if out and out.strip() else None


def _blob(top: Path, sha: str, rel: str) -> str | None:
    return _git(top, "show", f"{sha}:{rel}")


def _schema_at(top: Path, sha: str, cfg_rel: str, fallback: Schema) -> Schema:
    """The project's schema as it stood at ``sha``.

    Which attributes are normative is the authoring graph's judgement (SR-0162),
    and that judgement is in the config, not the item — so a fingerprint computed
    under today's schema over yesterday's item can fail to reproduce a stamp that
    was perfectly valid. Reading the config alongside the item keeps the
    comparison faithful; where that revision has no readable config the current
    schema is the only available answer.
    """
    raw = _blob(top, sha, cfg_rel)
    if raw is None:
        return fallback
    try:
        return Schema.from_config(tomllib.loads(raw))
    except (tomllib.TOMLDecodeError, ValueError, KeyError, TypeError):
        return fallback


def _item_at(top: Path, sha: str, rel: str) -> Item | None:
    """The item as it stood at ``sha``, or None when it was absent or unreadable."""
    from .storage import _load_yaml            # local: storage imports this module

    raw = _blob(top, sha, rel)
    if raw is None:
        return None
    try:
        data = _load_yaml(raw)
    except Exception:                          # a malformed historical blob
        return None
    if not isinstance(data, dict) or "uid" not in data or "type" not in data:
        return None
    try:
        return Item.from_dict(data)
    except (KeyError, TypeError, ValueError):
        return None


def _relative(top: Path, path: Path) -> str | None:
    try:
        return path.resolve().relative_to(top).as_posix()
    except ValueError:
        return None


# -------------------------------------------------------------------- resolving

def _reproduces(top: Path, sha: str, rel: str, cfg_rel: str, stamp: str,
                fallback: Schema) -> bool:
    """Whether the item at ``sha`` fingerprints to ``stamp`` — the only test that
    makes a revision the one that was ratified."""
    past = _item_at(top, sha, rel)
    if past is None:
        return False
    return fingerprint(past, _schema_at(top, sha, cfg_rel, fallback)) == stamp


def resolve_revision(project, item) -> tuple[str | None, str, bool]:
    """Find the revision whose content reproduces ``item``'s ratification stamp.

    Returns ``(sha, reason, cached)``. ``sha`` is None when no revision reproduces
    the stamp, and ``reason`` then says why in words an operator can act on.

    A cached revision (SR-0166) is re-verified against the stamp before it is
    reused and discarded where it does not match. A cache is exactly what goes
    wrong quietly: a hint left behind by rewritten history, a restored backup or a
    hand edit would otherwise produce a confident difference against the wrong
    content, which SR-0165 makes the one outcome worse than showing none. The
    stamp stays the authority; the revision is only ever an answer it has already
    agreed with.
    """
    stamp = item.attrs.get(STAMP_ATTR)
    if not stamp:
        return None, "the record carries no fingerprint to resolve against", False
    if item._path is None:
        return None, "the item has no file on disk", False
    top = repo_top(Path(item._path).parent)
    if top is None:
        return None, "the project is not inside a git work tree", False
    rel = _relative(top, Path(item._path))
    if rel is None:
        return None, "the item's file is outside the git work tree", False
    cfg_rel = _relative(top, Path(project.path) / "throughline.toml") or ""
    schema = project.schema

    cached = item.attrs.get(REVISION_ATTR)
    if isinstance(cached, str) and cached:
        if _reproduces(top, cached, rel, cfg_rel, stamp, schema):
            return cached, "", True
        # Discarded rather than trusted, and not reported as a failure: resolving
        # again is the repair, and the walk below may well succeed.

    # --follow so a register move does not read as a truncated history. Newest
    # first only shortens the common case; correctness does not depend on order,
    # because a candidate qualifies by reproducing the stamp and nothing else.
    log = _git(top, "log", "--follow", "--format=%H", "--", rel)
    if log is None:
        return None, "git could not read this item's history", False
    shas = [ln.strip() for ln in log.splitlines() if ln.strip()]
    if not shas:
        return None, ("the item has no committed history — it has never been "
                      "committed, so the content that was ratified was never "
                      "recorded"), False
    for sha in shas:
        if _reproduces(top, sha, rel, cfg_rel, stamp, schema):
            return sha, "", False
    return None, (f"no revision of this item reproduces the stamp — {len(shas)} "
                  "revision(s) were examined; the record may have been backfilled "
                  "over content that was never committed (ratified_backfilled), or "
                  "the history holding it may have been rewritten"), False


def _normative_names(project, item, past_schema: Schema | None) -> list[str]:
    """The normative attribute names to compare, taking both schemas' judgement.

    Which attributes count can itself have changed between the two revisions, and
    an attribute that was normative when the signature was given is part of what
    was signed even if it is not normative now."""
    names = list(project.schema.normative_attrs(item.type))
    if past_schema is not None:
        for n in past_schema.normative_attrs(item.type):
            if n not in names:
                names.append(n)
    return names


def change_since_ratification(project, item) -> RatificationChange:
    """The difference between ``item``'s current normative content and the content
    its ratification stamp was taken over (SR-0165).

    Never approximated: where no revision reproduces the stamp the result says the
    change cannot be shown, which is kept distinct from having found no change.
    """
    stamp = item.attrs.get(STAMP_ATTR)
    if not stamp:
        return RatificationChange(uid=item.uid, outcome=UNRATIFIED)
    if fingerprint(item, project.schema) == stamp:
        # The signature still covers the content. Reported before any history is
        # touched, both because it is the common case and because it is the one
        # answer that needs no revision to justify it.
        return RatificationChange(uid=item.uid, outcome=UNCHANGED, stamp=stamp)

    sha, reason, cached = resolve_revision(project, item)
    if sha is None:
        return RatificationChange(uid=item.uid, outcome=UNRESOLVABLE, stamp=stamp,
                                  reason=reason)

    top = repo_top(Path(item._path).parent)
    rel = _relative(top, Path(item._path)) if top else None
    cfg_rel = _relative(top, Path(project.path) / "throughline.toml") or "" if top else ""
    past = _item_at(top, sha, rel) if (top and rel) else None
    if past is None:                                  # pragma: no cover - defensive
        return RatificationChange(
            uid=item.uid, outcome=UNRESOLVABLE, stamp=stamp,
            reason=f"the item could not be re-read at {sha[:9]}")
    past_schema = _schema_at(top, sha, cfg_rel, project.schema)

    changes: list[FieldChange] = []
    for name in FINGERPRINT_SCALARS:
        was, now = getattr(past, name, None), getattr(item, name, None)
        if was != now:
            changes.append(FieldChange(field=name, was=was, now=now))
    for name in _normative_names(project, item, past_schema):
        was, now = past.attrs.get(name), item.attrs.get(name)
        if was != now:
            changes.append(FieldChange(field=f"attrs.{name}", was=was, now=now))

    # A resolved revision with no differing field means the two fingerprints
    # disagree over something this comparison does not cover — a real gap, and one
    # that must not be reported as "nothing changed" (SR-0165).
    if not changes:
        return RatificationChange(
            uid=item.uid, outcome=UNRESOLVABLE, stamp=stamp, revision=sha,
            cached=cached,
            reason=(f"{sha[:9]} reproduces the stamp yet no reported field "
                    "differs, so what moved is outside the fields this comparison "
                    "covers"))
    return RatificationChange(uid=item.uid, outcome=CHANGED, stamp=stamp,
                              revision=sha, changes=tuple(changes), cached=cached)


# -------------------------------------------------------------------- rendering

def _value(v: object) -> str:
    if v is None:
        return "(unset)"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def render_change(change: RatificationChange, *, ratifier: str | None = None,
                  width: int = 2) -> list[str]:
    """The difference as lines of text, for a terminal. Kept apart from the
    resolution above so that a caller wanting fields to lay out never has to take
    formatted output back apart (SR-0165)."""
    pad = " " * width
    who = f" by {ratifier}" if ratifier else ""
    if change.outcome == UNRATIFIED:
        return []
    if change.outcome == UNCHANGED:
        return [f"unchanged since it was ratified{who}."]
    if change.outcome == UNRESOLVABLE:
        return [
            f"WHAT CHANGED SINCE IT WAS RATIFIED{who.upper()} CANNOT BE SHOWN.",
            f"{pad}{change.reason}.",
            f"{pad}stamp {change.stamp}",
            f"{pad}nobody can state what you would be accepting.",
        ]
    lines = [f"changed since it was ratified{who} "
             f"(stamp {change.stamp}, ratified content at {change.revision[:9]}"
             f"{', cached' if change.cached else ''}):"]
    for c in change.changes:
        if c.multiline:
            lines.append(f"{pad}{c.field}:")
            was = _value(c.was).splitlines() or [""]
            now = _value(c.now).splitlines() or [""]
            diff = list(difflib.unified_diff(was, now, lineterm="", n=1))
            for ln in diff[2:] if len(diff) > 2 else diff:
                lines.append(f"{pad}{pad}{ln}")
        else:
            lines.append(f"{pad}{c.field}: was {_value(c.was)!r}, "
                         f"now {_value(c.now)!r}")
    return lines
