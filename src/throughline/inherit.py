# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A project's schema taken from a declared source (SR-0275, SR-0276, SR-0279).

A ``[[sources]]`` entry marked ``schema = true`` is the project's *schema source*:
the project's schema is that source's schema, itself formed the same way, with the
project's own declarations added. This module is the pure half of that rule. It
never resolves anything; the loader hands it the source's configuration, already
formed, and the project's own.

What is inherited is exactly what SR-0275 names: item types and their attributes,
link types and endpoint rules, statuses, transitions and status roles, ratify
roles, the grounding configuration, and coverage rules. Everything else in
``throughline.toml`` — the project's name, the severities of its named rules, docs,
sources, the rest of ``[ratify]`` — stays the project's own.

A project may add to what it inherits but never redefine it (SR-0276). A name the
source declares may be declared again only with the identical value; a list the
source declares (link types, status values, grounding lists) takes the project's
extra members, and keeps every one of the source's. An attribute may be added to an
inherited type, but nothing else about that type may be said differently, and an
enum's values belong to whoever declares the attribute.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass

from .schema import _GROUNDING_DEFAULTS, SchemaError

# A declaration's address: ("type", name), ("attr", type, name), ("link", name),
# ("link_rule", type), ("status", value), ("status_role", role),
# ("transition", from), ("ratify_role", role), ("grounding", key, member) for a
# list member, ("grounding", key) for any other value, and ("coverage", rule) for a
# coverage rule, the rule written as canonical JSON since a rule has no name.
Path = tuple[str, ...]


class InheritanceError(SchemaError):
    """A project's own declaration redefines one its schema source makes (SR-0276)."""


@dataclass(frozen=True)
class Inheritance:
    """Where a project's schema came from: the schema source's namespace, the
    source's schema configuration as formed, and every declaration the project
    inherited from it (SR-0279)."""
    namespace: str
    source_config: dict
    paths: frozenset

    def owns(self, *path: str) -> bool:
        return tuple(path) in self.paths


def describe(path: Path) -> str:
    """A declaration's address in words, for a refusal or a report."""
    kind = path[0]
    if kind == "type":
        return f"item type '{path[1]}'"
    if kind == "type_key":
        return f"item type '{path[1]}' {path[2]}"
    if kind == "attr":
        return f"item type '{path[1]}' attribute '{path[2]}'"
    if kind == "link":
        return f"link type '{path[1]}'"
    if kind == "link_rule":
        return f"link rule for '{path[1]}'"
    if kind == "status":
        return f"status '{path[1]}'"
    if kind == "status_role":
        return f"status role '{path[1]}'"
    if kind == "transition":
        return f"transitions from '{path[1]}'"
    if kind == "ratify_role":
        return f"ratify role '{path[1]}'"
    if kind == "coverage":
        rule = json.loads(path[1])
        return (f"coverage rule: items where {rule.get('filter', '*')} need "
                f"{rule.get('needs', '?')}")
    if len(path) == 3:
        return f"grounding {path[1]} entry '{path[2]}'"
    return f"grounding {path[1]}"


def declarations(config: dict) -> dict[Path, object]:
    """Every schema declaration ``config`` makes itself, by address. Nothing is
    filled in from a default: an address is here only when the file says it."""
    out: dict[Path, object] = {}
    for tname, tbody in (config.get("types") or {}).items():
        tbody = tbody or {}
        out[("type", tname)] = True
        for key, value in tbody.items():
            if key != "attrs":
                out[("type_key", tname, key)] = value
        for aname, spec in (tbody.get("attrs") or {}).items():
            out[("attr", tname, aname)] = spec
    for name in (config.get("links") or {}).get("types") or []:
        out[("link", name)] = True
    for ltype, spec in (config.get("link_rules") or {}).items():
        out[("link_rule", ltype)] = spec
    status = config.get("status") or {}
    for value in status.get("values") or []:
        out[("status", value)] = True
    for role, value in (status.get("roles") or {}).items():
        out[("status_role", role)] = value
    for frm, tos in (config.get("transitions") or {}).items():
        out[("transition", frm)] = list(tos or [])
    for role, members in ((config.get("ratify") or {}).get("roles") or {}).items():
        out[("ratify_role", role)] = members
    for key, value in (config.get("grounding") or {}).items():
        if isinstance(value, list):
            for member in value:
                out[("grounding", key, member)] = True
        else:
            out[("grounding", key)] = value
    for rule in ((config.get("rules") or {}).get("coverage") or []):
        out[coverage_path(rule)] = True
    return out


def coverage_path(rule: dict) -> Path:
    """A coverage rule's address: the rule itself, written so that two rules saying
    the same thing have the same address."""
    return ("coverage", json.dumps(rule, sort_keys=True, ensure_ascii=False))


