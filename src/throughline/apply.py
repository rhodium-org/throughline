# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Carrying out a change set drafted where the Tool cannot run (SR-0252).

An assistant or agent that cannot run the Tool — one that reads a capped number
of files and returns only text, as Microsoft 365 Copilot does in a controlled
environment — works from an export of the graph, built on `tl dump` and the
fingerprint and revision each item carries there (SR-0251). The export is the
business of whatever tool briefs that assistant (rhodium-org/throughline-ms-copilot
does it for Copilot); the assistant sends its changes back as a *change set*: one JSON
document listing the Tool's own operations — new, amend, link, unlink, delete —
each naming the item it touches and, for an item that already exists, quoting
the revision it was drafted against: the digest of its whole stored record that
the dump gives beside its fingerprint, so a change to any field since the export
refuses the set (format 2). A format-1 set quotes the fingerprint instead and is
still read, with the weaker check that implies.

The set is carried out all or nothing. Every operation is checked against the
graph as it stands before anything is written: a malformed operation, an
operation the Tool does not offer an assistant (a decision, or a change to the
structure), or a revision that is not the item's current one refuses the
whole set, naming every offending operation. Each operation then runs through
the same function the matching command runs — :func:`new_item`,
:func:`amend_item`, :func:`add_link`, :func:`remove_link`, :func:`delete_item`
— so no rule is written twice, and a refusal any of them raises restores every
file the set had touched. Everything the set creates or changes is recorded
with a machine origin, so it enters the lifecycle as proposed and a person still
decides (SR-0092).

These are the functions `tl apply` is made of; the command line is one caller
(SR-0224, SR-0225). A program embedding the Tool reaches them by name.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .fingerprint import fingerprint, revision
from .grounding import GroundingError, set_status
from .items import amend_item, delete_item, item_type_for, new_item, parse_attrs
from .links import GroundingView, LinkError, add_link, remove_link
from .model import Item, Link
from .schema import SchemaError
from .storage import ProjectError, write_item
from .uid import UidError

#: The version of the change-set form this release documents and writes about.
#: Format 2 quotes each existing item's ``revision``; format 1 quoted its
#: ``fingerprint``, which misses a change to the title, rationale, links, status
#: or a non-normative attribute, and is still read so a set already drafted
#: against it is not broken.
CHANGE_SET_FORMAT_VERSION = 2

#: The versions :func:`parse_change_set` reads, and the key each one quotes an
#: existing item's version under.
READABLE_FORMATS = {1: "fingerprint", 2: "revision"}

#: The operations an assistant may ask for, in the Tool's own vocabulary.
OPERATIONS = ("new", "amend", "link", "unlink", "delete")

# Commands an assistant may not take through a change set: decisions a person
# makes, and changes to the structure of the graph. Named so a refusal says why.
_DECISIONS = ("ratify", "withdraw", "clarify", "status", "invalidate", "flag",
              "review", "schema", "migrate", "resolve", "init", "register")

_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

# The keys each operation may carry, and the ones it must. An operation on an
# existing item may also carry the version key of its format (READABLE_FORMATS).
_KEYS: dict[str, tuple[set[str], set[str]]] = {
    "new": ({"register"},
            {"op", "register", "type", "title", "text", "rationale", "attrs",
             "links", "label"}),
    "amend": ({"uid"},
              {"op", "uid", "title", "text", "rationale", "attrs",
               "unset"}),
    "link": ({"uid", "target", "type"},
             {"op", "uid", "target", "type", "stamp"}),
    "unlink": ({"uid", "target"},
               {"op", "uid", "target", "type"}),
    "delete": ({"uid"}, {"op", "uid", "reason"}),
}

