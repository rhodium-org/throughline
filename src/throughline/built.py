# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Whether an item is built, and when (UR-0049 to UR-0052, SR-0377 to SR-0384).

Agreement and building are two axes. Agreement is the signature record
(:mod:`signatures`), with who and when. Building is this module: a project may
name, as its ``built`` status role, the statuses in which an item is realised by
the work the graph governs (SR-0377), and every move into or out of them is
recorded on the item, in its ``implementations`` list, with when, by whom and at
which revision (SR-0378). A project that names no built role has no built axis:
nothing here writes to it or judges it.

A project laid over work that already existed records when, as ``[project]
adopted`` (SR-0381); an item built no later than that is existing work waiting
to be accepted, not work done without agreement (SR-0380).

Pure functions over an item and its schema, apart from :func:`revision_at`,
which asks git which commit a work tree stands at.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

IMPLEMENTATIONS = "implementations"

AT, BY, REVISION = "at", "by", "revision"
WITHDRAWN, ADOPTED, BACKFILLED = "withdrawn", "adopted", "backfilled"


def now_iso() -> str:
    """The time now, as a signature records it (UR-0040)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalise_time(value) -> str | None:
    """A time as UTC ``YYYY-MM-DDTHH:MM:SSZ``, from a TOML datetime, a date or an
    ISO 8601 text; ``None`` for nothing. Raises ``ValueError`` for anything else,
    so a mistyped baseline is refused rather than compared as text."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        moment = value
    elif hasattr(value, "year") and not isinstance(value, str):     # a date
        moment = datetime(value.year, value.month, value.day)
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def revision_at(path: str | Path | None) -> str | None:
    """The commit the work tree holding ``path`` stands at, or ``None`` where
    there is no git or no repository."""
    if path is None:
        return None
    where = Path(path)
    where = where if where.is_dir() else where.parent
    try:
        out = subprocess.run(["git", "-C", str(where), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def has_built_axis(schema) -> bool:
    """Whether the project names a built role at all (SR-0377)."""
    return bool(schema.built_statuses)


def is_built(item, schema) -> bool:
    """Whether ``item`` stands in a status of the built role."""
    return item.status in schema.built_statuses


def _entries(item) -> list[dict]:
    return [e for e in (item.implementations or []) if isinstance(e, dict)]


def first_built_at(item) -> str | None:
    """When the item was first recorded as built: the first entry that is not a
    withdrawal."""
    for entry in _entries(item):
        if not entry.get(WITHDRAWN) and entry.get(AT):
            return normalise_time(entry[AT])
    return None


def built_at(item, schema) -> str | None:
    """When the item was last put in a built status (SR-0382): the time of the
    last entry, while it stands built and that entry is not a withdrawal;
    ``None`` otherwise."""
    if not is_built(item, schema):
        return None
    entries = _entries(item)
    if not entries or entries[-1].get(WITHDRAWN):
        return None
    return normalise_time(entries[-1].get(AT))


def adopted(item, schema) -> bool:
    """Whether the item is existing work the graph was laid over (SR-0380): built,
    and first built no later than the project's adopted time."""
    if not schema.adopted or not is_built(item, schema):
        return False
    first = first_built_at(item)
    return first is not None and first <= schema.adopted


def signed_at(item) -> str | None:
    """The time of the latest signature that stands on the item (SR-0382)."""
    from .signatures import relevant
    times = [normalise_time(e.get(AT)) for e in relevant(item) if e.get(AT)]
    times = [t for t in times if t]
    return max(times) if times else None


def crosses(schema, frm: str | None, to: str | None) -> bool:
    """Whether a move from ``frm`` to ``to`` crosses the built role's edge."""
    built = schema.built_statuses
    return bool(built) and ((frm in built) != (to in built))


def note_move(schema, item, frm: str, to: str, *, by: str | None = None,
              at: str | None = None, **marks) -> dict | None:
    """Record a status move on ``item`` where it crosses the built edge (SR-0378),
    finding who and which revision only when it does: a move that crosses nothing
    starts no git process."""
    if not crosses(schema, frm, to):
        return None
    if by is None:
        from .identity import default_ratifier
        by = default_ratifier(item._path.parent if item._path else None)
    revision = revision_at(item._path) if to in schema.built_statuses else None
    return record_move(schema, item, frm, to, by=by, revision=revision, at=at, **marks)


def record_move(schema, item, frm: str, to: str, *, by: str | None,
                revision: str | None, at: str | None = None,
                **marks) -> dict | None:
    """Append the entry a status move writes (SR-0378), and return it; ``None``
    when the move does not cross the built role's edge, or the project names no
    built role. Into the role: when, by whom, at which revision. Out of it: when
    and by whom, marked withdrawn."""
    if not has_built_axis(schema):
        return None
    was, now = frm in schema.built_statuses, to in schema.built_statuses
    if was == now:
        return None
    entry: dict = {AT: at or now_iso()}
    if by:
        entry[BY] = by
    if now:
        if revision:
            entry[REVISION] = revision
    else:
        entry[WITHDRAWN] = True
    entry.update({k: v for k, v in marks.items() if v})
    item.implementations = list(item.implementations or []) + [entry]
    return entry


def describe_for_signing(item, schema) -> str | None:
    """What a signature on ``item`` would be (SR-0384): agreement to work not yet
    built, or acceptance of work built at a given time from a given revision.
    ``None`` where the project has no built axis."""
    if not has_built_axis(schema):
        return None
    if not is_built(item, schema):
        return "this signature agrees work not yet built"
    entries = [e for e in _entries(item) if not e.get(WITHDRAWN)]
    last = entries[-1] if entries else {}
    when = normalise_time(last.get(AT)) if last.get(AT) else None
    rev = last.get(REVISION)
    detail = ""
    if when:
        detail += f" at {when}"
    if rev:
        detail += f" from {str(rev)[:12]}"
    if last.get(ADOPTED):
        detail += " (existing work the graph was laid over)"
    if not detail:
        detail = " (no record says when)"
    return f"this signature accepts work already built{detail}"


class AdoptError(Exception):
    """`tl adopt` refused, before anything was written."""


def adopt(project, uids=None, *, all_items: bool = False, at: str | None = None,
          by: str | None = None) -> tuple[str, list[str], bool]:
    """Record when the graph was laid over existing work, and mark that work built
    (SR-0381). Sets ``[project] adopted`` to ``at`` (now by default), and gives each
    item named, or every live item with ``all_items``, an ``implementations``
    entry at that time, by ``by``, at the current revision, marked ``adopted``,
    moving it into the first status of the built role where it is not built yet.

    Returns ``(adopted time, the items marked, whether the configuration
    changed)``. Idempotent: run again with the same time it changes nothing.
    Every item is checked before any is written, so a refusal writes nothing."""
    from .grounding import transition_refusal
    from .identity import default_ratifier
    from .storage import CONFIG_NAME, write_item
    from .tomledit import TomlDocument

    schema = project.schema
    if not has_built_axis(schema):
        raise AdoptError(
            "this project names no built role under [status.roles], so nothing in "
            "it is built and there is nothing to adopt (SR-0377)")
    if not uids and not all_items:
        raise AdoptError("name the items that already exist, or pass --all")
    when = normalise_time(at) if at else now_iso()
    if schema.adopted and schema.adopted != when:
        raise AdoptError(
            f"the graph was already laid over existing work at {schema.adopted}; "
            "a second baseline would move which work counts as existing")
    if all_items:
        chosen = [i for i in project.items()
                  if not i.is_deleted and i.status not in schema.dead_statuses()]
    else:
        chosen = []
        for uid in uids:
            item = project.get(uid)
            if item is None:
                raise AdoptError(f"{uid} does not exist")
            chosen.append(item)
    target = schema.built_order[0]
    for item in chosen:
        if not is_built(item, schema):
            refusal = transition_refusal(schema, item, target)
            if refusal is not None:
                raise AdoptError(
                    f"{refusal}: allow it with `tl schema transition allow "
                    f"{item.status} {target}`, or `tl migrate` (SR-0379)")
    who = by or default_ratifier(project.path)
    revision = revision_at(project.path)
    marked: list[str] = []
    for item in chosen:
        if any(isinstance(e, dict) and e.get(ADOPTED) for e in item.implementations):
            continue
        if not is_built(item, schema):
            item.status = target
        entry = {AT: when, BY: who}
        if revision:
            entry[REVISION] = revision
        entry[ADOPTED] = True
        item.implementations = list(item.implementations or []) + [entry]
        write_item(item, project.register_of(item.uid))
        marked.append(item.uid)
    changed = False
    if schema.adopted != when:
        cfg = Path(project.path) / CONFIG_NAME
        doc = TomlDocument(cfg.read_text(encoding="utf-8"))
        doc.set_key("project", "adopted", when, because=(
            "When this graph was laid over work that already existed (SR-0381): an "
            "item built no later than this awaits acceptance, and `tl check "
            "--strict` does not refuse it unsigned (SR-0380). Set by `tl adopt`."))
        cfg.write_text(doc.text(), encoding="utf-8")
        changed = True
    return when, marked, changed
