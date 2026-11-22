# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""Moving a project's filters from the earlier language into GQL (SR-0339).

A project's filters are all in one language (SR-0337), so the move is all or
nothing: every coverage rule's filter in the configuration and every filter in
the marked regions of the documents the project configures is rewritten into the
GQL condition that selects the same items, and ``filter_language`` is set to
``gql``. Nothing is written until every filter has been rewritten and shown, on
the project's own items, to select what the original selects.

The rewriting is mechanical, because the earlier grammar is closed (SR-0104), but
the two languages differ where a property is absent: ``attrs.get('p') != 'x'``
selects an item with no ``p`` and ``item.p <> 'x'`` does not, so that one is
written ``item.p IS NULL OR item.p <> 'x'``. They differ too in what trimming
removes: ``strip()`` takes every kind of white space from both ends, and TRIM, as
the standard gives it with no trim character, takes spaces only, so ``strip()``
is written TRIM and selects differently where a text is edged with a tab or a
line break. The check on the project's items stops the move where that, or a
difference this module did not foresee, changes what a filter selects.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from .filters import FilterError, _parse as _parse_python
from .gql import check_condition
from .graph import Index
from .inject import _BLOCK, _MATRIX_REL, document_paths
from .schema import GQL, PYTHON
from .atomic import write_all
from .storage import (CONFIG_NAME, FORMAT_VERSION, ProjectError, load_project,
                      read_text)
from .tomledit import TomlDocument, render_assignment
from .validate import eval_filter

_FIELDS = ("uid", "type", "status", "register", "title", "text", "rationale",
           "normative", "derived")
_STANDING = ("signed", "stale", "retired", "reviewed", "signers")
_BOOLEAN = {"normative", "derived", "signed", "stale", "retired", "reviewed"}
_TEXT = {"uid", "type", "status", "register", "title", "text", "rationale"}
_PROPERTY = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")
_COMPARISON = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">="}

# The directives whose whole argument is a filter, and those that put something
# before it. A directive named in neither is one this module cannot find the
# filter of, and the move is refused naming it.
_WHOLE = frozenset({"table", "catalog", "count", "unused", "stats", "sourced"})
_NO_FILTER = frozenset({"item"})


class Untranslatable(FilterError):
    """A filter of the earlier language has no GQL form this module can write."""


def _quote(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "''") + "'"


def _literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, (int, float)):
        return repr(value)
    raise Untranslatable(f"the value {value!r} has no GQL form in a filter")


