# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""A standard's shape declared as items, and a follower checked against it
(UR-0044, SR-0285..SR-0299).

A standard declares the registers, types, value sets, attributes, links and link
types its followers hold as items of six kinds — ``register``, ``type``, ``enum``,
``attribute``, ``link`` and ``link_type`` — whose meaning is fixed here, at the
meta-standard version this Tool implements (SR-0285). A declaration names what it
belongs to and what it is typed by with links, never with attributes:

- an attribute ``belongs_to`` exactly one register and ``has_type`` exactly one
  type or value set;
- a type ``has_type`` exactly one base type, a value set the base type enum;
- a link declaration names the register links run ``from``, each register they run
  ``to``, and its link type ``via``;
- any declaration may say it ``aliases`` one its standard makes (SR-0299).

A declaration's ``scope`` is ``followers`` (the default: the graphs that take this
one as their schema source) or ``this-graph`` (the graph holding it: a follower's
own additions, SR-0294). A graph with no schema source that holds declarations is
its own standard: every declaration it holds applies to it, which is how the
meta-standard describes itself.

The checks run only where declarations apply, so a graph whose schema source
declares in its configuration alone, or that has none, is unchanged (SR-0293).
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from .schema import SchemaError, kind_accepts

DECLARATION_TYPES = frozenset(
    {"register", "type", "enum", "attribute", "link", "link_type"})
BASE_TYPES = ("string", "text", "int", "float", "bool", "date", "enum", "file")
_ORDERED = frozenset({"int", "float", "date"})
# The attributes tl writes itself on any item, which no declaration need name.
TOOL_ATTRS = frozenset({"origin"})
FOLLOWERS, THIS_GRAPH = "followers", "this-graph"
# The meta-standard version this Tool implements: the six kinds, the base types
# and the conditions above (SR-0285, SR-0286).
META_STANDARD_VERSION = 1
_ORIGIN_SPEC = {"type": "enum", "values": ["human", "ai", "hybrid"]}


class DeclarationError(SchemaError):
    """A declaration that cannot be read, or that collides with another (refused
    when the configuration is read)."""


# ------------------------------------------------------------- patterns (SR-0286)

# Constructs outside the subset that reads the same in Python and JavaScript, with
# why and the subset's equivalent where one exists.
_REFUSED = [
    (re.compile(r"\\d"), r"\d", "matches non-Latin digits in Python but only 0-9 in JavaScript", "[0-9]"),
    (re.compile(r"\\D"), r"\D", "is the complement of \\d, which differs between the two", "[^0-9]"),
    (re.compile(r"\\w"), r"\w", "matches non-Latin letters in Python but only ASCII in JavaScript", "[A-Za-z0-9_]"),
    (re.compile(r"\\W"), r"\W", "is the complement of \\w, which differs between the two", "[^A-Za-z0-9_]"),
    (re.compile(r"\\s"), r"\s", "matches a different set of spaces in each language", "[ \\t]"),
    (re.compile(r"\\S"), r"\S", "is the complement of \\s, which differs between the two", "[^ \\t]"),
    (re.compile(r"\\[bB]"), r"\b", "is a word boundary, whose words differ between the two", None),
    (re.compile(r"\(\?[=!<]"), "(?= (?! (?<", "is lookaround, which the subset leaves out", None),
    (re.compile(r"\(\?[a-zA-Z]"), "(?flags)", "is an inline flag, written differently in each language", None),
    (re.compile(r"\\[1-9]"), r"\1", "is a backreference, which the subset leaves out", None),
    (re.compile(r"\\[AZzG]"), r"\A \Z \z", "is an anchor only one of the two languages has", "^ or $"),
    (re.compile(r"\(\?P"), "(?P", "is a Python-only named group", "( )"),
]


def check_pattern(pattern: str) -> None:
    """Refuse a pattern outside the portable subset, naming the construct, why it
    can read differently, and its equivalent where one exists (SR-0286)."""
    for rx, construct, why, instead in _REFUSED:
        if rx.search(pattern):
            hint = f"; write {instead} instead" if instead else ""
            raise DeclarationError(
                f"pattern {pattern!r} uses {construct}, which {why}, so a value "
                f"could pass in tl and fail in the browser{hint}")
    try:
        re.compile(pattern)
    except re.error as e:
        raise DeclarationError(f"pattern {pattern!r} is not a pattern: {e}") from e


# ----------------------------------------------------------- base types (SR-0286)

def _as_date(value):
    if isinstance(value, _dt.datetime):
        return None
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            return _dt.date.fromisoformat(value)
        except ValueError:
            return None
    return None


