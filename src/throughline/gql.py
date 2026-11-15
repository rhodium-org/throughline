# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A filter as a GQL search condition over one item (SR-0336, SR-0338, SR-0340).

Where a project declares GQL as its filter language (SR-0337), a filter is a
search condition of ISO/IEC 39075:2024 in which the item under test is bound to
the variable ``item``:

    item.type = 'requirement' AND item.status <> 'deleted'
    item.priority IN ['must', 'should']
    EXISTS { (item)<-[:verifies]-() }

It is the language the engines built to throughline's format take, so a filter
means the same wherever a graph is read. Only a condition is taken, never a whole
statement: a filter says which items, and the Tool says what is done with them.

The text is read by the tokenizer and parser below and evaluated by walking what
they build. Nothing of a filter reaches ``eval``, ``exec``, a shell, a database or
any other interpreter (SR-0340): filters come from documents, from configuration
and from graphs composed from other people, so they are untrusted input.

A condition has three values, as the standard's has: true, false and unknown. A
property the item does not hold is null, a comparison with null is unknown, and a
filter selects an item only where its condition is true.
"""
from __future__ import annotations

import functools
import re

from .filters import FilterError

# The item's own fields, as properties (SR-0338). Its standing (SR-0335) and its
# attributes are read through the namespace `validate` builds, so a filter and
# the gate judge an item by the same functions.
_FIELDS = frozenset({"uid", "type", "status", "register", "title", "text",
                     "rationale", "normative", "derived"})
_STANDING = frozenset({"signed", "stale", "retired", "reviewed", "signers"})
_FUNCTIONS = ("LOWER", "UPPER", "TRIM", "COALESCE", "SIZE")
_KEYWORDS = frozenset({"AND", "OR", "NOT", "IS", "NULL", "IN", "CONTAINS", "STARTS",
                       "ENDS", "WITH", "EXISTS", "TRUE", "FALSE", "WHERE"})
ITEM = "item"

_TOKEN = re.compile(r"""
      (?P<space>\s+)
    | (?P<number>[0-9]+(?:\.[0-9]+)?)
    | (?P<name>[A-Za-z][A-Za-z0-9_]*)
    | (?P<symbol><>|<=|>=|[=<>()\[\]{},.:\-])