class _Translator:
    """One filter of the earlier language, as the GQL condition that selects the
    same items. ``lists`` names the attributes that hold lists in the project, so
    ``'x' in attrs.get('tags')`` is written as membership and not as text."""

    def __init__(self, lists: frozenset[str] = frozenset()):
        self.lists = lists

    # ------------------------------------------------------------ properties
    def attribute(self, name) -> str:
        if not isinstance(name, str) or not _PROPERTY.match(name):
            raise Untranslatable(
                f"the attribute {name!r} cannot be written as a GQL property name")
        if name in _FIELDS or name in _STANDING or name == "attrs":
            return f"item.attrs.{name}"
        return f"item.{name}"

    def attribute_of(self, node) -> tuple[str, str] | None:
        """``(name, default or None)`` where ``node`` reads one attribute:
        ``attrs['k']``, ``attrs.get('k')`` or ``attrs.get('k', d)``."""
        if isinstance(node, ast.Subscript) and self._is_attrs(node.value) \
                and isinstance(node.slice, ast.Constant):
            return node.slice.value, None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "get" and self._is_attrs(node.func.value) \
                and node.args and isinstance(node.args[0], ast.Constant):
            default = self.value(node.args[1]) if len(node.args) > 1 else None
            return node.args[0].value, default
        return None

    @staticmethod
    def _is_attrs(node) -> bool:
        return isinstance(node, ast.Name) and node.id == "attrs"

    def nullable(self, node) -> bool:
        """Whether ``node`` may be absent: an attribute read with no default."""
        read = self.attribute_of(node)
        return read is not None and read[1] is None

    def is_none(self, node) -> bool:
        return (isinstance(node, ast.Name) and node.id == "none") or (
            isinstance(node, ast.Constant) and node.value is None)

    def textual(self, node) -> bool:
        """Whether ``node`` is text for certain: a text field, a string, or a
        string method's result."""
        if isinstance(node, ast.Name):
            return node.id in _TEXT
        if isinstance(node, ast.Constant):
            return isinstance(node.value, str)
        return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("lower", "upper", "strip"))

    # ---------------------------------------------------------------- values
    def value(self, node) -> str:
        if isinstance(node, ast.Constant):
            return _literal(node.value)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)) \
                and isinstance(node.operand, ast.Constant):
            sign = -1 if isinstance(node.op, ast.USub) else 1
            return _literal(sign * node.operand.value)
        if isinstance(node, ast.Name):
            if node.id in ("true", "false", "none"):
                return {"true": "TRUE", "false": "FALSE", "none": "NULL"}[node.id]
            if node.id in _FIELDS or node.id in _STANDING:
                return f"item.{node.id}"
            raise Untranslatable(f"the name '{node.id}' has no GQL form")
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            members = []
            for element in node.elts:
                if not isinstance(element, ast.Constant):
                    raise Untranslatable("a list in a GQL filter holds literal values")
                members.append(_literal(element.value))
            return "[" + ", ".join(members) + "]"
        read = self.attribute_of(node)
        if read is not None:
            name, default = read
            prop = self.attribute(name)
            return prop if default is None else f"COALESCE({prop}, {default})"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            method = node.func.attr
            if method in ("lower", "upper", "strip") and not node.args:
                function = {"lower": "LOWER", "upper": "UPPER", "strip": "TRIM"}[method]
                return f"{function}({self.value(node.func.value)})"
            raise Untranslatable(f"the method '{method}' has no GQL form as a value")
        raise Untranslatable(
            f"'{ast.unparse(node)}' has no GQL form this migration can write")

    # ------------------------------------------------------------ conditions
    def condition(self, node) -> str:
        if isinstance(node, ast.BoolOp):
            word = " AND " if isinstance(node.op, ast.And) else " OR "
            parts = []
            for operand in node.values:
                text = self.condition(operand)
                # More parentheses than the grammar needs, never fewer: anything
                # made of more than one condition is closed off.
                compound = isinstance(operand, ast.BoolOp) or (
                    isinstance(operand, ast.Compare) and len(operand.ops) > 1)
                parts.append(f"({text})" if compound else text)
            return word.join(parts)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return f"NOT ({self.condition(node.operand)})"
        if isinstance(node, ast.Compare):
            pairs, left = [], node.left
            for op, right in zip(node.ops, node.comparators):
                pairs.append(self.comparison(left, op, right))
                left = right
            return " AND ".join(pairs)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            return self.call(node)
        if isinstance(node, ast.Name):
            if node.id in _BOOLEAN:
                return f"item.{node.id}"
            if node.id in ("true", "false"):
                return node.id.upper()
            if node.id == "none":
                return "FALSE"
            if node.id in _TEXT:
                return f"item.{node.id} <> ''"
            if node.id == "signers":
                return "SIZE(item.signers) > 0"
        if isinstance(node, ast.Constant) and isinstance(node.value, bool):
            return "TRUE" if node.value else "FALSE"
        read = self.attribute_of(node)
        if read is not None and read[1] is None:
            # Used as a truth value: held at all. An empty or false value is
            # where this differs, and the check on the project's items says so.
            return f"{self.attribute(read[0])} IS NOT NULL"
        raise Untranslatable(
            f"'{ast.unparse(node)}' used as a condition has no GQL form this "
            "migration can write")

    def comparison(self, left, op, right) -> str:
        if isinstance(op, (ast.Eq, ast.NotEq)):
            wanted = isinstance(op, ast.Eq)
            for side, other in ((left, right), (right, left)):
                if self.is_none(side):
                    return f"{self.value(other)} IS {'' if wanted else 'NOT '}NULL"
            a, b = self.value(left), self.value(right)
            if wanted:
                return f"{a} = {b}"
            absent = [x for x, n in ((a, left), (b, right)) if self.nullable(n)]
            if absent:
                return "(" + " OR ".join([f"{x} IS NULL" for x in absent]
                                         + [f"{a} <> {b}"]) + ")"
            return f"{a} <> {b}"
        if type(op) in _COMPARISON:
            return f"{self.value(left)} {_COMPARISON[type(op)]} {self.value(right)}"
        if isinstance(op, (ast.In, ast.NotIn)):
            return self.membership(left, right, isinstance(op, ast.NotIn))
        raise Untranslatable(f"the comparison '{type(op).__name__}' has no GQL form")

    def membership(self, member, container, negated: bool) -> str:
        a = self.value(member)
        read = self.attribute_of(container)
        as_text = self.textual(container) or (
            read is not None and read[0] not in self.lists)
        if isinstance(container, (ast.List, ast.Tuple, ast.Set)) or (
                read is not None and read[0] in self.lists) or (
                isinstance(container, ast.Name) and container.id == "signers"):
            b = self.value(container)
            if not negated:
                return f"{a} IN {b}"
            if self.nullable(member):
                return f"({a} IS NULL OR {a} NOT IN {b})"
            return f"{a} NOT IN {b}"
        if as_text:
            b = self.value(container)
            return f"NOT ({b} CONTAINS {a})" if negated else f"{b} CONTAINS {a}"
        raise Untranslatable(
            f"'{ast.unparse(container)}' is neither a list nor text for certain, "
            "so membership in it has no one GQL form")

    def call(self, node) -> str:
        method, receiver = node.func.attr, node.func.value
        if isinstance(receiver, ast.Name) and receiver.id == "links":
            return self.link(method, node.args)
        if method in ("startswith", "endswith") and len(node.args) == 1 \
                and isinstance(node.args[0], ast.Constant):
            word = "STARTS WITH" if method == "startswith" else "ENDS WITH"
            return f"{self.value(receiver)} {word} {self.value(node.args[0])}"
        read = self.attribute_of(node)
        if read is not None:
            return self.condition_of_attribute(read)
        raise Untranslatable(f"the method '{method}' has no GQL form in a filter")

    def condition_of_attribute(self, read) -> str:
        name, default = read
        if default is None:
            return f"{self.attribute(name)} IS NOT NULL"
        raise Untranslatable(
            f"attrs.get({name!r}, …) used as a condition has no one GQL form")

    def link(self, predicate: str, args) -> str:
        names = [a.value for a in args if isinstance(a, ast.Constant)]
        if len(names) != len(args) or len(names) > 1:
            raise Untranslatable(f"links.{predicate} takes one literal, or nothing")
        if predicate == "to":
            if not names:
                raise Untranslatable("links.to needs a target")
            target = _quote(str(names[0]))
            return ("EXISTS { (item)-[]->(t WHERE t.uid = " + target
                    + " OR t.target = " + target + ") }")
        if predicate not in ("outgoing", "incoming"):
            raise Untranslatable(f"links.{predicate} has no GQL form")
        kind = ""
        if names and names[0] is not None:
            if not isinstance(names[0], str) or not _PROPERTY.match(names[0]):
                raise Untranslatable(
                    f"the link type {names[0]!r} cannot be written as a GQL label")
            kind = f":{names[0]}"
        return ("EXISTS { (item)-[" + kind + "]->() }" if predicate == "outgoing"
                else "EXISTS { (item)<-[" + kind + "]-() }")