def base_accepts(base: str, value, root: Path | None = None) -> bool:
    """Whether ``value`` is a value of ``base``, as the meta-standard's test values
    say. ``root`` is the graph's root, against which a file is resolved."""
    if base == "string":
        return isinstance(value, str) and value != "" and "\n" not in value
    if base == "text":
        return isinstance(value, str) and value != ""
    if base == "date":
        return _as_date(value) is not None
    if base == "file":
        if not isinstance(value, str) or not value or "\\" in value:
            return False
        if value.startswith("/") or ".." in Path(value).parts:
            return False
        return root is None or (root / value).is_file()
    if base == "enum":
        return isinstance(value, str)
    return kind_accepts(base, value)


def _ordered(base: str, value):
    return _as_date(value) if base == "date" else value


# ------------------------------------------------------------ conditions (SR-0289)

_CMP = re.compile(
    r"\s*([A-Z][A-Z0-9]*)\.([a-z][a-z0-9_]*)\s*"
    r"(==|!=|<=|>=|<|>|not\s+in|in)\s*"
    r"(\([^)]*\)|[^\s()]+)\s*")


@dataclass(frozen=True)
class Comparison:
    register: str
    attr: str
    op: str
    value: object          # a scalar, or a tuple for in / not in

    def __str__(self) -> str:
        v = f"({', '.join(map(str, self.value))})" if isinstance(self.value, tuple) else self.value
        return f"{self.register}.{self.attr} {self.op} {v}"


@dataclass(frozen=True)
class Condition:
    """Comparisons joined by ``and`` and ``or``: a disjunction of conjunctions."""
    text: str
    terms: tuple[tuple[Comparison, ...], ...]

    def registers(self) -> set[str]:
        return {c.register for conj in self.terms for c in conj}


def parse_condition(text: str) -> Condition:
    terms: list[tuple[Comparison, ...]] = []
    for disj in re.split(r"\s+or\s+", text.strip()):
        conj: list[Comparison] = []
        for part in re.split(r"\s+and\s+", disj):
            m = _CMP.fullmatch(part)
            if not m:
                raise DeclarationError(
                    f"condition {text!r}: {part.strip()!r} is not REGISTER.attribute "
                    "OP value (SR-0289)")
            reg, attr, op, raw = m.groups()
            op = " ".join(op.split())
            if op in ("in", "not in"):
                if not (raw.startswith("(") and raw.endswith(")")):
                    raise DeclarationError(
                        f"condition {text!r}: '{op}' takes a list in parentheses")
                value = tuple(v.strip() for v in raw[1:-1].split(",") if v.strip())
            else:
                value = raw
            conj.append(Comparison(reg, attr, op, value))
        terms.append(tuple(conj))
    return Condition(text, tuple(terms))


# --------------------------------------------------------------- declarations

@dataclass(frozen=True)
class TypeDecl:
    uid: str
    title: str
    base: str
    min: object = None
    max: object = None
    pattern: str | None = None
    values: tuple[str, ...] | None = None     # an enum's values


@dataclass(frozen=True)
class RegDecl:
    uid: str
    prefix: str
    dir: str
    holds: str
    label: str = ""
    required: bool = True
    exactly_one: bool = False
    scope: str = FOLLOWERS
    std_prefix: str | None = None             # set when this is an alias


@dataclass(frozen=True)
class AttrDecl:
    uid: str
    register: str
    name: str
    type: TypeDecl
    required: bool = False
    required_when: Condition | None = None
    absent_when: Condition | None = None
    list: bool = False
    scope: str = FOLLOWERS
    std_name: str | None = None               # set when this is an alias


@dataclass(frozen=True)
class LinkDecl:
    uid: str
    frm: str
    to: tuple[str, ...]
    via: str
    min: int | None = None
    max: int | None = None
    per_target_min: int | None = None
    when: Condition | None = None
    from_when: Condition | None = None
    scope: str = FOLLOWERS


@dataclass(frozen=True)
class LTDecl:
    uid: str
    name: str
    grounds: bool = False
    scope: str = FOLLOWERS
    std_name: str | None = None


@dataclass
class Declarations:
    """Declarations by what they name. ``by_uid`` holds every declaration item a
    graph and its schema sources hold, by UID as this graph writes it, so a link to
    one resolves without knowing which graph made it."""
    registers: dict[str, RegDecl] = field(default_factory=dict)
    attrs: dict[tuple[str, str], AttrDecl] = field(default_factory=dict)
    links: list[LinkDecl] = field(default_factory=list)
    link_types: dict[str, LTDecl] = field(default_factory=dict)
    by_uid: dict[str, object] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.registers or self.attrs or self.links or self.link_types)

    def attrs_of(self, prefix: str) -> list[AttrDecl]:
        return [a for (p, _), a in self.attrs.items() if p == prefix]


def _flag(item, key: str, default: bool) -> bool:
    v = item.attrs.get(key, default)
    if not isinstance(v, bool):
        raise DeclarationError(f"{item.uid}: '{key}' must be true or false, not {v!r}")
    return v