#: The change-set form, as the text handed to an assistant with the export.
#: A tool that briefs an assistant takes it from here (`tl apply --describe`),
#: so what the assistant is told to write cannot drift from what
#: :func:`parse_change_set` accepts.
CHANGE_SET_FORMAT = f"""\
# A throughline change set — the form `tl apply` accepts (format {CHANGE_SET_FORMAT_VERSION})

A change set is one JSON document: a list of operations, carried out in order,
all or nothing. `tl apply` refuses the whole set, naming every offending
operation, when any operation is malformed, is not one of the five below, or
quotes a revision that is not the item's current one. Nothing is written
until every operation has been checked, and a refused set changes nothing.

An item that already exists is named by its UID and carries the "revision"
the export gave for it (the `revision` field of the item in `tl dump`). Any
change to the item since the export — its text, title, rationale, links,
status or any attribute — refuses the set, so a change is never written over a
newer one. An item created earlier in the same set is named by its label,
written "$label", and carries no revision.

Everything the set creates or changes is recorded with origin "ai" and waits
as proposed: a person decides afterwards. Decisions — ratify, withdraw,
clarify, status, schema, migrate — are not operations; put them in words to
the person instead.

The five operations:

{{"op": "new", "register": "SR", "type": "system_requirement", "title": "...",
 "text": "...", "rationale": "...", "attrs": {{"priority": "should"}},
 "links": [{{"target": "UR-0003", "type": "implements"}}], "label": "sr1"}}
  creates an item in the register with that prefix. "type" may be left out
  where the register declares one. "links" ground the item: a non-root item
  must reach a root through them. "label" is optional.

{{"op": "amend", "uid": "SR-0012", "revision": "sha256:...", "title": "...",
 "text": "...", "rationale": "...", "attrs": {{"priority": "must"}},
 "unset": ["owner"]}}
  changes only the fields given; an empty string clears a field. Only an
  attribute the item's type declares may be set.

{{"op": "link", "uid": "SR-0012", "revision": "sha256:...",
 "target": "UR-0003", "type": "implements"}}
  adds a typed link from the item to the target.

{{"op": "unlink", "uid": "SR-0012", "revision": "sha256:...",
 "target": "UR-0003", "type": "implements"}}
  removes that link; "type" may be left out when one link joins them. Refused
  where it would leave an item ungrounded.

{{"op": "delete", "uid": "SR-0012", "revision": "sha256:...",
 "reason": "..."}}
  tombstones the item: its file stays and its UID is never reused.

Rules: no other keys; a "uid", "target" or "label" is written exactly; every
value is a string except "attrs" (strings, numbers or booleans), "stamp"
(boolean), and "unset" and "links" (lists). The list may also be sent as
{{"change_set_format": {CHANGE_SET_FORMAT_VERSION}, "operations": [...]}}, which is the safer form. Never send rewritten
item files: send this change set, and nothing else.
"""


class ChangeSetError(ValueError):
    """The whole set refused, with one line per offending operation. Nothing
    was written, or everything written has been restored."""

    def __init__(self, problems: list[str]):
        self.problems = list(problems)
        super().__init__("change set refused — nothing was changed:\n"
                         + "\n".join(f"  {p}" for p in self.problems))


@dataclass
class OperationResult:
    """What one operation did: its position in the set (from 1), its kind, the
    item it touched, the outcome in the matching command's own word, and a line
    of detail."""
    index: int
    op: str
    uid: str
    outcome: str
    detail: str = ""


@dataclass
class ApplyResult:
    """What a set did: each operation's outcome, the files that differ from
    before (relative to the project root), and the items created and changed.
    A dry run reports the same and leaves no file different."""
    operations: list[OperationResult] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    dry_run: bool = False


def _is_label(value: object) -> bool:
    return isinstance(value, str) and value.startswith("$")


def _scalar(value: object) -> bool:
    return isinstance(value, (str, int, float, bool)) and not isinstance(value, bool) \
        or isinstance(value, bool)


def _inferred_format(data: list) -> int:
    """The format of a set that does not say: 1 only where its operations quote a
    fingerprint and none quotes a revision, so a format-1 set drafted before
    format 2 still reads as it was written. A set mixing the two is read as the
    current format, whose rules then name each fingerprint as an unknown key."""
    ops = [op for op in data if isinstance(op, dict)]
    if any("fingerprint" in op for op in ops) and not any("revision" in op for op in ops):
        return 1
    return CHANGE_SET_FORMAT_VERSION