def translate(expr: str, lists: frozenset[str] = frozenset()) -> str:
    """The GQL condition that selects what the earlier-language filter ``expr``
    selects (SR-0339). Raises :class:`Untranslatable`, naming the construct, where
    it has no form this module can write, and :class:`FilterError` where ``expr``
    is not a filter of the earlier language at all."""
    if not expr or not expr.strip():
        return ""
    out = _Translator(lists).condition(_parse_python(expr))
    check_condition(out)
    return out


# ------------------------------------------------------------------ the move

@dataclass
class Rewrite:
    """One filter and what it becomes: where it is, the two texts."""
    where: str            # "coverage rule #2", or "docs/overview.md: tl:table"
    old: str
    new: str


@dataclass
class Move:
    """What moving a project to GQL filters would write, or why it will not."""
    root: Path
    rewrites: list[Rewrite] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)
    already: bool = False
    config: str | None = None
    documents: dict[Path, str] = field(default_factory=dict)


def filter_of(kind: str, arg: str) -> str | None:
    """The filter inside a directive's argument, exactly as written there, or
    ``None`` where the directive takes none. Raises :class:`Untranslatable` for a
    directive this module does not know the argument of."""
    arg = arg.strip()
    if kind in _NO_FILTER:
        return None
    if kind in _WHOLE:
        return arg or None
    if kind == "graph":
        m = re.match(r"^collapse-external\b\s*(.*)$", arg, re.DOTALL)
        return (m.group(1).strip() if m else arg) or None
    if kind == "chart":
        m = re.match(r"^(\S+)\s*(.*)$", arg, re.DOTALL)
        return (m.group(2).strip() if m else "") or None
    if kind == "matrix":
        m = _MATRIX_REL.match(arg)
        return (m.group(5).strip() if m else arg) or None
    raise Untranslatable(
        f"tl:{kind} is not one of the Tool's own directives, so where its filter "
        "sits in its argument is not known")