def _int(item, key: str):
    v = item.attrs.get(key)
    if v is None:
        return None
    if not isinstance(v, int) or isinstance(v, bool):
        raise DeclarationError(f"{item.uid}: '{key}' must be a whole number, not {v!r}")
    return v


def _scope(item) -> str:
    s = item.attrs.get("scope", FOLLOWERS)
    if s not in (FOLLOWERS, THIS_GRAPH):
        raise DeclarationError(
            f"{item.uid}: scope must be '{FOLLOWERS}' or '{THIS_GRAPH}', not {s!r}")
    return s


def _targets(item, link_type: str) -> list[str]:
    return [l.target for l in item.links if l.type == link_type]


def _one(item, link_type: str, what: str) -> str:
    found = _targets(item, link_type)
    if len(found) != 1:
        raise DeclarationError(
            f"{item.uid} ({item.type}) must name exactly one {what} by a "
            f"'{link_type}' link; it names {len(found)}")
    return found[0]


def _qualify(uid: str, namespace: str) -> str:
    return f"{namespace}:{uid}"


def build(items, *, dead: frozenset[str], source: "Declarations | None",
          source_ns: str | None) -> Declarations:
    """Every declaration ``items`` hold, resolved against ``source`` (the schema
    source's formed declarations, by its own UIDs). The result's ``by_uid`` holds
    the source's declarations qualified by ``source_ns``, and these by their own
    UIDs."""
    out = Declarations()
    if source is not None and source_ns:
        for uid, decl in source.by_uid.items():
            out.by_uid[_qualify(uid, source_ns)] = decl
            # A follower composes its source's sources transitively under their own
            # namespaces, so meta:BASE-0001 written in a follower is found too.
            if ":" in uid:
                out.by_uid.setdefault(uid, decl)
    live = [it for it in items if it.status not in dead and it.deleted is None]
    bases: dict[str, str] = {}
    for it in live:
        if it.type == "base":
            name = it.attrs.get("name")
            if name not in BASE_TYPES:
                raise DeclarationError(
                    f"{it.uid}: base type {name!r} is not one of {', '.join(BASE_TYPES)} "
                    "(SR-0286)")
            bases[it.uid] = name
            out.by_uid[it.uid] = ("base", name)

    def resolve(target: str):
        if target in out.by_uid:
            return out.by_uid[target]
        raise DeclarationError(f"declaration target {target} does not exist")

    def base_of(item) -> str:
        t = resolve(_one(item, "has_type", "base type"))
        if not (isinstance(t, tuple) and t[0] == "base"):
            raise DeclarationError(f"{item.uid}: has_type must name a base type")
        return t[1]

    # Types and value sets first: attributes name them.
    for it in live:
        if it.type == "type":
            base = base_of(it)
            lo, hi, pat = it.attrs.get("min"), it.attrs.get("max"), it.attrs.get("pattern")
            if (lo is not None or hi is not None) and base not in _ORDERED:
                raise DeclarationError(
                    f"{it.uid}: min and max apply to int, float and date, not {base}")
            if pat is not None:
                if base != "string":
                    raise DeclarationError(f"{it.uid}: pattern applies to string, not {base}")
                check_pattern(str(pat))
            for k, v in (("min", lo), ("max", hi)):
                if v is not None and not base_accepts(base, v):
                    raise DeclarationError(f"{it.uid}: {k} {v!r} is not a {base}")
            out.by_uid[it.uid] = TypeDecl(it.uid, it.title, base, lo, hi,
                                          str(pat) if pat is not None else None)
        elif it.type == "enum":
            if base_of(it) != "enum":
                raise DeclarationError(f"{it.uid}: a value set builds on the base type enum")
            raw = it.attrs.get("values")
            vals = tuple(v.strip() for v in (raw if isinstance(raw, list) else str(raw or "").split(","))
                         if str(v).strip())
            if not vals:
                raise DeclarationError(f"{it.uid}: a value set lists its values")
            out.by_uid[it.uid] = TypeDecl(it.uid, it.title, "enum", values=vals)

    for it in live:
        if it.type == "register":
            for k in ("prefix", "dir", "holds"):
                if not it.attrs.get(k):
                    raise DeclarationError(f"{it.uid}: a register declaration names its {k}")
            out.by_uid[it.uid] = RegDecl(
                it.uid, str(it.attrs["prefix"]), str(it.attrs["dir"]), str(it.attrs["holds"]),
                str(it.attrs.get("label") or ""), _flag(it, "required", True),
                _flag(it, "exactly_one", False), _scope(it))
        elif it.type == "link_type":
            if not it.attrs.get("name"):
                raise DeclarationError(f"{it.uid}: a link type declaration names its link type")
            out.by_uid[it.uid] = LTDecl(it.uid, str(it.attrs["name"]),
                                        _flag(it, "grounds", False), _scope(it))

    def reg_of(item, link_type: str, what: str) -> RegDecl:
        r = resolve(_one(item, link_type, what))
        if not isinstance(r, RegDecl):
            raise DeclarationError(f"{item.uid}: '{link_type}' must name a register declaration")
        return r

    pending_conditions: list[tuple[object, str, str, bool]] = []
    for it in live:
        if it.type == "attribute":
            if not it.attrs.get("name"):
                raise DeclarationError(f"{it.uid}: an attribute declaration names its attribute")
            reg = reg_of(it, "belongs_to", "register")
            typ = resolve(_one(it, "has_type", "type or value set"))
            if not isinstance(typ, TypeDecl):
                raise DeclarationError(f"{it.uid}: has_type must name a type or value set")
            conds = {k: parse_condition(str(it.attrs[k])) for k in ("required_when", "absent_when")
                     if it.attrs.get(k)}
            out.by_uid[it.uid] = AttrDecl(
                it.uid, reg.prefix, str(it.attrs["name"]), typ, _flag(it, "required", False),
                conds.get("required_when"), conds.get("absent_when"), _flag(it, "list", False),
                _scope(it) if "scope" in it.attrs else reg.scope)
        elif it.type == "link":
            frm = reg_of(it, "from", "register it runs from")
            tos = [resolve(t) for t in _targets(it, "to")]
            if not tos or not all(isinstance(t, RegDecl) for t in tos):
                raise DeclarationError(f"{it.uid}: 'to' must name at least one register declaration")
            lt = resolve(_one(it, "via", "link type"))
            if not isinstance(lt, LTDecl):
                raise DeclarationError(f"{it.uid}: 'via' must name a link type declaration")
            conds = {k: parse_condition(str(it.attrs[k])) for k in ("when", "from_when")
                     if it.attrs.get(k)}
            out.by_uid[it.uid] = LinkDecl(
                it.uid, frm.prefix, tuple(t.prefix for t in tos), lt.name,
                _int(it, "min"), _int(it, "max"), _int(it, "per_target_min"),
                conds.get("when"), conds.get("from_when"),
                _scope(it) if "scope" in it.attrs else frm.scope)

    # Aliases (SR-0299): a declaration that names one its standard makes.
    for it in live:
        if it.type in ("register", "attribute", "link_type"):
            for target in _targets(it, "aliases"):
                std = resolve(target)
                mine = out.by_uid[it.uid]
                if type(std) is not type(mine):
                    raise DeclarationError(
                        f"{it.uid} aliases {target}, which is not a declaration of the same kind")
                std = replace(std, uid=target)
                if isinstance(mine, RegDecl):
                    out.by_uid[it.uid] = replace(std, prefix=mine.prefix,
                                                 dir=mine.dir, std_prefix=std.prefix,
                                                 scope=THIS_GRAPH)
                elif isinstance(mine, AttrDecl):
                    out.by_uid[it.uid] = replace(std, name=mine.name, std_name=std.name,
                                                 scope=THIS_GRAPH)
                else:
                    out.by_uid[it.uid] = replace(std, name=mine.name, std_name=std.name,
                                                 scope=THIS_GRAPH)
    return out


