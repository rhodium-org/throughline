# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Named operations that change a project's own schema (SR-0181).

Every other part of the graph is reachable through an operation — item content,
links, status moves — and the schema that decides what a legal graph *is* was the
exception. These are the verbs that close it: each names the change it makes, so
it can judge its own result rather than merely ask whether the file still parses.

Three rules govern every operation here:

* The resulting schema is built and validated before anything is written, so a
  change that cannot produce a coherent schema fails at the source (SR-0181).
* A change that could take away a permission is measured against the project's
  current items and **refused** if it would invalidate any of them, naming what
  it hit and the operation that resolves it (SR-0182). There is no force flag.
* The file is edited surgically and the reason recorded beside the change
  (SR-0183, SR-0184) — see :mod:`throughline.tomledit`.

Whether a change narrows is judged by its *effect* on what the schema admits, not
by the verb expressing it: :func:`may_invalidate` proves a change safe or sends it
to the pre-flight, and it declines to prove safety for the case that catches
authors out — the first entry added to a rule that was previously absent, which
forbids everything it does not name.
"""
from __future__ import annotations

import copy
import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .model import Project
from .schema import Schema, SchemaError
from .storage import CONFIG_NAME, ProjectError, load_project
from .tomledit import TomlDocument, TomlEditError
from .validate import ERROR, Finding, validate

# What to do about each finding a schema change can introduce. A refusal that
# only counts the damage sends its reader off to search the graph; naming the
# operation that resolves each item is what lets a human or an agent act on the
# spot (SR-0182).
_REMEDY = {
    "bad-status": "move it to a declared status with `tl status <UID> <STATUS>`",
    "bad-transition": "the move it already made is no longer permitted — allow it "
                      "again, or accept the item's current status",
    "unknown-key": "remove the attribute from the item with `tl amend <UID>`",
    "schema": "correct the item with `tl amend <UID>`",
    "bad-link-target": "retype or remove the link with `tl link --retype` / `tl unlink`",
    "bad-link-shape": "retype or remove the link with `tl link --retype` / `tl unlink`",
    "dangling-link": "remove the link with `tl unlink`",
    "orphan": "ground it with `tl link <UID> <PARENT> --type <GROUND LINK>`",
    "unserved-root": "author an item grounding to it, or leave it out of "
                     "[grounding] delivery_roots",
    "unratified": "ratify it with `tl ratify <UID> --by <WHO>`",
    "grounding-cycle": "break the cycle with `tl unlink`",
    "suspect-unreachable": "allow a move to the suspect status from it",
}

# The grounding table is the one place set-containment does not decide safety:
# *adding* to delivery_roots obliges a root to be served, and adding to ai_origins
# obliges existing items to be ratified. Both widen a vocabulary and invalidate
# items, so a grounding change is never proved safe by inspection.
_GROUNDING_FIELDS = ("root_types", "delivery_roots", "ground_link_types",
                     "suspect_link_types", "ai_origins")


class SchemaOpError(Exception):
    """The operation cannot be carried out as asked."""


@dataclass(frozen=True)
class Change:
    """One named schema change: what it does, the config it produces, and how to
    write it into the document."""
    description: str
    config: dict
    edit: Callable[[TomlDocument, str], None]


@dataclass(frozen=True)
class Refusal:
    """A change that was refused because it would invalidate existing items."""
    description: str
    findings: list[Finding]

    def render(self, composed: bool) -> str:
        lines = [f"refused: {self.description} would invalidate "
                 f"{len(self.findings)} item(s) — nothing was written", ""]
        for f in self.findings:
            where = f.uid or Path(f.file).name
            lines.append(f"  {where:<12} {f.message}")
            remedy = _REMEDY.get(f.rule)
            if remedy:
                lines.append(f"  {'':<12} → {remedy}")
        lines += ["", "Resolve those items first and run this again, or choose a "
                      "change that does not take the permission away."]
        if composed:
            lines.append(
                "This project declares composed sources. The check above reads "
                "this graph alone; run `tl-compose check` for the assembled union.")
        return "\n".join(lines)


# --------------------------------------------------------------------- safety

def _wider(old: frozenset | None, new: frozenset | None) -> bool:
    """Whether ``new`` permits at least everything ``old`` did, where ``None``
    means unconstrained. A constraint appearing where there was none is the case
    this refuses to call safe (SR-0182)."""
    if new is None:
        return True
    if old is None:
        return False
    return new >= old


def may_invalidate(old: Schema, new: Schema, *, grounding_changed: bool) -> bool:
    """Whether the move from ``old`` to ``new`` could make an existing item
    invalid. False only when that is provably impossible."""
    if grounding_changed:
        return True
    if not _wider(old.statuses, new.statuses):
        return True
    if not _wider(old.link_types, new.link_types):
        return True
    if old.transitions is not None:
        if new.transitions is None:
            pass
        else:
            for frm in set(old.transitions) | set(new.transitions):
                if not new.transitions.get(frm, frozenset()) >= old.transitions.get(
                        frm, frozenset()):
                    return True
    for ltype in set(old.link_rules) | set(new.link_rules):
        o = old.link_rules.get(ltype)
        n = new.link_rules.get(ltype)
        o_frm, o_to = (o.frm, o.to) if o else (None, None)
        n_frm, n_to = (n.frm, n.to) if n else (None, None)
        if not _wider(o_frm, n_frm) or not _wider(o_to, n_to):
            return True
    if set(old.types) - set(new.types):
        return True
    for tname, old_attrs in old.types.items():
        new_attrs = new.types.get(tname, {})
        if set(old_attrs) - set(new_attrs):
            return True
        for aname, spec in new_attrs.items():
            was = old_attrs.get(aname)
            if was is None and spec.required:
                return True
            if was is not None and spec.values and not (
                    frozenset(spec.values) >= frozenset(was.values)):
                return True
    return False


def _new_errors(project: Project, config: dict) -> list[Finding]:
    """Error-severity findings the prospective config introduces. Measured as a
    difference so a graph that is already red does not block an unrelated
    change, and so the report names only what this change did."""
    before = {_key(f) for f in validate(project) if f.severity == ERROR}
    prospective = dataclasses.replace(project, config=config, _schema=None)
    return [f for f in validate(prospective)
            if f.severity == ERROR and _key(f) not in before]


def _key(f: Finding) -> tuple[str, str, str]:
    return (f.rule, f.uid, f.file)


# ------------------------------------------------------------------- applying

def apply_change(root: Path, change: Change, because: str) -> Refusal | str:
    """Validate, pre-flight, and write ``change``. Returns the description on
    success or a :class:`Refusal` that left the file untouched."""
    project = load_project(root)
    try:
        prospective = Schema.from_config(change.config)
    except SchemaError as e:
        raise SchemaOpError(f"that change does not produce a valid schema: {e}") from e

    grounding_changed = ((project.config.get("grounding") or {})
                         != (change.config.get("grounding") or {}))
    if may_invalidate(project.schema, prospective, grounding_changed=grounding_changed):
        findings = _new_errors(project, change.config)
        if findings:
            return Refusal(change.description, findings)

    cfg_file = root / CONFIG_NAME
    doc = TomlDocument(cfg_file.read_text(encoding="utf-8"))
    try:
        change.edit(doc, because)
    except TomlEditError as e:
        raise SchemaOpError(f"cannot edit {cfg_file} safely: {e}") from e
    cfg_file.write_text(doc.text(), encoding="utf-8")
    return change.description


def is_composed(project: Project) -> bool:
    return bool(project.config.get("sources"))


def load(root: Path) -> Project:
    try:
        return load_project(root)
    except ProjectError as e:
        raise SchemaOpError(str(e)) from e


# ---------------------------------------------------------------- operations

def _copy(project: Project) -> dict:
    return copy.deepcopy(project.config)


def _array(cfg: dict, table: str, key: str) -> list:
    return list((cfg.get(table) or {}).get(key) or [])


def _set_array(cfg: dict, table: str, key: str, values: list) -> None:
    cfg.setdefault(table, {})[key] = values


def _add_to(project: Project, table: str, key: str, value: str, noun: str, *,
            current: list[str] | None = None) -> Change:
    """Add ``value`` to the array at ``table.key``. ``current`` is what the schema
    already permits while the key is absent, for a field that carries a default;
    starting from nothing there would replace the default with this one member,
    which is a narrowing wearing the word 'add'."""
    cfg = _copy(project)
    present = _array(cfg, table, key)
    values = (present or list(current or [])) + [value]
    if values.count(value) > 1:
        raise SchemaOpError(f"{noun} '{value}' is already declared")
    _set_array(cfg, table, key, values)
    # An addition leaves the members before it where they are, so it is written
    # in place; only a key that is not in the file yet has to be rendered whole.
    edit = ((lambda doc, why: doc.add_to_array(table, key, [value], because=why))
            if present else
            (lambda doc, why: doc.set_key(table, key, values, because=why)))
    return Change(f"adding {noun} '{value}'", cfg, edit)


def _remove_from(project: Project, table: str, key: str, value: str, noun: str, *,
                 current: list[str] | None = None) -> Change:
    cfg = _copy(project)
    values = _array(cfg, table, key) or list(current or [])
    if value not in values:
        raise SchemaOpError(f"{noun} '{value}' is not declared")
    values.remove(value)
    _set_array(cfg, table, key, values)
    return Change(f"removing {noun} '{value}'", cfg,
                  lambda doc, why: doc.set_key(table, key, values, because=why))


def _undeclared(noun: str, cmd: str, value: str, in_use: set[str]) -> SchemaOpError:
    """The refusal for adding to a vocabulary that is not declared at all.

    An undeclared vocabulary admits everything, so naming its first member
    excludes every other — the same narrowing worn as a widening that SR-0182
    covers for link rules. Which vocabulary the project means is a decision
    rather than an increment, so it is proposed and not assumed; the proposal is
    what the graph uses today, which is the one list guaranteed to leave every
    existing item valid."""
    proposed = sorted(in_use | {value})
    return SchemaOpError(
        f"the {noun} vocabulary is not declared, so every {noun} is legal here; "
        f"declaring '{value}' would narrow it to that one alone. Declare the "
        f"vocabulary you mean first —\n"
        f"    tl schema {cmd} declare {' '.join(proposed)} --because '...'\n"
        f"which is what the graph uses today plus '{value}' — then add to it "
        f"with this command.")


def _declare(project: Project, table: str, key: str, values: list[str],
             noun: str, cmd: str) -> Change:
    """Declare a vocabulary that has none. This narrows — everything was legal
    before it — so it goes to the same pre-flight as any other narrowing and is
    refused if the list leaves an existing item behind (SR-0182)."""
    if _array(project.config, table, key):
        raise SchemaOpError(
            f"the {noun} vocabulary is already declared; add to it with "
            f"`tl schema {cmd} add`")
    if len(set(values)) != len(values):
        raise SchemaOpError(f"the same {noun} is named twice")
    cfg = _copy(project)
    _set_array(cfg, table, key, list(values))
    return Change(f"declaring the {noun} vocabulary as {', '.join(values)}", cfg,
                  lambda doc, why: doc.set_key(table, key, list(values),
                                               because=why))


def status_add(project: Project, name: str) -> Change:
    if not _array(project.config, "status", "values"):
        raise _undeclared("status", "status", name,
                          project.relied_on_statuses())
    return _add_to(project, "status", "values", name, "status")


def status_declare(project: Project, names: list[str]) -> Change:
    return _declare(project, "status", "values", names, "status", "status")


def status_remove(project: Project, name: str) -> Change:
    return _remove_from(project, "status", "values", name, "status")


def linktype_add(project: Project, name: str) -> Change:
    if not _array(project.config, "links", "types"):
        raise _undeclared("link type", "linktype", name,
                          project.relied_on_link_types())
    return _add_to(project, "links", "types", name, "link type")


def linktype_declare(project: Project, names: list[str]) -> Change:
    return _declare(project, "links", "types", names, "link type", "linktype")


def linktype_remove(project: Project, name: str) -> Change:
    return _remove_from(project, "links", "types", name, "link type")


def grounding_add(project: Project, field: str, value: str) -> Change:
    _check_grounding_field(field)
    return _add_to(project, "grounding", field, value, f"{field} entry",
                   current=sorted(getattr(project.schema, field)))


def grounding_remove(project: Project, field: str, value: str) -> Change:
    _check_grounding_field(field)
    return _remove_from(project, "grounding", field, value, f"{field} entry",
                        current=sorted(getattr(project.schema, field)))


def _check_grounding_field(field: str) -> None:
    if field not in _GROUNDING_FIELDS:
        raise SchemaOpError(
            f"'{field}' is not a grounding field — expected one of "
            + ", ".join(_GROUNDING_FIELDS))


def transition_allow(project: Project, frm: str, to: str) -> Change:
    cfg = _copy(project)
    targets = _array(cfg, "transitions", frm)
    if to in targets:
        raise SchemaOpError(f"'{frm}' may already move to '{to}'")
    targets.append(to)
    _set_array(cfg, "transitions", frm, targets)
    return Change(f"permitting the move {frm} -> {to}", cfg,
                  lambda doc, why: doc.set_key("transitions", frm, targets,
                                               because=why))


def transition_deny(project: Project, frm: str, to: str) -> Change:
    cfg = _copy(project)
    targets = _array(cfg, "transitions", frm)
    if to not in targets:
        raise SchemaOpError(f"'{frm}' does not permit a move to '{to}'")
    targets.remove(to)
    _set_array(cfg, "transitions", frm, targets)
    return Change(f"forbidding the move {frm} -> {to}", cfg,
                  lambda doc, why: doc.set_key("transitions", frm, targets,
                                               because=why))


def linkrule_allow(project: Project, ltype: str, *, side: str, itype: str) -> Change:
    return _linkrule(project, ltype, side=side, itype=itype, add=True)


def linkrule_deny(project: Project, ltype: str, *, side: str, itype: str) -> Change:
    return _linkrule(project, ltype, side=side, itype=itype, add=False)


def _linkrule(project: Project, ltype: str, *, side: str, itype: str,
              add: bool) -> Change:
    if side not in ("from", "to"):
        raise SchemaOpError("a link rule side is 'from' or 'to'")
    cfg = _copy(project)
    rules = cfg.setdefault("link_rules", {})
    rule = dict(rules.get(ltype) or {})
    members = list(rule.get(side) or [])
    unconstrained = side not in rule
    if add:
        if itype in members:
            raise SchemaOpError(
                f"'{ltype}' already permits {side} '{itype}'")
        members.append(itype)
    else:
        if unconstrained:
            raise SchemaOpError(
                f"'{ltype}' constrains no {side} type, so there is none to deny — "
                "use `tl schema linkrule allow` to start constraining it")
        if itype not in members:
            raise SchemaOpError(f"'{ltype}' does not permit {side} '{itype}'")
        members.remove(itype)
    rule[side] = members
    rules[ltype] = rule
    verb = "permitting" if add else "forbidding"
    note = (" (which forbids every other type, because that side was "
            "unconstrained)" if add and unconstrained else "")
    return Change(f"{verb} {side} '{itype}' on link type '{ltype}'{note}", cfg,
                  lambda doc, why: doc.set_key("link_rules", ltype, rule,
                                               because=why))


def linkrule_clear(project: Project, ltype: str) -> Change:
    cfg = _copy(project)
    rules = cfg.setdefault("link_rules", {})
    if ltype not in rules:
        raise SchemaOpError(f"link type '{ltype}' has no endpoint rule")
    del rules[ltype]
    return Change(f"removing the endpoint rule on link type '{ltype}'", cfg,
                  lambda doc, why: _clear_rule(doc, ltype, why))


def _clear_rule(doc: TomlDocument, ltype: str, why: str) -> None:
    doc.remove_key("link_rules", ltype)
    doc.note_table("link_rules", why)


def type_add(project: Project, name: str) -> Change:
    cfg = _copy(project)
    types = cfg.setdefault("types", {})
    if name in types:
        raise SchemaOpError(f"item type '{name}' is already declared")
    types[name] = {}
    return Change(f"adding item type '{name}'", cfg,
                  lambda doc, why: doc.add_table(f"types.{name}", comment=why))


def type_remove(project: Project, name: str) -> Change:
    cfg = _copy(project)
    types = cfg.setdefault("types", {})
    if name not in types:
        raise SchemaOpError(f"item type '{name}' is not declared")
    del types[name]
    return Change(f"removing item type '{name}'", cfg,
                  lambda doc, why: _remove_type(doc, name, why))


def _remove_type(doc: TomlDocument, name: str, why: str) -> None:
    doc.remove_table(f"types.{name}")
    if doc.has_table("types"):
        doc.note_table("types", why)


def attr_add(project: Project, itype: str, name: str, *, kind: str | None = None,
             values: list[str] | None = None, required: bool = False,
             normative: bool = False, default=None) -> Change:
    cfg = _copy(project)
    types = cfg.setdefault("types", {})
    if itype not in types:
        raise SchemaOpError(f"item type '{itype}' is not declared")
    body = types[itype] = dict(types[itype] or {})
    attrs = body.setdefault("attrs", {})
    if name in attrs:
        raise SchemaOpError(f"'{itype}' already declares attribute '{name}'")
    spec: dict = {}
    if kind:
        spec["type"] = kind
    if values:
        spec["values"] = list(values)
    if required:
        spec["required"] = True
    if normative:
        spec["normative"] = True
    if default is not None:
        spec["default"] = default
    attrs[name] = spec
    return Change(f"adding attribute '{name}' to item type '{itype}'", cfg,
                  lambda doc, why: doc.set_key(f"types.{itype}", f"attrs.{name}",
                                               spec, because=why))


def attr_remove(project: Project, itype: str, name: str) -> Change:
    cfg = _copy(project)
    types = cfg.setdefault("types", {})
    if itype not in types:
        raise SchemaOpError(f"item type '{itype}' is not declared")
    body = types[itype] = dict(types[itype] or {})
    attrs = dict(body.get("attrs") or {})
    if name not in attrs:
        raise SchemaOpError(f"'{itype}' declares no attribute '{name}'")
    del attrs[name]
    body["attrs"] = attrs
    return Change(f"removing attribute '{name}' from item type '{itype}'", cfg,
                  lambda doc, why: _remove_attr(doc, itype, name, why))


def _remove_attr(doc: TomlDocument, itype: str, name: str, why: str) -> None:
    doc.remove_key(f"types.{itype}", f"attrs.{name}")
    doc.note_table(f"types.{itype}", why)