def _selection(project, idx, expr: str, language: str) -> list[str] | str:
    """The UIDs ``expr`` selects among the project's items, or the refusal that
    evaluating it raised."""
    try:
        return sorted(it.uid for it in project.items()
                      if eval_filter(it, expr, idx, project.schema, language))
    except FilterError as e:
        return f"could not be evaluated: {e}"


def plan_move(path) -> Move:
    """Work out the whole move to GQL filters without writing anything (SR-0339).
    Raises ``ProjectError`` where the project cannot be loaded."""
    root = Path(path)
    project = load_project(root)
    move = Move(root=root)
    if project.filter_language == GQL:
        move.already = True
        return move
    if project.inheritance is not None and (
            (project.inheritance.source_config.get("rules") or {}).get("coverage")):
        move.refusals.append(
            f"it takes coverage rules from its schema source "
            f"'{project.inheritance.namespace}', whose filters are in the earlier "
            "language — move that graph first, then this one")
        return move
    idx = Index.build(project)
    lists = frozenset(
        name for it in project.items() for name, value in it.attrs.items()
        if isinstance(value, (list, tuple))) | frozenset(
        name for attrs in project.schema.types.values()
        for name, spec in attrs.items() if spec.list)

    def rewrite(where: str, old: str) -> str | None:
        try:
            new = translate(old, lists)
        except FilterError as e:
            move.refusals.append(f"{where}: {e}\n    filter = {old}")
            return None
        before = _selection(project, idx, old, PYTHON)
        after = _selection(project, idx, new, GQL)
        if before != after:
            detail = (after if isinstance(after, str) else before if isinstance(before, str)
                      else "it selects " + (", ".join(sorted(set(after) ^ set(before))[:8])
                                            or "the same items in another order")
                      + " differently")
            move.refusals.append(
                f"{where}: rewritten, the filter does not select what it did "
                f"({detail})\n    filter = {old}\n    as GQL = {new}")
            return None
        move.rewrites.append(Rewrite(where, old, new))
        return new

    # The configuration: each coverage rule the project itself declares.
    cfg_file = root / CONFIG_NAME
    doc = TomlDocument(cfg_file.read_text(encoding="utf-8"))
    own_rules = list((project.config.get("rules") or {}).get("coverage") or [])
    spans = doc.array_table_spans(("rules", "coverage"))
    for number, (rule, (start, end)) in enumerate(zip(own_rules, spans), start=1):
        old = rule.get("filter") or ""
        if not old.strip():
            continue
        new = rewrite(f"coverage rule #{number}", old)
        if new is None:
            continue
        for line in range(start + 1, end):
            if re.match(r"\s*filter\s*=", doc._lines[line]):
                doc._lines[line:line + 1] = render_assignment("filter", new)
                break
        else:
            move.refusals.append(
                f"coverage rule #{number}: its filter is not written on a line of "
                f"its own in {cfg_file}, so it is not rewritten here")
    doc.set_key("project", "filter_language", GQL, because=(
        "The language this project's filters are in, in its coverage rules and "
        "its documents (SR-0337): a GQL search condition over the item. Moved "
        "from the earlier language by `tl migrate --to-gql` (SR-0339)."))
    # With the language, the format version that says so (SR-0346): a tl from
    # before GQL filters refuses the project instead of misreading it.
    doc.set_key("project", "format_version", FORMAT_VERSION)
    move.config = doc.text()

    # The documents: every marked region whose directive takes a filter.
    for path_ in document_paths(project):
        text = read_text(path_, "a document")
        shown = path_.relative_to(root).as_posix() if path_.is_relative_to(root) \
            else str(path_)

        def replace(m: re.Match) -> str:
            kind = m.group("kind").lower()
            try:
                old = filter_of(kind, m.group("arg"))
            except Untranslatable as e:
                move.refusals.append(f"{shown}: {e}")
                return m.group(0)
            if old is None:
                return m.group(0)
            new = rewrite(f"{shown}: tl:{kind}", old)
            if new is None:
                return m.group(0)
            opener = m.group("open")
            at = opener.rindex(old)
            return (opener[:at] + new + opener[at + len(old):]
                    + m.group("body") + m.group("close"))

        rewritten = _BLOCK.sub(replace, text)
        if rewritten != text:
            move.documents[path_] = rewritten
    return move