def _collect(decls: Declarations, keys, scope: str | None) -> Declarations:
    """The declarations among ``keys`` (UIDs in ``decls.by_uid``) of ``scope``
    (None: every scope), indexed by what they name. Refuses a second declaration of
    a name (SR-0290, SR-0294)."""
    out = Declarations(by_uid=decls.by_uid)
    for uid in keys:
        d = decls.by_uid[uid]
        if isinstance(d, tuple) or isinstance(d, TypeDecl):
            continue
        if scope is not None and d.scope != scope:
            continue
        _index(out, uid, d)
    return out


def _clash(kind: str, name: str, a: str, b: str) -> DeclarationError:
    return DeclarationError(
        f"{kind} {name} is declared twice, by {a} and {b} (SR-0290): rename one, "
        "or alias the imported one (SR-0299)")


def _index(out: Declarations, uid: str, d) -> None:
    if isinstance(d, RegDecl):
        for other in out.registers.values():
            if other.dir == d.dir and other.prefix != d.prefix:
                raise _clash("register folder", d.dir + "/", other.uid, uid)
        if d.prefix in out.registers:
            raise _clash("register", d.prefix, out.registers[d.prefix].uid, uid)
        out.registers[d.prefix] = replace(d, uid=uid) if d.std_prefix is None else d
    elif isinstance(d, AttrDecl):
        key = (d.register, d.name)
        if key in out.attrs:
            raise _clash("attribute", f"{d.register}.{d.name}", out.attrs[key].uid, uid)
        out.attrs[key] = d
    elif isinstance(d, LinkDecl):
        out.links.append(d)
    elif isinstance(d, LTDecl):
        if d.name in out.link_types:
            raise _clash("link type", d.name, out.link_types[d.name].uid, uid)
        out.link_types[d.name] = d


