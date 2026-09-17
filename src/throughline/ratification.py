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
second copy of the rule, drifting from the first. The same holds one level down:
a changed prose field's diff is data too (:func:`diff_prose`, SR-0200), and the
terminal rendering here is one reader of it among others.
"""
from __future__ import annotations

import difflib
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .fingerprint import content_fingerprint, fingerprint, normative_attr_names, signed_content
from .identity import RATIFIED_BY_ATTR
from .model import Item
from .schema import Schema

# The attribute holding the revision resolved for a stamp (SR-0166). Written by
# `tl migrate`, never by ratify: the content being ratified is still uncommitted
# when ratify runs, so the revision that holds it does not exist yet, and
# recording HEAD then would anchor to the state before the change.
REVISION_ATTR = "ratified_revision"

STAMP_ATTR = "ratified_fingerprint"

# The content the stamp was taken over, recorded beside it (SR-0215). Read before
# any history is touched, and only when it reproduces the stamp (SR-0216).
CONTENT_ATTR = "ratified_content"

# The identity a corrected record replaced (SR-0196). Written only by ratify, and
# only for a record that had not been published, so it never names a signature
# anyone else could have seen.
SUPERSEDED_ATTR = "ratified_supersedes"

#: Outcomes. Kept as four distinct values because collapsing any pair of them
#: loses a claim a reader acts on differently (SR-0165).
CHANGED = "changed"
UNCHANGED = "unchanged"
UNRESOLVABLE = "unresolvable"
UNRATIFIED = "unratified"

#: Where the earlier content came from (SR-0216): the recorded content on the
#: ratification record, or a revision found in version-control history.
RECORD = "record"
HISTORY = "history"

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
    stamp, present only when the difference was resolved from history. ``source``
    says where the earlier content came from — :data:`RECORD` or :data:`HISTORY` —
    and is empty when no earlier content was looked for (SR-0216). ``reason`` says
    why the difference could not be resolved, and is empty otherwise.
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
    source: str = ""

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
            "source": self.source,
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


def ratification_is_committed(project, item) -> bool | None:
    """Whether ``item``'s current ratification record appears in the project's
    committed history (SR-0196). ``None`` when that cannot be established.

    The question is asked of version control and never inferred. A record is
    published once the committed file carries this same ratifier over this same
    stamp — the pair is what makes it the same record, so a signature taken, amended
    and re-taken in one sitting does not read as published because an earlier one
    was. ``None`` where the question cannot be put — no work tree, or a tree whose
    state cannot be read: the caller must then refuse, because a correction is
    permitted only on evidence that the record was never shared, and absence of
    evidence is not that evidence. A work tree holding no commits at all is not
    such a case; it is an answer, and the answer is that nothing is published.
    """
    by = item.attrs.get(RATIFIED_BY_ATTR)
    stamp = item.attrs.get(STAMP_ATTR)
    if not by or not stamp:
        return False            # nothing recorded is nothing published
    if item._path is None:
        return None
    top = repo_top(Path(item._path).parent)
    if top is None:
        return None
    rel = _relative(top, Path(item._path))
    if rel is None:
        return None
    if _git(top, "rev-parse", "--verify", "--quiet", "HEAD^{commit}") is None:
        # An unborn HEAD is an answer, not a gap: a work tree with no commits has
        # published nothing, so no record in it can have been shared.
        return False
    past = _item_at(top, "HEAD", rel)
    if past is None:
        # The file is absent at HEAD, so this record has never been committed.
        # Distinguished from an unreadable tree above, which returns None.
        return False
    return (past.attrs.get(RATIFIED_BY_ATTR) == by
            and past.attrs.get(STAMP_ATTR) == stamp)


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


def recorded_content(item) -> dict | None:
    """The content recorded on ``item``'s ratification record, only where it
    reproduces the record's stamp (SR-0216). A record holding none, or holding
    content that does not reproduce the stamp, answers None: such content is not
    evidence of what was signed, so nothing may show it as the earlier wording."""
    stamp = item.attrs.get(STAMP_ATTR)
    content = item.attrs.get(CONTENT_ATTR)
    if not stamp or content is None:
        return None
    if content_fingerprint(item.authored_uid, content) != stamp:
        return None
    return content


def content_at_stamp(project, item) -> tuple[dict | None, str]:
    """The content ``item``'s stamp was taken over, where it can be proved, for a
    record that holds none (SR-0218). Returns ``(content, where)``: from the item
    as it stands when its content still reproduces the stamp (``where`` is
    ``"current"``), otherwise from the revision history resolves for the stamp
    (``where`` is that revision). ``(None, reason)`` when neither proves it.

    Proof is the stamp and nothing else, the same test SR-0165 applies to a
    revision, so what this returns is always content the stamp reproduces."""
    stamp = item.attrs.get(STAMP_ATTR)
    if not stamp:
        return None, "the record carries no fingerprint"
    if fingerprint(item, project.schema) == stamp:
        return signed_content(item, project.schema), "current"
    sha, reason, _cached = resolve_revision(project, item)
    if sha is None:
        return None, reason
    top = repo_top(Path(item._path).parent)
    rel = _relative(top, Path(item._path)) if top else None
    past = _item_at(top, sha, rel) if (top and rel) else None
    if past is None:
        return None, f"the item could not be re-read at {sha[:9]}"
    cfg_rel = _relative(top, Path(project.path) / "throughline.toml") or ""
    content = signed_content(past, _schema_at(top, sha, cfg_rel, project.schema))
    if content_fingerprint(item.authored_uid, content) != stamp:  # pragma: no cover - defensive
        return None, f"the content at {sha[:9]} does not reproduce the stamp"
    return content, sha


def _change_from_record(project, item, stamp: str, recorded: dict) -> RatificationChange:
    """The change since ratification, from recorded content alone (SR-0216). The
    comparison is the one the history route makes — the fingerprint's scalar
    inputs, then every attribute normative either when signed or now — so the two
    routes report the same fields for the same content."""
    changes: list[FieldChange] = []
    for name in FINGERPRINT_SCALARS:
        if name == "uid":
            continue                  # an item's UID never changes; none is recorded
        was, now = recorded[name], getattr(item, name, None)
        if was != now:
            changes.append(FieldChange(field=name, was=was, now=now))
    signed_attrs = recorded["attrs"]
    for name, was in signed_attrs.items():
        # An attribute absent when signed is recorded as the empty string the
        # fingerprint hashes for it; it is reported as absent, as history would.
        was = None if was == "" else was
        now = item.attrs.get(name)
        if was != now:
            changes.append(FieldChange(field=f"attrs.{name}", was=was, now=now))
    # An attribute normative now but not when signed was never recorded, so its
    # signed value is unknown here. Reporting it as absent would claim something
    # the record cannot know (SR-0165); it is named in the reason instead.
    unrecorded = [n for n in normative_attr_names(item, project.schema)
                  if n not in signed_attrs]
    if not changes:
        why = ("the recorded content reproduces the stamp yet no recorded field "
               "differs, so what moved is outside the fields the record holds")
        if unrecorded:
            why += (" — " + ", ".join(unrecorded) + " became normative after the "
                    "signature, and its signed value was not recorded")
        return RatificationChange(uid=item.uid, outcome=UNRESOLVABLE, stamp=stamp,
                                  source=RECORD, reason=why)
    return RatificationChange(uid=item.uid, outcome=CHANGED, stamp=stamp,
                              changes=tuple(changes), source=RECORD)


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

    # The record first (SR-0216): it exists wherever the item file does, so the
    # answer needs no version control. Content that does not reproduce the stamp
    # is passed over, never shown, and history is asked instead — a revision is
    # itself accepted only when it reproduces the stamp.
    recorded = recorded_content(item)
    if recorded is not None:
        return _change_from_record(project, item, stamp, recorded)

    sha, reason, cached = resolve_revision(project, item)
    if sha is None:
        return RatificationChange(uid=item.uid, outcome=UNRESOLVABLE, stamp=stamp,
                                  reason=reason, source=HISTORY)

    top = repo_top(Path(item._path).parent)
    rel = _relative(top, Path(item._path)) if top else None
    cfg_rel = _relative(top, Path(project.path) / "throughline.toml") or "" if top else ""
    past = _item_at(top, sha, rel) if (top and rel) else None
    if past is None:                                  # pragma: no cover - defensive
        return RatificationChange(
            uid=item.uid, outcome=UNRESOLVABLE, stamp=stamp, source=HISTORY,
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
            cached=cached, source=HISTORY,
            reason=(f"{sha[:9]} reproduces the stamp yet no reported field "
                    "differs, so what moved is outside the fields this comparison "
                    "covers"))
    return RatificationChange(uid=item.uid, outcome=CHANGED, stamp=stamp,
                              revision=sha, changes=tuple(changes), cached=cached,
                              source=HISTORY)


# -------------------------------------------------------------------- rendering

def _value(v: object) -> str:
    if v is None:
        return "(unset)"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


# A sentence ends at . ! or ? followed by whitespace. Good enough for requirement
# prose; an abbreviation such as "e.g." splits a sentence in two, which costs a
# unit shown twice at worst, never a change hidden.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

# Painted as git paints a diff on a terminal: a removed line red, an added line
# green, and within a replaced sentence the words that moved in reverse video —
# so a reworded eighty-word sentence is read at the word that changed, not
# searched for it. 27 turns reverse off without dropping the line's colour.
_RED, _GREEN, _RESET = "\033[31m", "\033[32m", "\033[0m"
_EMPHASIS, _UNEMPHASIS = "\033[7m", "\033[27m"
_PAINT = {"-": _RED, "+": _GREEN, " ": ""}


def _units(value: str) -> list[str]:
    """The pieces a prose value is compared and shown in: its lines where it has
    them, else its sentences (SR-0198)."""
    if "\n" in value:
        return value.splitlines()
    return _SENTENCE_END.split(value) if value else [""]


def is_prose(was: object, now: object) -> bool:
    """Whether a changed field is shown as a diff (SR-0198): both values are
    strings and at least one holds whitespace. A token — a priority, a flag — is
    shown as before and after on one line instead."""
    return (isinstance(was, str) and isinstance(now, str)
            and any(ch.isspace() for ch in was + now))


#: A word of a unit and whether it is among the words that moved.
Word = tuple[str, bool]

#: The marks a unit of a prose diff carries, as a git diff marks its lines.
KEPT, REMOVED, ADDED = " ", "-", "+"


@dataclass(frozen=True)
class DiffUnit:
    """One unit of a prose diff (SR-0200): a sentence, or a line where the value
    has lines, marked :data:`KEPT`, :data:`REMOVED` or :data:`ADDED`, with each
    word and whether it is among the words that moved. Words are marked only
    between a removed sentence and the added one it plainly became; a unit
    deleted, added or rewritten outright carries no marks."""
    mark: str
    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        return " ".join(w for w, _moved in self.words)

    @property
    def marked(self) -> bool:
        """Whether any word of the unit is among the words that moved."""
        return any(moved for _w, moved in self.words)


def _plain(unit: str) -> list[Word]:
    return [(w, False) for w in unit.split()]


def _word_marks(removed: str, added: str) -> tuple[list[Word], list[Word]]:
    """Both sides of a replaced sentence with the words that differ marked."""
    a, b = removed.split(), added.split()
    keep_a, keep_b = [False] * len(a), [False] * len(b)
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            keep_a[i1:i2] = [True] * (i2 - i1)
            keep_b[j1:j2] = [True] * (j2 - j1)
    return ([(w, not k) for w, k in zip(a, keep_a)],
            [(w, not k) for w, k in zip(b, keep_b)])


# Below this share of words in common, a removed sentence and an added one are
# not the same sentence reworded, and marking their differences would mark
# nearly every word — the whole is clearer than the parts.
_SAME_SENTENCE = 0.5


def _pair_words(removed: list[str],
                added: list[str]) -> tuple[list[list[Word]], list[list[Word]]]:
    """The units of one hunk with the words that moved marked, wherever a removed
    sentence has a successor it plainly became. Each removed sentence takes the
    most similar added one still free; a sentence with no counterpart — deleted
    outright, added outright, or rewritten beyond recognition — is shown whole."""
    marks_r = [_plain(u) for u in removed]
    marks_a = [_plain(u) for u in added]
    free = list(range(len(added)))

    def alike(r: str, s: str) -> float:
        return difflib.SequenceMatcher(None, r.split(), s.split(),
                                       autojunk=False).ratio()

    for ri, r in enumerate(removed):
        if not free:
            break
        best = max(free, key=lambda ai: alike(r, added[ai]))
        if alike(r, added[best]) >= _SAME_SENTENCE:
            marks_r[ri], marks_a[best] = _word_marks(r, added[best])
            free.remove(best)
    return marks_r, marks_a


def diff_prose(was: str, now: str) -> tuple[DiffUnit, ...]:
    """The difference between two prose values as data (SR-0200). Kept units stay
    in place, so a removed sentence sits above its replacement inside the
    paragraph it belongs to, and removed units precede added ones within a hunk,
    as they do in a git diff (SR-0198).

    This is the one place the comparison is made. :func:`render_change` reads it
    to paint a terminal, and a consumer with a screen of its own reads it too,
    so the two can never disagree about what moved."""
    a, b = _units(was), _units(now)
    units: list[DiffUnit] = []
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            units.extend(DiffUnit(KEPT, tuple(_plain(u))) for u in a[i1:i2])
            continue
        marks_r, marks_a = _pair_words(a[i1:i2], b[j1:j2])
        units.extend(DiffUnit(REMOVED, tuple(w)) for w in marks_r)
        units.extend(DiffUnit(ADDED, tuple(w)) for w in marks_a)
    return tuple(units)


def wrap_words(words: list[Word] | tuple[Word, ...], *, first: str, rest: str,
               width: int) -> list[list[Word]]:
    """Greedy word wrap of a unit's words that never breaks a word and keeps each
    word's mark. ``first`` and ``rest`` are the prefixes the caller will draw
    before the first and the later lines; the wrap is measured on the words alone
    so that painting a line afterwards cannot lengthen it past ``width``."""
    lines: list[list[Word]] = []
    line: list[Word] = []
    used = len(first)
    for word, moved in words:
        need = len(word) + (1 if line else 0)
        if line and used + need > width:
            lines.append(line)
            line, used = [], len(rest)
            need = len(word)
        line.append((word, moved))
        used += need
    lines.append(line)
    return lines


def _render_prose(field: str, was: str, now: str, *, pad: str,
                  columns: int, colour: bool) -> list[str]:
    """One field's change as its kept, removed and added units, each wrapped to
    the terminal (SR-0198). Built from :func:`diff_prose` and nothing else
    (SR-0200): what is painted here is exactly what the data says."""
    lines = [f"{pad}{field}:"]
    indent = pad * 2
    width = max(columns, 40)

    def painted(wrapped: list[Word]) -> str:
        # Consecutive moved words share one run, spaces included, so a reworded
        # phrase reads as a phrase and not as a row of blinking words.
        out: list[str] = []
        open_run = False
        for word, moved in wrapped:
            sep = " " if out else ""
            if colour and moved and not open_run:
                out.append(f"{sep}{_EMPHASIS}{word}")
                open_run = True
            elif colour and moved:
                out.append(f"{sep}{word}")
            else:
                if open_run:
                    out.append(_UNEMPHASIS)
                    open_run = False
                out.append(f"{sep}{word}")
        if open_run:
            out.append(_UNEMPHASIS)
        return "".join(out)

    for unit in diff_prose(was, now):
        first, rest = f"{indent}{unit.mark} ", f"{indent}  "
        paint = _PAINT[unit.mark] if colour else ""
        for n, wrapped in enumerate(wrap_words(unit.words, first=first, rest=rest,
                                               width=width)):
            head = first if n == 0 else rest
            line = f"{head}{painted(wrapped)}".rstrip()
            lines.append(f"{paint}{line}{_RESET}" if paint else line)
    return lines


def render_change(change: RatificationChange, *, ratifier: str | None = None,
                  width: int = 2, columns: int = 80,
                  colour: bool = False) -> list[str]:
    """The difference as lines of text, for a terminal ``columns`` wide, painted
    with ANSI colour only when ``colour`` is asked for — the caller knows whether
    a terminal is listening. Kept apart from the resolution above so that a caller
    wanting fields to lay out never has to take formatted output back apart
    (SR-0165)."""
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
    where = (f"at {change.revision[:9]}{', cached' if change.cached else ''}"
             if change.revision else "from the record")
    lines = [f"changed since it was ratified{who} "
             f"(stamp {change.stamp}, ratified content {where}):"]
    for c in change.changes:
        if is_prose(c.was, c.now):
            lines.extend(_render_prose(c.field, c.was, c.now, pad=pad,
                                       columns=columns, colour=colour))
        else:
            lines.append(f"{pad}{c.field}: was {_value(c.was)!r}, "
                         f"now {_value(c.now)!r}")
    return lines