def apply_move(move: Move) -> None:
    """Write a planned move (SR-0339). Refused where the plan holds a refusal:
    a project is in one language, so it is all or nothing."""
    if move.refusals:
        raise ProjectError("the move to GQL filters was refused; nothing is written")
    if move.already or move.config is None:
        return
    # The configuration and every document, or none of them (SR-0356).
    try:
        write_all({move.root / CONFIG_NAME: move.config, **move.documents})
    except OSError as e:
        raise ProjectError(f"cannot write {e.filename}: {e.strerror} — nothing "
                           "was written") from e


def filters_in_earlier_language(path) -> int:
    """How many filters a project still holds in the earlier language: 0 where it
    is at GQL or holds none. For the advice `tl migrate` gives (SR-0339)."""
    try:
        project = load_project(Path(path))
    except ProjectError:
        return 0
    if project.filter_language == GQL:
        return 0
    count = sum(1 for r in ((project.config.get("rules") or {}).get("coverage") or [])
                if (r.get("filter") or "").strip())
    for path_ in document_paths(project):
        try:
            text = path_.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue                 # `tl docs` reports a document it cannot read
        for m in _BLOCK.finditer(text):
            try:
                count += filter_of(m.group("kind").lower(), m.group("arg")) is not None
            except Untranslatable:
                count += 1
    return count


__all__ = ["Move", "Rewrite", "Untranslatable", "apply_move", "filter_of",
           "filters_in_earlier_language", "plan_move", "translate"]