def _apply_aliases(decls: Declarations) -> None:
    """Rewrite what names an aliased register or link type to the alias."""
    for a in [a for a in decls.attrs.values() if a.std_name]:
        decls.attrs.pop((a.register, a.std_name), None)
    prefixes = {r.std_prefix: r.prefix for r in decls.registers.values() if r.std_prefix}
    names = {lt.std_name: lt.name for lt in decls.link_types.values() if lt.std_name}
    if prefixes:
        for p in list(prefixes):
            decls.registers.pop(p, None)
        decls.attrs = {(prefixes.get(p, p), n): replace(a, register=prefixes.get(p, p))
                       for (p, n), a in decls.attrs.items()}
    if names:
        for n in list(names):
            decls.link_types.pop(n, None)
    if prefixes or names:
        decls.links = [replace(l, frm=prefixes.get(l.frm, l.frm),
                               to=tuple(prefixes.get(t, t) for t in l.to),
                               via=names.get(l.via, l.via)) for l in decls.links]


# ------------------------------------------------------------ attaching to a project

def _decl_items(project):
    return [it for it in project.items() if it.type in DECLARATION_TYPES | {"base"}]


def attach(project) -> None:
    """Form ``project``'s declarations, extend its schema configuration with what
    they declare, and refuse what collides. Sets ``project.formed_declarations``
    (every declaration it and its sources hold, for its followers to resolve
    against), ``project.offered`` (what it declares for its followers) and
    ``project.declarations`` (what it is checked against)."""
    inh = project.inheritance
    source = getattr(inh, "source_project", None) if inh else None
    src_formed = getattr(source, "formed_declarations", None) if source else None
    src_offered = getattr(source, "offered", None) if source else None
    dead = project.schema.dead_statuses()
    own_items = _decl_items(project)
    if not own_items and not src_offered:
        project.formed_declarations = src_formed
        project.offered = Declarations()
        project.declarations = Declarations()
        return
    formed = build(own_items, dead=dead, source=src_formed,
                   source_ns=inh.namespace if inh else None)
    own = [it.uid for it in own_items
           if it.uid in formed.by_uid and not isinstance(formed.by_uid[it.uid], tuple)]
    project.formed_declarations = formed
    project.offered = _collect(formed, own, FOLLOWERS)
    if source is not None:
        _refuse_twice(project)
    if source is None:
        # A graph with no schema source holding declarations is its own standard:
        # the meta-standard. One written for another version is refused (SR-0286).
        version = (project.config.get("project") or {}).get("meta_standard")
        if version is not None and version != META_STANDARD_VERSION:
            raise DeclarationError(
                f"{project.path} is meta-standard version {version!r}, and this Tool "
                f"implements version {META_STANDARD_VERSION} (SR-0286)")
        effective = _collect(formed, own, None)
        offered_lts = {}
    else:
        effective = Declarations(by_uid=formed.by_uid)
        for uid, d in _offered_items(src_offered or Declarations()):
            q = _qualify(uid, inh.namespace)
            _index(effective, q, replace(d, uid=q))
        mine = _collect(formed, own, THIS_GRAPH)
        for d in list(mine.registers.values()) + list(mine.attrs.values()) \
                + mine.links + list(mine.link_types.values()):
            _index(effective, d.uid, d)
        offered_lts = _transitive_link_types(source)
        for name, lt in offered_lts.items():
            if name not in effective.link_types:
                effective.link_types[name] = lt
    _apply_aliases(effective)
    _check_conditions(effective)
    project.declarations = effective
    if effective:
        _extend_schema_config(project, effective)


def _refuse_twice(project) -> None:
    """A standard declaring the same thing both in its configuration and by items
    is refused (SR-0285, SR-0292)."""
    local = project.config
    for reg in project.offered.registers.values():
        if reg.holds in (local.get("types") or {}):
            raise DeclarationError(
                f"item type '{reg.holds}' is declared both in throughline.toml and by "
                f"{reg.uid}; declare it once, by items (SR-0285)")
    clash = set((local.get("links") or {}).get("types") or ()) & set(project.offered.link_types)
    clash |= set((local.get("grounding") or {}).get("ground_link_types") or ()) \
        & set(project.offered.link_types)
    if clash:
        raise DeclarationError(
            f"link type(s) {sorted(clash)} are declared both in throughline.toml and by "
            "link_type items; declare them once, by items (SR-0292)")