def parse_change_set(text: str) -> list[dict]:
    """Read a change set from its JSON text, refusing it whole when any operation
    is malformed or is not one an assistant may take (SR-0252). A set that names
    its ``change_set_format`` is read in that format; one that does not is read
    as the current format unless it quotes only fingerprints. The operations
    come back as given, checked for shape; whether each can be carried out is
    :func:`apply_change_set`'s to say."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ChangeSetError([f"the change set is not JSON: {e}"]) from None
    version = None
    if isinstance(data, dict) and "operations" in data:
        version = data.get("change_set_format")
        if version is not None and (isinstance(version, bool)
                                    or version not in READABLE_FORMATS):
            raise ChangeSetError([f"change_set_format {version!r} is not one this "
                                  "release reads ("
                                  + ", ".join(map(str, READABLE_FORMATS)) + ")"])
        data = data["operations"]
    if not isinstance(data, list):
        raise ChangeSetError(["the change set must be a JSON list of operations "
                              "(or an object whose \"operations\" is that list)"])
    if version is None:
        version = _inferred_format(data)
    quoted = READABLE_FORMATS[version]
    problems: list[str] = []
    ops: list[dict] = []
    labels: set[str] = set()
    for i, op in enumerate(data, start=1):
        where = f"#{i}"
        if not isinstance(op, dict):
            problems.append(f"{where}: an operation must be a JSON object")
            continue
        kind = op.get("op")
        if kind not in OPERATIONS:
            if kind in _DECISIONS:
                problems.append(f"{where}: '{kind}' is a decision or a change to the "
                                "graph's structure, not an operation an assistant "
                                f"may take ({', '.join(OPERATIONS)})")
            else:
                problems.append(f"{where}: \"op\" must be one of "
                                f"{', '.join(OPERATIONS)}, got {kind!r}")
            continue
        required, allowed = _KEYS[kind]
        if kind != "new":
            allowed = allowed | {quoted}
        name = f"{where} {kind}"
        extra = sorted(set(op) - allowed)
        if extra:
            problems.append(f"{name}: unknown key(s) {', '.join(extra)}")
        missing = sorted(required - set(op))
        if missing:
            problems.append(f"{name}: missing {', '.join(missing)}")
        for key in ("uid", "target", "register", "type", "title", "text",
                    "rationale", "label", "reason", quoted):
            if key in op and not isinstance(op[key], str):
                problems.append(f"{name}: \"{key}\" must be a string")
        if "stamp" in op and not isinstance(op["stamp"], bool):
            problems.append(f"{name}: \"stamp\" must be true or false")
        if "attrs" in op:
            attrs = op["attrs"]
            if not isinstance(attrs, dict) or not all(
                    isinstance(k, str) and _scalar(v) for k, v in attrs.items()):
                problems.append(f"{name}: \"attrs\" must be an object of strings, "
                                "numbers or booleans")
        if "unset" in op and not (isinstance(op["unset"], list) and all(
                isinstance(v, str) and v for v in op["unset"])):
            problems.append(f"{name}: \"unset\" must be a list of attribute names")
        if "links" in op:
            links = op["links"]
            if not isinstance(links, list) or not all(
                    isinstance(l, dict) and set(l) == {"target", "type"}
                    and isinstance(l.get("target"), str)
                    and isinstance(l.get("type"), str) for l in links):
                problems.append(f"{name}: \"links\" must be a list of "
                                "{\"target\": UID, \"type\": kind}")
        uid = op.get("uid")
        if kind != "new" and isinstance(uid, str):
            if _is_label(uid):
                if uid[1:] not in labels:
                    problems.append(f"{name}: {uid} names no item created earlier "
                                    "in this set")
            elif quoted not in op:
                problems.append(f"{name} {uid}: an operation on an existing item "
                                f"carries the \"{quoted}\" it was based on")
            elif isinstance(op[quoted], str) and not _DIGEST_RE.match(op[quoted]):
                problems.append(f"{name} {uid}: \"{quoted}\" must be the sha256:… "
                                "value the export gave")
        if kind == "new":
            label = op.get("label")
            if isinstance(label, str):
                if not _LABEL_RE.match(label):
                    problems.append(f"{name}: \"label\" must be letters, digits, "
                                    "'_' or '-', got {label!r}")
                elif label in labels:
                    problems.append(f"{name}: label {label!r} is already used in "
                                    "this set")
                else:
                    labels.add(label)
        for ref in (op.get("target"), *[l.get("target") for l in op.get("links", [])
                                        if isinstance(l, dict)]):
            if _is_label(ref) and ref[1:] not in labels:
                problems.append(f"{name}: {ref} names no item created earlier in "
                                "this set")
        ops.append(op)
    if problems:
        raise ChangeSetError(problems)
    return ops


def _snapshot(root: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in root.rglob("*")
            if p.is_file() and ".git" not in p.parts}


def _restore(root: Path, before: dict[Path, bytes]) -> None:
    for p in [p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]:
        if p not in before:
            p.unlink()
    for p, data in before.items():
        if not p.exists() or p.read_bytes() != data:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)


def _differs(root: Path, before: dict[Path, bytes]) -> list[str]:
    now = _snapshot(root)
    changed = [p for p in now if before.get(p) != now[p]]
    changed += [p for p in before if p not in now]
    return sorted(p.relative_to(root).as_posix() for p in changed)


def _ai_origin(schema) -> str:
    origins = schema.ai_origins
    return "ai" if "ai" in origins else sorted(origins)[0]


def apply_change_set(project, operations: list[dict], *,
                     view: GroundingView | None = None,
                     find: Callable[[str], Item | None] | None = None,
                     dry_run: bool = False) -> ApplyResult:
    """Carry out ``operations`` (as :func:`parse_change_set` returns them) on
    ``project``, all or nothing (SR-0252).

    Every operation is first checked against the graph as it stands: the item it
    names exists and is not deleted, the revision it quotes (or, in a format-1
    set, the fingerprint) is the item's current one, and what it links to can be
    found. A problem anywhere refuses the whole set before a file is written.
    Then each operation runs through the function the matching command runs, and a refusal from any of them restores
    every file and refuses the set the same way. ``view`` and ``find`` let a
    program that grounds over a wider graph than the one it writes — a union with
    its sources — judge links and find targets there (SR-0212); left out, they are
    the project's own. A ``dry_run`` checks and carries out the set, reports it,
    and then restores every file."""
    schema = project.schema
    find = find or project.get
    root = Path(project.path)
    ai = _ai_origin(schema)
    problems: list[str] = []
    labels: dict[str, str] = {}

    def existing(name: str, op: dict) -> Item | None:
        uid = op["uid"]
        if _is_label(uid):
            return None
        item = project.get(uid)
        if item is None:
            problems.append(f"{name}: {uid} does not exist")
        elif item.is_deleted:
            problems.append(f"{name}: {uid} is deleted — a tombstone is permanent")
        else:
            key = "revision" if "revision" in op else "fingerprint"
            now = revision(item) if key == "revision" else fingerprint(item, schema)
            if op.get(key) != now:
                problems.append(f"{name}: {uid} changed since the export — the set "
                                f"quotes {key} {op.get(key)} but the item now "
                                f"carries {now}")
        return item

    def target_known(name: str, ref: str) -> None:
        if _is_label(ref) or find(ref) is not None:
            return
        problems.append(f"{name}: target {ref} does not exist")

    # Everything is judged before anything moves.
    for i, op in enumerate(operations, start=1):
        kind = op["op"]
        name = f"#{i} {kind}"
        if kind == "new":
            if op["register"] not in project.registers:
                problems.append(f"{name}: no register with prefix "
                                f"'{op['register']}'")
            for l in op.get("links", []):
                target_known(name, l["target"])
                if schema.link_types and l["type"] not in schema.link_types:
                    problems.append(f"{name}: '{l['type']}' is not a link type this "
                                    f"project declares ({', '.join(sorted(schema.link_types))})")
            if "label" in op:
                labels[op["label"]] = ""
            continue
        existing(name, op)
        if kind in ("link", "unlink"):
            target_known(name, op["target"])
        if kind == "link" and schema.link_types and op["type"] not in schema.link_types:
            problems.append(f"{name}: '{op['type']}' is not a link type this project "
                            f"declares ({', '.join(sorted(schema.link_types))})")
    if problems:
        raise ChangeSetError(problems)

    before = _snapshot(root)
    result = ApplyResult(dry_run=dry_run)
    created: list[str] = []
    changed: list[str] = []

    def resolve(ref: str) -> str:
        return labels[ref[1:]] if _is_label(ref) else ref

    roles = schema.status_roles or {}
    initial, proposed = roles.get("initial"), roles.get("proposed")

    def put_forward(item: Item) -> None:
        """Move an item the set changed out of the initial status into proposed,
        as an item the set creates is born (SR-0252): machine-authored content
        waits as proposed. Only from the initial status, and only where the
        project's lifecycle has that move — an item a person has already moved
        on keeps its place, and the set never writes a transition the project
        forbids."""
        if (initial and proposed and item.status == initial
                and schema.allows_transition(initial, proposed)):
            set_status(schema, item, proposed)

    def mark_changed(item: Item) -> None:
        """Record that the set changed this item (SR-0252): it carries the machine
        origin, so the gate asks a person before it counts (SR-0092)."""
        if item.attrs.get("origin") != ai:
            item.attrs["origin"] = ai
        put_forward(item)
        write_item(item, project.register_of(item.uid))
        if item.uid not in changed and item.uid not in created:
            changed.append(item.uid)

    try:
        for i, op in enumerate(operations, start=1):
            kind = op["op"]
            if kind == "new":
                prefix = op["register"]
                item_type = item_type_for(project, prefix, op.get("type"))
                pairs = [f"{k}={v}" for k, v in (op.get("attrs") or {}).items()]
                attrs = parse_attrs(schema, item_type, pairs, command="apply")
                item = new_item(project, prefix, item_type=item_type,
                                title=op.get("title", ""), text=op.get("text", ""),
                                origin=ai, attrs=attrs)
                if op.get("rationale"):
                    item.rationale = op["rationale"]
                for l in op.get("links", []):
                    item.links.append(Link(target=resolve(l["target"]), type=l["type"]))
                path = write_item(item, project.register_of(item.uid))
                if "label" in op:
                    labels[op["label"]] = item.uid
                created.append(item.uid)
                result.operations.append(OperationResult(
                    i, kind, item.uid, "created", path.relative_to(root).as_posix()))
                continue
            uid = resolve(op["uid"])
            if kind == "amend":
                item = project.get(uid)
                pairs = [f"{k}={v}" for k, v in (op.get("attrs") or {}).items()]
                attrs = parse_attrs(schema, item.type, pairs, command="apply",
                                    declared_only=True)
                attrs["origin"] = ai
                outcome = amend_item(project, uid, title=op.get("title"),
                                     text=op.get("text"), rationale=op.get("rationale"),
                                     attrs=attrs, unset=op.get("unset", ()))
                put_forward(item)
                write_item(item, project.register_of(uid))
                if uid not in changed and uid not in created:
                    changed.append(uid)
                moved = [c for c in outcome.changed if c != "origin"]
                result.operations.append(OperationResult(
                    i, kind, uid, "amended", ", ".join(moved) or "nothing moved"))
            elif kind == "link":
                target = resolve(op["target"])
                word = add_link(project, uid, target, op["type"],
                                stamp=bool(op.get("stamp", False)), view=view)
                mark_changed(project.get(uid))
                result.operations.append(OperationResult(
                    i, kind, uid, word, f"{uid} --{op['type']}--> {target}"))
            elif kind == "unlink":
                target = resolve(op["target"])
                kinds = remove_link(project, uid, target, op.get("type"), view=view)
                mark_changed(project.get(uid))
                result.operations.append(OperationResult(
                    i, kind, uid, "unlinked",
                    ", ".join(f"{uid} --{k}--> {target}" for k in kinds)))
            elif kind == "delete":
                retired = delete_item(project, uid, reason=op.get("reason", "unspecified"))
                mark_changed(project.get(uid))
                result.operations.append(OperationResult(
                    i, kind, uid, "tombstoned" if retired else "already deleted",
                    op.get("reason", "")))
    except (GroundingError, LinkError, UidError, SchemaError, ProjectError,
            ValueError) as e:
        _restore(root, before)
        raise ChangeSetError([f"#{i} {kind} {op.get('uid', op.get('register', ''))}: "
                              f"{e}"]) from None

    result.files = _differs(root, before)
    result.created = created
    result.changed = changed
    if dry_run:
        _restore(root, before)
    return result