def _refuse(path: Path, namespace: str, here, there) -> InheritanceError:
    return InheritanceError(
        f"{describe(path)} is declared by the schema source '{namespace}' and "
        f"cannot be redefined here (SR-0276): here {here!r}, there {there!r}. "
        f"Change it in '{namespace}', or remove it from this project")


def _union(first: list, then: list) -> list:
    return list(first) + [m for m in then if m not in first]


def merge(source: dict, local: dict, namespace: str) -> tuple[dict, Inheritance]:
    """The project's schema configuration: ``source`` (the schema source's, as
    formed) with ``local``'s own declarations added. Returns it with the record of
    what was inherited. Raises :class:`InheritanceError` naming the first
    declaration ``local`` redefines (SR-0276)."""
    theirs = declarations(source)
    for path, value in declarations(local).items():
        if path in theirs:
            if value != theirs[path]:
                raise _refuse(path, namespace, value, theirs[path])
        elif path[0] == "type_key" and ("type", path[1]) in theirs:
            # Anything said about an inherited type other than a new attribute
            # changes what that type is — `normative` above all, whose absence
            # already means true there.
            raise _refuse(path, namespace, value, "not declared")

    out = copy.deepcopy(local)

    types = copy.deepcopy(source.get("types") or {})
    for tname, tbody in (local.get("types") or {}).items():
        tbody = copy.deepcopy(tbody or {})
        if tname not in types:
            types[tname] = tbody
            continue
        mine = dict(types[tname] or {})
        attrs = dict(mine.get("attrs") or {})
        attrs.update(tbody.get("attrs") or {})
        if attrs:
            mine["attrs"] = attrs
        types[tname] = mine
    if types:
        out["types"] = types

    def merged_list(table: str, key: str, default: list | None = None) -> list | None:
        there = (source.get(table) or {}).get(key)
        here = (local.get(table) or {}).get(key)
        if there is None and here is None:
            return None
        return _union(there if there is not None else list(default or []), here or [])

    links = merged_list("links", "types")
    if links is not None:
        out["links"] = {**(local.get("links") or {}), "types": links}

    rules = {**copy.deepcopy(source.get("link_rules") or {}),
             **copy.deepcopy(local.get("link_rules") or {})}
    if rules:
        out["link_rules"] = rules

    status = dict(local.get("status") or {})
    values = merged_list("status", "values")
    if values is not None:
        status["values"] = values
    roles_there = (source.get("status") or {}).get("roles")
    roles_here = (local.get("status") or {}).get("roles")
    if roles_there is not None or roles_here is not None:
        status["roles"] = {**(roles_there or {}), **(roles_here or {})}
    if status:
        out["status"] = status

    transitions = {**copy.deepcopy(source.get("transitions") or {}),
                   **copy.deepcopy(local.get("transitions") or {})}
    if transitions:
        out["transitions"] = transitions

    ratify_roles = {**copy.deepcopy((source.get("ratify") or {}).get("roles") or {}),
                    **copy.deepcopy((local.get("ratify") or {}).get("roles") or {})}
    if ratify_roles:
        out["ratify"] = {**(local.get("ratify") or {}), "roles": ratify_roles}

    # Coverage rules: the source's, then the project's own that the source does not
    # already state. A follower cannot drop one it inherits; it has no way to say so.
    theirs_rules = list(((source.get("rules") or {}).get("coverage")) or [])
    mine_rules = list(((local.get("rules") or {}).get("coverage")) or [])
    if theirs_rules:
        seen = {coverage_path(r) for r in theirs_rules}
        rules_out = dict(local.get("rules") or {})
        rules_out["coverage"] = copy.deepcopy(theirs_rules) + [
            copy.deepcopy(r) for r in mine_rules if coverage_path(r) not in seen]
        out["rules"] = rules_out

    grounding = dict(local.get("grounding") or {})
    for key in set(source.get("grounding") or {}) | set(grounding):
        there = (source.get("grounding") or {}).get(key)
        if isinstance(there, list) or isinstance(grounding.get(key), list) \
                or key in _GROUNDING_DEFAULTS:
            grounding[key] = merged_list("grounding", key, _GROUNDING_DEFAULTS.get(key))
        elif key not in grounding:
            grounding[key] = copy.deepcopy(there)
    if grounding:
        out["grounding"] = grounding

    return out, Inheritance(namespace=namespace, source_config=source,
                            paths=frozenset(theirs))


def redundant(source: dict, local: dict) -> list[Path]:
    """The declarations ``local`` makes that ``source`` already makes identically,
    in file order — what migration removes (SR-0277). A declaration that differs is
    not listed; it is left for its keeper."""
    theirs = declarations(source)
    return [path for path, value in declarations(local).items()
            if path in theirs and theirs[path] == value]