def _offered_items(offered: Declarations):
    for d in offered.registers.values():
        yield d.uid, d
    for d in offered.attrs.values():
        yield d.uid, d
    for d in offered.links:
        yield d.uid, d
    for d in offered.link_types.values():
        yield d.uid, d


def _transitive_link_types(source) -> dict[str, LTDecl]:
    """A standard's link types for its followers: its own and those of its own
    schema source, to any depth (SR-0292)."""
    out: dict[str, LTDecl] = {}
    while source is not None:
        offered = getattr(source, "offered", None)
        for name, lt in (offered.link_types if offered else {}).items():
            out.setdefault(name, lt)
        inh = source.inheritance
        source = getattr(inh, "source_project", None) if inh else None
    return out


def _check_conditions(decls: Declarations) -> None:
    """Refuse a condition naming a register that is neither its own nor declared
    exactly_one, an attribute not declared, or an operator or value its type does
    not allow (SR-0289)."""
    singles = {p for p, r in decls.registers.items() if r.exactly_one}

    def check(cond: Condition | None, own: str | None, uid: str) -> None:
        if cond is None:
            return
        for conj in cond.terms:
            for c in conj:
                if c.register != own and c.register not in singles:
                    raise DeclarationError(
                        f"{uid}: condition {cond.text!r} names {c.register}, which is "
                        "neither its own register nor one declared exactly_one (SR-0289)")
                a = decls.attrs.get((c.register, c.attr))
                if a is None:
                    raise DeclarationError(
                        f"{uid}: condition {cond.text!r} names {c.register}.{c.attr}, "
                        "which no attribute declaration declares")
                base = a.type.base
                if base == "enum":
                    if c.op not in ("==", "!=", "in", "not in"):
                        raise DeclarationError(
                            f"{uid}: {c} compares an enum with {c.op}; use ==, !=, in or not in")
                    for v in (c.value if isinstance(c.value, tuple) else (c.value,)):
                        if v not in (a.type.values or ()):
                            raise DeclarationError(
                                f"{uid}: {c} names {v!r}, which {a.type.uid} does not hold")
                elif base in _ORDERED:
                    if c.op in ("in", "not in"):
                        raise DeclarationError(f"{uid}: {c}: in and not in are for enums")
                    if _coerce(base, c.value) is None:
                        raise DeclarationError(f"{uid}: {c}: {c.value!r} is not a {base}")
                else:
                    raise DeclarationError(
                        f"{uid}: {c} compares a {base}; a condition compares enums, "
                        "ints, floats and dates")

    for a in decls.attrs.values():
        check(a.required_when, a.register, a.uid)
        check(a.absent_when, a.register, a.uid)
    for l in decls.links:
        check(l.when, None, l.uid)
        check(l.from_when, l.frm, l.uid)


def _coerce(base: str, raw):
    if base == "date":
        return _as_date(raw)
    try:
        return int(raw) if base == "int" else float(raw)
    except (TypeError, ValueError):
        return None


def _extend_schema_config(project, decls: Declarations) -> None:
    """Add to the project's schema configuration the item types, attributes, link
    types and grounding link types its declarations make, so every other rule reads
    them as it reads a configuration's (SR-0285, SR-0292). A declaration made both
    here and in the project's own configuration is refused."""
    import copy
    base = copy.deepcopy(project.formed_config)
    types = base.setdefault("types", {})
    for reg in decls.registers.values():
        attrs = {"origin": dict(_ORIGIN_SPEC)}
        for a in decls.attrs_of(reg.prefix):
            spec: dict = {}
            if not a.list:
                spec["type"] = {"file": "string"}.get(a.type.base, a.type.base)
                if a.type.base == "enum":
                    spec["values"] = list(a.type.values or ())
            if a.required:
                spec["required"] = True
            attrs[a.name] = spec
        body = dict(types.get(reg.holds) or {})
        body["attrs"] = {**(body.get("attrs") or {}), **attrs}
        types[reg.holds] = body
    if decls.link_types:
        links = base.setdefault("links", {})
        have = list(links.get("types") or [])
        links["types"] = have + [n for n in decls.link_types if n not in have]
        grounding = base.setdefault("grounding", {})
        g = list(grounding.get("ground_link_types") or [])
        grounding["ground_link_types"] = g + [
            n for n, lt in decls.link_types.items() if lt.grounds and n not in g]
    project.schema_config = base
    project._schema = None


# ------------------------------------------------------------------ the check

@dataclass
class Found:
    rule: str
    uid: str
    file: str
    message: str


def _near(name: str, choices) -> str | None:
    best = None
    for c in choices:
        d = _distance(name, c)
        if d <= 2 and (best is None or d < best[0]):
            best = (d, c)
    return best[1] if best else None


def _distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _holds(cond: Condition | None, item_attrs: dict, singles: dict[str, dict],
           decls: Declarations, own: str | None) -> bool:
    if cond is None:
        return False
    for conj in cond.terms:
        if all(_compare(c, item_attrs if c.register == own else singles.get(c.register, {}),
                        decls) for c in conj):
            return True
    return False