""", re.VERBOSE)
# The standard's <escaped character>, and the hex digits its two Unicode forms take.
_ESCAPES = {"\\": "\\", "'": "'", '"': '"', "`": "`", "t": "\t", "b": "\b",
            "n": "\n", "r": "\r", "f": "\f"}
_UNICODE = {"u": 4, "U": 6}
_TEXT_FIELDS = frozenset({"uid", "type", "status", "register", "title", "text",
                          "rationale"})
_TEXT_FUNCTIONS = frozenset({"LOWER", "UPPER", "TRIM"})


def _refuse(message: str, expr: str, at: int | None = None) -> FilterError:
    where = f" at character {at + 1}" if at is not None else ""
    return FilterError(f"{message}{where} in the filter: {expr.strip()}")


def _tokens(expr: str) -> list[tuple[str, object, int]]:
    """``expr`` as ``(kind, value, position)`` triples, ending with ``end``."""
    out: list[tuple[str, object, int]] = []
    i = 0
    while i < len(expr):
        two = expr[i:i + 2]
        if two == "==":
            raise _refuse("'==' is not an operator here: equality is written with "
                          "one sign, as in item.type = 'requirement'", expr, i)
        if two == "!=":
            raise _refuse("'!=' is not an operator here: not equal is written <>, "
                          "as in item.status <> 'deleted'", expr, i)
        ch = expr[i]
        if ch in "'\"":
            text, i = _string(expr, i)
            out.append(("string", text, i))
            continue
        m = _TOKEN.match(expr, i)
        if m is None:
            raise _refuse(f"unexpected character {ch!r}", expr, i)
        kind = m.lastgroup
        if kind == "number":
            end = m.end()
            if end < len(expr) and (expr[end].isalpha() or expr[end] == "_"):
                raise _refuse(f"unsupported number {expr[i:end + 1]}…: a number is "
                              "an integer or a decimal written in digits", expr, i)
            raw = m.group()
            out.append(("number", float(raw) if "." in raw else int(raw), i))
        elif kind == "name":
            word = m.group()
            if word.upper() in _KEYWORDS:
                out.append(("keyword", word.upper(), i))
            else:
                out.append(("name", word, i))
        elif kind == "symbol":
            out.append(("symbol", m.group(), i))
        i = m.end()
    out.append(("end", None, len(expr)))
    return out


def _string(expr: str, start: int) -> tuple[str, int]:
    """The character string literal opening at ``start`` and the index after it.
    A quote is written inside its own kind by doubling it or after a backslash."""
    quote = expr[start]
    out: list[str] = []
    i = start + 1
    while i < len(expr):
        ch = expr[i]
        if ch == "\\" and i + 1 < len(expr):
            char, i = _escaped(expr, i)
            out.append(char)
        elif ch == quote:
            if expr[i + 1:i + 2] == quote:
                out.append(quote)
                i += 2
            else:
                return "".join(out), i + 1
        else:
            out.append(ch)
            i += 1
    raise _refuse("a character string is not closed", expr, start)


def _escaped(expr: str, at: int) -> tuple[str, int]:
    """The character the backslash sequence at ``at`` stands for and the index
    after it. Any sequence the standard does not give is refused by name: read
    as some other character, it would make a filter mean what nobody wrote."""
    mark = expr[at + 1]
    if mark in _ESCAPES:
        return _ESCAPES[mark], at + 2
    digits = _UNICODE.get(mark)
    if digits:
        code = expr[at + 2:at + 2 + digits]
        if len(code) == digits and all(c in "0123456789abcdefABCDEF" for c in code) \
                and int(code, 16) <= 0x10FFFF:
            return chr(int(code, 16)), at + 2 + digits
        raise _refuse(f"'\\{mark}' in a character string is followed by {digits} "
                      "hexadecimal digits naming a character, as in "
                      + ("'\\u0041'" if mark == "u" else "'\\U01F600'"), expr, at)
    raise _refuse(f"'\\{mark}' is not an escaped character: those are \\\\ \\' \\\" "
                  "\\` \\t \\b \\n \\r \\f, \\uXXXX and \\UXXXXXX", expr, at)


def _is_text(node) -> bool:
    """Whether ``node`` is a text for certain, whatever the item: a character
    string, a field that is always text, or a function that gives one."""
    return (node[0] == "lit" and isinstance(node[1], str)) \
        or (node[0] == "prop" and len(node[2]) == 1 and node[2][0] in _TEXT_FIELDS) \
        or (node[0] == "func" and node[1] in _TEXT_FUNCTIONS)


class _Parser:
    """Recursive descent over the constructs SR-0336 lists and no others."""

    def __init__(self, expr: str):
        self.expr = expr
        self.toks = _tokens(expr)
        self.pos = 0
        self.other: str | None = None      # the variable an edge pattern binds

    # ------------------------------------------------------------- plumbing
    def peek(self, ahead: int = 0) -> tuple[str, object, int]:
        return self.toks[min(self.pos + ahead, len(self.toks) - 1)]

    def take(self) -> tuple[str, object, int]:
        tok = self.toks[self.pos]
        self.pos += 1
        return tok

    def at(self, kind: str, value: object = None, ahead: int = 0) -> bool:
        k, v, _ = self.peek(ahead)
        return k == kind and (value is None or v == value)

    def accept(self, kind: str, value: object = None) -> bool:
        if self.at(kind, value):
            self.pos += 1
            return True
        return False

    def expect(self, kind: str, value: object, what: str) -> None:
        if not self.accept(kind, value):
            raise self.fail(f"expected {what}")

    def fail(self, message: str) -> FilterError:
        kind, value, at = self.peek()
        found = "the end" if kind == "end" else repr(str(value))
        return _refuse(f"{message}, found {found}", self.expr, at)

    # -------------------------------------------------------------- grammar
    def condition(self):
        node = self.conjunction()
        while self.accept("keyword", "OR"):
            node = ("or", node, self.conjunction())
        return node

    def conjunction(self):
        node = self.negation()
        while self.accept("keyword", "AND"):
            node = ("and", node, self.negation())
        return node

    def negation(self):
        if self.accept("keyword", "NOT"):
            return ("not", self.negation())
        return self.predicate()

    def predicate(self):
        if self.accept("keyword", "EXISTS"):
            return self.exists()
        left = self.value()
        if self.at("symbol") and self.peek()[1] in ("=", "<>", "<", "<=", ">", ">="):
            op = self.take()[1]
            return ("cmp", op, left, self.value())
        if self.accept("keyword", "IS"):
            negated = self.accept("keyword", "NOT")
            self.expect("keyword", "NULL", "NULL after IS")
            return ("isnull", left, negated)
        negated = False
        if self.at("keyword", "NOT") and self.at("keyword", "IN", ahead=1):
            self.take()
            negated = True
        if self.accept("keyword", "IN"):
            right = self.value()
            if right[0] not in ("list", "prop", "func"):
                raise _refuse("IN takes a list, written in square brackets, as in "
                              "item.status IN ['proposed', 'ratified']",
                              self.expr, self.peek()[2])
            return ("in", left, right, negated)
        if self.accept("keyword", "CONTAINS"):
            return ("text", "contains", left, self.value())
        if self.accept("keyword", "STARTS"):
            self.expect("keyword", "WITH", "WITH after STARTS")
            return ("text", "starts", left, self.value())
        if self.accept("keyword", "ENDS"):
            self.expect("keyword", "WITH", "WITH after ENDS")
            return ("text", "ends", left, self.value())
        return left

    def value(self):
        kind, value, at = self.peek()
        if kind == "string":
            self.take()
            return ("lit", value)
        if kind == "number":
            self.take()
            return ("lit", value)
        if kind == "symbol" and value == "-" and self.at("number", ahead=1):
            self.take()
            return ("lit", -self.take()[1])
        if kind == "keyword" and value in ("TRUE", "FALSE", "NULL"):
            self.take()
            return ("lit", {"TRUE": True, "FALSE": False, "NULL": None}[value])
        if kind == "symbol" and value == "[":
            self.take()
            members = []
            if not self.accept("symbol", "]"):
                while True:
                    member = self.value()
                    if member[0] != "lit":
                        raise _refuse("a list holds literal values", self.expr, at)
                    members.append(member[1])
                    if self.accept("symbol", "]"):
                        break
                    self.expect("symbol", ",", "a comma or ] in a list")
            return ("list", members)
        if kind == "symbol" and value == "(":
            self.take()
            node = self.condition()
            self.expect("symbol", ")", "a closing parenthesis")
            return node
        if kind == "name":
            return self.named()
        raise self.fail("expected a value")

    def named(self):
        _, word, at = self.take()
        if self.at("symbol", "("):
            name = word.upper()
            if name not in _FUNCTIONS:
                raise _refuse(f"{word} is not a function a filter may call: the "
                              f"functions are {', '.join(_FUNCTIONS)}", self.expr, at)
            self.take()
            args = [self.value()]
            while self.accept("symbol", ","):
                args.append(self.value())
            self.expect("symbol", ")", f"a closing parenthesis after {name}'s arguments")
            if name != "COALESCE" and len(args) != 1:
                raise _refuse(f"{name} takes one argument", self.expr, at)
            if name == "SIZE" and _is_text(args[0]):
                raise _refuse("SIZE is the number of members of a list, and this "
                              "is a text: a filter has no function for a text's "
                              "length", self.expr, at)
            return ("func", name, args)
        if not self.at("symbol", "."):
            if word == ITEM or word == self.other:
                raise _refuse(f"'{word}' is an item, not a value: name one of its "
                              f"properties, as in {word}.type", self.expr, at)
            raise _refuse(f"bare name '{word}': the item is written item, as in "
                          f"item.{word}", self.expr, at)
        if word != ITEM and word != self.other:
            raise _refuse(f"unknown variable '{word}': the item under test is "
                          "written item", self.expr, at)
        path = []
        while self.accept("symbol", "."):
            kind, name, where = self.take()
            if kind not in ("name", "keyword"):
                raise _refuse("expected a property name after the dot", self.expr, where)
            path.append(str(name) if kind == "name" else str(name).lower())
        if self.at("symbol", "("):
            raise _refuse(f"'{'.'.join([word, *path])}(…)' is a method call, which "
                          "a filter does not have: the functions are "
                          f"{', '.join(_FUNCTIONS)}, written as in LOWER(item.title)",
                          self.expr, at)
        if len(path) > 2 or (len(path) == 2 and path[0] != "attrs") \
                or path == ["attrs"]:
            raise _refuse(f"'{'.'.join([word, *path])}' is not a property: write "
                          "item.<name>, or item.attrs.<name> for an attribute named "
                          "like a field", self.expr, at)
        return ("prop", word, path)

    def exists(self):
        """``EXISTS { (item)-[:type]->(other WHERE …) }`` and its incoming form:
        one edge, from or to the item, whose other end may carry one condition."""
        _, _, at = self.peek()
        self.expect("symbol", "{", "{ after EXISTS")
        self.expect("symbol", "(", "(item) to open the pattern")
        if not self.accept("name", ITEM):
            raise self.fail("a pattern starts at the item, written (item)")
        self.expect("symbol", ")", "a closing parenthesis after item")
        if self.accept("symbol", "<"):
            direction = "in"
            self.expect("symbol", "-", "<-[ or <-- for an incoming link")
            link_type = self.edge_body()
            self.expect("symbol", "-", "a closing - on the incoming link")
        else:
            direction = "out"
            self.expect("symbol", "-", "-[ or --> or <-[ after (item)")
            link_type = self.edge_body()
            self.expect("symbol", "-", "-> to close the link")
            self.expect("symbol", ">", "-> to close the link")
        self.expect("symbol", "(", "( for the other end of the link")
        name, condition = None, None
        if self.at("name"):
            _, name, where = self.take()
            if name == ITEM:
                raise _refuse("the other end of the link needs a name of its own",
                              self.expr, where)
        if self.accept("keyword", "WHERE"):
            if name is None:
                raise self.fail("a condition on the other end needs a name for it, "
                                "as in (t WHERE t.uid = 'X')")
            self.other = name
            condition = self.condition()
            self.other = None
        self.expect("symbol", ")", "a closing parenthesis on the other end")
        if not self.accept("symbol", "}"):
            raise _refuse("a pattern is one link from or to the item, then }",
                          self.expr, self.peek()[2])
        return ("exists", direction, link_type, name, condition)

    def edge_body(self) -> str | None:
        """What stands between the dashes: nothing, ``[]`` or ``[:type]``."""
        if not self.accept("symbol", "["):
            return None
        link_type = None
        if self.accept("symbol", ":"):
            kind, name, where = self.take()
            if kind != "name":
                raise _refuse("expected a link type after the colon", self.expr, where)
            link_type = str(name)
        self.expect("symbol", "]", "] to close the link (one link type, or none)")
        return link_type


@functools.lru_cache(maxsize=512)
def _parse(expr: str):
    parser = _Parser(expr)
    node = parser.condition()
    if not parser.at("end"):
        raise parser.fail("unexpected text after the condition")
    return node


def check_condition(expr: str) -> None:
    """Refuse ``expr`` where it is not a condition of the grammar (SR-0336),
    without evaluating it. An empty condition selects everything."""
    if expr and expr.strip():
        _parse(expr)


# ---------------------------------------------------------------- evaluation

class _Node:
    """One end of a link as a filter reads it (SR-0338): an item, with its fields,
    standing and attributes as properties, or a link target that is no item, whose
    one property is ``target``."""

    def __init__(self, item, target: str | None, idx, schema):
        self.item, self.target, self.idx, self.schema = item, target, idx, schema
        self._names = None

    def prop(self, path: list[str]):
        if self.item is None:
            return self.target if path == ["target"] else None
        if len(path) == 2:                       # item.attrs.<name>
            return self.item.attrs.get(path[1])
        name = path[0]
        if name in _FIELDS or name in _STANDING:
            if self._names is None:
                from .validate import _filter_namespace
                self._names = _filter_namespace(self.item, self.idx, self.schema)
            return self._names[name]
        return self.item.attrs.get(name)


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _compare(op: str, a, b):
    if a is None or b is None:
        return None
    alike = (_number(a) and _number(b)) or (
        type(a) is type(b) and not _number(a)) or (
        isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)))
    if op == "=":
        return (list(a) == list(b) if isinstance(a, (list, tuple)) else a == b) \
            if alike else False
    if op == "<>":
        return (list(a) != list(b) if isinstance(a, (list, tuple)) else a != b) \
            if alike else True
    if not alike or not (_number(a) or isinstance(a, str)):
        return None
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


def _truth(value):
    """A value as a condition: true, false, or unknown for anything else."""
    return value if isinstance(value, bool) else None


def _eval(node, env: dict):
    kind = node[0]
    if kind == "lit":
        return node[1]
    if kind == "list":
        return list(node[1])
    if kind == "prop":
        return env[node[1]].prop(node[2])
    if kind == "or":
        a = _truth(_eval(node[1], env))
        if a is True:
            return True
        b = _truth(_eval(node[2], env))
        return True if b is True else (None if a is None or b is None else False)
    if kind == "and":
        a = _truth(_eval(node[1], env))
        if a is False:
            return False
        b = _truth(_eval(node[2], env))
        return False if b is False else (None if a is None or b is None else True)
    if kind == "not":
        a = _truth(_eval(node[1], env))
        return None if a is None else not a
    if kind == "cmp":
        return _compare(node[1], _eval(node[2], env), _eval(node[3], env))
    if kind == "isnull":
        absent = _eval(node[1], env) is None
        return not absent if node[2] else absent
    if kind == "in":
        value, members = _eval(node[1], env), _eval(node[2], env)
        if value is None or not isinstance(members, (list, tuple)):
            return None
        found = any(_compare("=", value, m) is True for m in members)
        result = True if found else (None if any(m is None for m in members) else False)
        return (None if result is None else not result) if node[3] else result
    if kind == "text":
        a, b = _eval(node[2], env), _eval(node[3], env)
        if not isinstance(a, str) or not isinstance(b, str):
            return None
        return {"contains": b in a, "starts": a.startswith(b),
                "ends": a.endswith(b)}[node[1]]
    if kind == "func":
        return _call(node[1], [_eval(a, env) for a in node[2]])
    if kind == "exists":
        return _exists(node, env)
    raise FilterError(f"unsupported construct '{kind}'")   # pragma: no cover


def _call(name: str, args: list):
    if name == "COALESCE":
        return next((a for a in args if a is not None), None)
    value = args[0]
    if name == "SIZE":
        return len(value) if isinstance(value, (list, tuple)) else None
    if not isinstance(value, str):
        return None
    if name == "TRIM":      # no trim character given: spaces, and only spaces
        return value.strip(" ")
    return value.lower() if name == "LOWER" else value.upper()


def _exists(node, env: dict) -> bool:
    _, direction, link_type, name, condition = node
    here = env[ITEM]
    if here.item is None:
        return False
    idx, schema = here.idx, here.schema
    if direction == "out":
        ends = [(link.target, link.type) for link in here.item.links]
    else:
        if idx is None:
            raise FilterError("incoming links are unavailable in this context")
        ends = idx.in_links(here.item.uid)
    for other, kind in ends:
        if link_type is not None and kind != link_type:
            continue
        if condition is None:
            return True
        found = idx.items.get(other) if idx is not None else None
        end = _Node(found, None if found is not None else other, idx, schema)
        if _truth(_eval(condition, {**env, name: end})) is True:
            return True
    return False


def evaluate(expr: str, item, idx=None, schema=None):
    """The value of the condition ``expr`` for ``item``: ``True``, ``False``, or
    ``None`` where it is unknown (SR-0336). A filter selects an item only where
    this is ``True``. ``idx`` supplies the links for an edge pattern and ``schema``
    the project's schema, or a function that returns it, for the item's standing.
    Raises :class:`FilterError` for a condition outside the grammar."""
    if not expr or not expr.strip():
        return True
    return _truth(_eval(_parse(expr), {ITEM: _Node(item, None, idx, schema)}))


__all__ = ["check_condition", "evaluate", "ITEM"]