def _compare(c: Comparison, attrs: dict, decls: Declarations) -> bool:
    if c.attr not in attrs:
        return False
    v = attrs[c.attr]
    a = decls.attrs.get((c.register, c.attr))
    base = a.type.base if a else "string"
    if c.op == "in":
        return str(v) in c.value
    if c.op == "not in":
        return str(v) not in c.value
    if base in _ORDERED:
        lhs, rhs = _ordered(base, v), _coerce(base, c.value)
        if lhs is None or rhs is None:
            return False
        try:
            return {"==": lhs == rhs, "!=": lhs != rhs, "<": lhs < rhs, "<=": lhs <= rhs,
                    ">": lhs > rhs, ">=": lhs >= rhs}[c.op]
        except TypeError:
            return False
    return (str(v) == str(c.value)) if c.op == "==" else (str(v) != str(c.value))


def _value_ok(a: AttrDecl, value, root: Path) -> str | None:
    t = a.type
    if a.list:
        if not isinstance(value, list) or not value:
            return "a non-empty list"
        values = value
    else:
        values = [value]
    for v in values:
        if not base_accepts(t.base, v, root):
            return f"a {t.base}" + (" naming a file in the graph" if t.base == "file" else "")
        if t.base == "enum" and str(v) not in (t.values or ()):
            return f"one of {', '.join(t.values or ())}"
        if t.pattern is not None and not re.fullmatch(t.pattern, str(v)):
            return f"text matching {t.pattern}"
        if t.min is not None and _ordered(t.base, v) < _ordered(t.base, t.min):
            return f"at least {t.min}"
        if t.max is not None and _ordered(t.base, v) > _ordered(t.base, t.max):
            return f"at most {t.max}"
    return None


def check(project, items=None) -> list[Found]:
    """Findings for ``project``'s own items against its declarations (SR-0287,
    SR-0288, SR-0291, SR-0293). ``items`` limits the check to those items (the
    local ones of a union); by default every item of the project."""
    decls = getattr(project, "declarations", None)
    if not decls:
        return []
    root = project.path
    dead = project.schema.dead_statuses()
    all_items = list(items if items is not None else project.items())
    live = [it for it in all_items if it.status not in dead and it.deleted is None]
    by_prefix: dict[str, list] = {}
    for it in live:
        by_prefix.setdefault(_prefix(it.uid), []).append(it)
    out: list[Found] = []

    def file_of(it) -> str:
        return str(it._path) if getattr(it, "_path", None) else ""

    # Registers (SR-0287) and undeclared registers (SR-0293).
    # The graph's own registers: in a union a borrowed register lies outside it.
    held = {p: reg for p, reg in project.registers.items()
            if reg.path is None or _rel(reg.path, root) is not None}
    for prefix, d in decls.registers.items():
        reg = held.get(prefix)
        if reg is None:
            if d.required:
                out.append(Found("register-declared", d.uid, "",
                                 f"the standard declares the register {prefix} ({d.dir}/, "
                                 f"holding {d.holds}) required, and this graph has none"))
            continue
        rel = _rel(reg.path, root)
        if rel is not None and rel != d.dir:
            out.append(Found("register-declared", d.uid, str(reg.path or ""),
                             f"register {prefix} is in {rel}/, and {d.uid} declares {d.dir}/"))
        if reg.type is not None and reg.type != d.holds:
            out.append(Found("register-declared", d.uid, str(reg.path or ""),
                             f"register {prefix} holds {reg.type}, and {d.uid} declares {d.holds}"))
        if d.exactly_one and len(by_prefix.get(prefix, [])) != 1:
            out.append(Found("register-declared", d.uid, str(reg.path or ""),
                             f"register {prefix} holds {len(by_prefix.get(prefix, []))} live "
                             f"items, and {d.uid} declares exactly one"))
    for p, reg in held.items():
        if p in decls.registers or reg.type in DECLARATION_TYPES | {"base", "constraint"}:
            continue
        near = _near(p, decls.registers)
        out.append(Found("undeclared", p, str(reg.path or ""),
                         f"register {p} is declared by no register declaration"
                         + (f" — did you mean {near}, {decls.registers[near].uid}?" if near else "")))

    singles = {p: by_prefix[p][0].attrs for p, r in decls.registers.items()
               if r.exactly_one and len(by_prefix.get(p, [])) == 1}

    # Attributes (SR-0288) and undeclared attributes (SR-0293).
    for it in live:
        p = _prefix(it.uid)
        if p not in decls.registers:
            continue
        declared = {a.name: a for a in decls.attrs_of(p)}
        for name, a in declared.items():
            present = name in it.attrs and it.attrs[name] is not None
            if present:
                if a.absent_when is not None and _holds(a.absent_when, it.attrs, singles, decls, p):
                    out.append(Found("attribute-declared", it.uid, file_of(it),
                                     f"{name} must be absent when {a.absent_when.text} ({a.uid})"))
                    continue
                want = _value_ok(a, it.attrs[name], root)
                if want:
                    out.append(Found("attribute-declared", it.uid, file_of(it),
                                     f"{name}: {it.attrs[name]!r} is not {want} ({a.uid}, {a.type.title})"))
            elif a.required or (a.required_when is not None
                                and _holds(a.required_when, it.attrs, singles, decls, p)):
                why = "always" if a.required else f"when {a.required_when.text}"
                out.append(Found("attribute-declared", it.uid, file_of(it),
                                 f"{name} is required {why} ({a.uid}) and is missing"))
        for name in it.attrs:
            if name in declared or name in TOOL_ATTRS:
                continue
            near = _near(name, declared)
            out.append(Found("undeclared", it.uid, file_of(it),
                             f"attribute {name} is declared for {p} by no attribute declaration"
                             + (f" — did you mean {near}, {declared[near].uid}?" if near else "")))

    # Links (SR-0291) and undeclared links (SR-0293).
    graph_on = {id(l): (l.when is None or _holds(l.when, {}, singles, decls, None))
                for l in decls.links}
    for it in live:
        p = _prefix(it.uid)
        if p not in decls.registers:
            continue
        for link in it.links:
            tp = _prefix(link.target)
            if not any(l.frm == p and l.via == link.type and tp in l.to for l in decls.links):
                near = _near(link.type, decls.link_types) if link.type not in decls.link_types else None
                out.append(Found("undeclared", it.uid, file_of(it),
                                 f"link {link.type} -> {link.target} ({p} to {tp}) is declared by "
                                 "no link declaration"
                                 + (f" — did you mean {near}?" if near else "")))
    for l in decls.links:
        if not graph_on[id(l)]:
            continue
        sources = [it for it in by_prefix.get(l.frm, [])
                   if l.from_when is None or _holds(l.from_when, it.attrs, singles, decls, l.frm)]
        incoming: dict[str, int] = {}
        for it in sources:
            n = 0
            for link in it.links:
                if link.type == l.via and _prefix(link.target) in l.to:
                    n += 1
                    incoming[link.target] = incoming.get(link.target, 0) + 1
            if l.min is not None and n < l.min:
                out.append(Found("link-declared", it.uid, file_of(it),
                                 f"has {n} {l.via} link(s) to {'/'.join(l.to)}, and {l.uid} "
                                 f"requires at least {l.min}"))
            if l.max is not None and n > l.max:
                out.append(Found("link-declared", it.uid, file_of(it),
                                 f"has {n} {l.via} link(s) to {'/'.join(l.to)}, and {l.uid} "
                                 f"allows at most {l.max}"))
        if l.per_target_min:
            for tp in l.to:
                for target in by_prefix.get(tp, []):
                    n = incoming.get(target.uid, 0)
                    if n < l.per_target_min:
                        out.append(Found("link-declared", target.uid, file_of(target),
                                         f"has {n} incoming {l.via} link(s) from {l.frm}"
                                         + (f" where {l.from_when.text}" if l.from_when else "")
                                         + f", and {l.uid} requires at least {l.per_target_min}"))
    return out


def _prefix(uid: str) -> str:
    uid = uid.rsplit(":", 1)[-1]
    return uid.rsplit("-", 1)[0]


def _rel(path, root) -> str | None:
    if path is None:
        return None
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return None


# ------------------------------------------------------------- the dump (SR-0295)

def marks(project) -> dict:
    """For each item of ``project``, the declarations its standard makes that cover
    its register, attributes and links, by UID. The follower's own declarations
    carry no mark."""
    decls = getattr(project, "declarations", None)
    if not decls:
        return {}
    out: dict = {}
    for it in project.items():
        p = _prefix(it.uid)
        reg = decls.registers.get(p)
        if reg is None or reg.scope == THIS_GRAPH and reg.std_prefix is None:
            continue
        mark: dict = {"register": reg.uid}
        attrs = {}
        for a in decls.attrs_of(p):
            if a.name in it.attrs and (a.scope == FOLLOWERS or a.std_name):
                attrs[a.name] = a.uid
        if attrs:
            mark["attrs"] = attrs
        links = []
        for link in it.links:
            for l in decls.links:
                if l.scope == FOLLOWERS and l.frm == p and l.via == link.type \
                        and _prefix(link.target) in l.to:
                    links.append({"target": link.target, "type": link.type, "declaration": l.uid})
                    break
        if links:
            mark["links"] = links
        out[it.uid] = mark
    return out
