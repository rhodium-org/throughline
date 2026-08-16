# Copyright (c) 2026 Henry J Grech-Cini
# SPDX-License-Identifier: Apache-2.0
"""In-place marker injection (SR-0094) — the drift-free document seam (UR-0024).

A requirements document is a human-owned Markdown file that interleaves the
author's own prose with item content drawn from the graph. The author marks the
places where generated content belongs with HTML comments (invisible on GitHub);
throughline regenerates *only* the marked regions, leaving every other byte of
the file untouched. Because the content is a reference that is re-rendered in
place — never a hand-maintained copy — it cannot silently drift from the graph.

A marked region is recognised by its *general form* — a ``tl:`` marker naming a
kind, with an optional modifier — and the kind is then resolved through a single
registry (SR-0186). One registry entry is the only place a directive is declared:
it carries how the directive renders, whether it publishes the items its filter
selects for the coverage rule (SR-0096), and how that selection is read from the
argument. A kind no registered directive provides fails injection by name, rather
than failing to match the pattern and being reported as an unbalanced marker.

A front end built on throughline may add a directive with ``register_directive``,
so a capability that depends on state core does not hold is provided by the layer
that holds it instead of being stubbed inside core (NG-0007). Core holds no
mapping from a directive to the front end that provides it.

Core provides nine directives, each opened by ``<!-- tl:<directive> <arg> -->``
and closed by ``<!-- tl:end -->``:

    tl:item   <UID>     one item rendered as a block, including its outgoing links
              grouped by type (SR-0113) with each target rendered through the
              optional target resolver, so a borrowed clause shows its reference
              number rather than a bare UID
    tl:table  <filter>  a table of items matching an SR-0045 filter
    tl:matrix [<dir>:<link_type>] <filter>  a traceability matrix for matching
              items; an optional incoming:/outgoing:<link_type> selector renders
              the items linked to each match in that direction (SR-0099), e.g.
              ``tl:matrix incoming:implements type == 'user_requirement'``
    tl:count  <filter>  the number of live items matching an SR-0045 filter,
              rendered as a bare integer (SR-0109) — a live tally for a document
              or a README badge that cannot silently drift from the graph
    tl:catalog <filter> every matching item rendered as a full block, in UID order
              (SR-0111) — a self-maintaining master reference document other
              documents cite by UID
    tl:unused <filter>  the matching items no narrative document references
              (SR-0112) — a catalogue mirror does not count as use, so the report
              stays meaningful alongside a full catalogue
    tl:graph  <filter>  the matching items and their link targets as a Mermaid
              flowchart (SR-0115), nodes coloured by item type and external targets
              set apart, so a reader sees the graph flow from roots to externals
    tl:chart  <key> [<filter>]  a Mermaid bar chart of the live-item count grouped
              by a key (SR-0116) — a field (type/status), an attribute, or the
              reserved ``degree`` for a node-complexity distribution
    tl:stats  <filter>  a compact Markdown summary of the graph's complexity
              (SR-0117) — item and link totals by type, grounding depth, the
              most-connected items, and the degree distribution

Anything richer (HTML, PDF, a whole book) is delegated to external tools
(pandoc, mdBook) run over the injected files. Keeping the engine to these
directives is a deliberate boundary: throughline is a validator and an injector,
never a document editor (see the ``non_goal`` NG-0001).
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from .graph import Index
from .validate import FilterError, eval_filter, is_namespace_qualified

# A directive name: an identifier, so the pattern that *finds* a marked region
# carries no enumeration of the kinds that exist (SR-0186). `end` is excluded
# because `tl:end` closes a region rather than opening one.
_NAME = r"(?!end\b)[A-Za-z_][A-Za-z0-9_]*"

# The modifiers a marker may carry after its kind. `.inline` (SR-0119) omits the
# newline wrapping so a scalar directive can sit inside a sentence.
_MODIFIERS = frozenset({"inline"})

# A marked region: the open marker, its generated body, and the end marker. The
# body is matched non-greedily so adjacent regions do not merge; DOTALL lets a
# body span lines; IGNORECASE tolerates `TL:` and `tl:`.
_BLOCK = re.compile(
    rf"(?P<open><!--\s*tl:(?P<kind>{_NAME})(?:\.(?P<mod>[A-Za-z_][A-Za-z0-9_]*))?\s+(?P<arg>.*?)\s*-->)"
    r"(?P<body>.*?)"
    r"(?P<close><!--\s*tl:end\s*-->)",
    re.DOTALL | re.IGNORECASE,
)
# Any single tl: marker, to detect an unbalanced open/close the block regex skips.
_OPEN = re.compile(rf"<!--\s*tl:{_NAME}\b", re.IGNORECASE)
_END = re.compile(r"<!--\s*tl:end\s*-->", re.IGNORECASE)

# An optional matrix selector (SR-0099): incoming:/outgoing:<link_type> before
# the filter. Reuses the coverage-rule grammar so the language is identical. An
# optional target-display suffix (SR-0110) follows the link type: @<primary> or
# @<primary>(<secondary>), each token being `uid` or an attribute name, so a cell
# can render a borrowed clause's own reference number instead of its UID.
_MATRIX_REL = re.compile(
    r"^(incoming|outgoing):(\w+)"
    r"(?:@(\w+)(?:\((\w+)\))?)?"
    r"\s*(.*)$",
    re.DOTALL,
)


def _parse_matrix_arg(
    arg: str,
) -> tuple[str | None, str | None, str | None, str | None, str]:
    """Split a tl:matrix argument into
    (direction, link_type, primary, secondary, filter). Absent a selector, the
    first four are None and the whole arg is the filter. `primary`/`secondary` are
    the target-display tokens (each `uid` or an attribute name); `primary` defaults
    to `uid` when a selector is present but no suffix is given."""
    m = _MATRIX_REL.match(arg.strip())
    if m:
        primary = m.group(3) or "uid"
        return m.group(1), m.group(2), primary, m.group(4), m.group(5).strip()
    return None, None, None, None, arg.strip()


class InjectError(ValueError):
    """A document marker could not be rendered (SR-0094): an unbalanced marker,
    an unknown item, or a malformed filter. Raised so drift is fixed, not hidden."""


# ------------------------------------------------------------------- directives

@dataclass(frozen=True)
class Directive:
    """One document directive (SR-0186). The registry entry is the *only* place a
    directive is declared, so it cannot be recognised for rendering while being
    omitted from the coverage question, or the reverse.

    ``render(project, arg, resolver) -> str`` produces the body of the marked
    region. ``publishes`` says whether the items ``selects`` returns count as
    published for the coverage rule (SR-0096): a diagram, a statistic or a report
    derives a picture *from* items rather than putting their content in front of a
    reader, so it selects them without publishing them. ``selects(project, arg)``
    reads the argument as the set of local item UIDs the directive draws on."""

    name: str
    render: Callable[..., str]
    publishes: bool = True
    selects: Callable[..., Iterable[str]] | None = None


_REGISTRY: dict[str, Directive] = {}


def register_directive(
    name: str,
    render: Callable[..., str],
    *,
    publishes: bool = True,
    selects: Callable[..., Iterable[str]] | None = None,
) -> Directive:
    """Register a document directive under ``name`` (SR-0186).

    A front end built on throughline calls this to provide a directive core does
    not — typically one needing state core does not hold, which core therefore
    does not stub (NG-0007). Registration is by name only: core keeps no record of
    which front end supplied a directive, and an unregistered kind is reported as
    unprovided without naming a tool that might provide it.

    Re-registering a name replaces the entry, so a front end may override a core
    directive deliberately."""
    if not re.fullmatch(_NAME, name):
        raise ValueError(
            f"invalid directive name {name!r} — a directive is named by an "
            "identifier, and 'end' is reserved for the closing marker")
    d = Directive(name=name, render=render, publishes=publishes, selects=selects)
    _REGISTRY[name] = d
    return d


def directive_names() -> tuple[str, ...]:
    """The names of every registered directive, sorted — what this build of the
    running front end provides."""
    return tuple(sorted(_REGISTRY))


class TargetResolver:
    """Resolves a matrix link target's display, liveness and attributes (SR-0110).

    The default reads only the local project, so injection behaves exactly as it
    did before this seam existed. A composing front end (tl-compose) supplies a
    resolver backed by the union graph, so a namespace-qualified target such as
    ``asvs:SR-0227`` — absent from the local project — still resolves its own
    ``source_ref`` and counts as live."""

    def __init__(self, project):
        self._project = project

    def present(self, uid: str) -> bool:
        """True if the target is a live item to be shown in a relationship cell."""
        return _is_live(self._project, uid)

    def display(self, uid: str) -> str:
        """The UID as it should appear in a cell. The local target string is
        already the form the author wrote (e.g. ``asvs:SR-0227``)."""
        return uid

    def attr(self, uid: str, name: str) -> str | None:
        """The value of attribute ``name`` on the target, or None if absent."""
        it = self._project.get(uid)
        if it is None:
            return None
        val = it.attrs.get(name)
        return None if val is None else str(val)

    def link_display(self, uid: str) -> str:
        """How an outgoing link target should read inside an item block (SR-0113).
        The default is the bare UID, so a project that does not compose sees its
        links unchanged. A composing resolver enriches a borrowed clause — e.g.
        appending its ``source_ref`` — so a reader sees what the item grounds to."""
        return uid


def has_markers(text: str) -> bool:
    """True if the text contains any tl: marker at all — the file is a throughline
    document. Files without markers are left completely untouched (SR-0095)."""
    return bool(_OPEN.search(text) or _END.search(text))


def _scan_refs(project, *, publishing_only: bool = False,
               exclude: frozenset[str] = frozenset()) -> set[str]:
    """The set of local item UIDs the document markers draw on, each marker read
    through its registry entry's ``selects`` (SR-0186). With ``publishing_only``,
    a directive that selects items without publishing them is skipped. ``exclude``
    drops further kinds by name for a caller asking a narrower question.

    Markers naming an unregistered kind, and malformed filters, are ignored —
    reporting them is `tl docs`'s job, not a coverage question's."""
    root = Path(project.path)
    refs: set[str] = set()
    for pattern in project.schema.docs_paths:
        for p in sorted(root.glob(pattern)):
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except OSError:
                continue
            for m in _BLOCK.finditer(text):
                kind, arg = m.group("kind").lower(), m.group("arg").strip()
                d = _REGISTRY.get(kind)
                if d is None or d.selects is None or kind in exclude:
                    continue
                if publishing_only and not d.publishes:
                    continue
                try:
                    refs.update(d.selects(project, arg))
                except InjectError:
                    continue
    return refs


def referenced_uids(project) -> set[str] | None:
    """The set of item UIDs published by a configured document (SR-0096). Returns
    ``None`` when no ``[docs] paths`` are configured, so the ``unpublished`` rule
    is inert for projects that do not publish through throughline.

    Whether a directive publishes what it selects is declared once, on its registry
    entry (SR-0186) — so a directive registered by a front end answers this question
    the same way core's own do. A ``tl:unused`` report does not publish the items it
    lists (SR-0112); a ``tl:graph`` / ``tl:chart`` / ``tl:stats`` derives a picture or
    a statistic, not the item's content (SR-0115/SR-0116/SR-0117). A ``tl:catalog``
    does publish — its items appear in full — and so is counted."""
    if not project.schema.docs_paths:
        return None
    return _scan_refs(project, publishing_only=True)


def inject_text(project, text: str, resolver: "TargetResolver | None" = None) -> str:
    """Return ``text`` with every marked region re-rendered from ``project``.
    Idempotent: injecting already-injected text yields identical output. Raises
    InjectError on an unbalanced marker so a broken document fails loudly.

    ``resolver`` (SR-0110) resolves matrix link targets' liveness and attributes;
    when omitted it reads the local ``project``, so behaviour is unchanged. A
    composing caller passes a union-backed resolver so borrowed clauses resolve."""
    if resolver is None:
        resolver = TargetResolver(project)
    opens = len(_OPEN.findall(text))
    ends = len(_END.findall(text))
    if opens != ends:
        raise InjectError(
            f"unbalanced tl: markers — {opens} opener(s) but {ends} tl:end(s); "
            "every opener needs exactly one tl:end")

    def _replace(m: re.Match) -> str:
        kind = m.group("kind").lower()
        mod = (m.group("mod") or "").lower()
        # The modifier is lexed generically, so an unrecognised one is reported
        # rather than silently ignored — a marker whose modifier did nothing would
        # be exactly the kind of silent miss the registry exists to remove.
        if mod and mod not in _MODIFIERS:
            raise InjectError(
                f"unknown modifier '.{mod}' on 'tl:{kind}' (expected one of "
                f"{', '.join(sorted(_MODIFIERS))})")
        body = _render(project, kind, m.group("arg").strip(), resolver)
        # The default wraps the body in newlines so a block directive (item, table,
        # graph …) renders as its own block. The tl:<kind>.inline modifier (SR-0119)
        # omits that wrapping so a scalar directive — above all tl:count — can sit
        # inside a sentence without a line-leading marker starting an HTML block and
        # splitting the Markdown paragraph.
        if mod == "inline":
            return f"{m.group('open')}{body}{m.group('close')}"
        return f"{m.group('open')}\n{body}\n{m.group('close')}"

    return _BLOCK.sub(_replace, text)


# ------------------------------------------------------------------- renderers

def _render(project, kind: str, arg: str, resolver: "TargetResolver") -> str:
    d = _REGISTRY.get(kind)
    if d is None:
        raise InjectError(_unprovided_message(kind))
    return d.render(project, arg, resolver)


def _unprovided_message(kind: str) -> str:
    """Why a marked region naming ``kind`` could not be rendered (SR-0186).

    States that the *running command* does not provide the directive and that
    front ends built on throughline register directives of their own — without
    naming one, because core holds no mapping from a directive to its provider
    (NG-0007). An author who ran the wrong command is told what is missing, rather
    than that the document has unbalanced markers."""
    return (
        f"'tl:{kind}' is not a directive this command provides — "
        f"it provides {', '.join(directive_names())}. "
        "Directives beyond throughline's own are registered by front ends built "
        "on it, so a document using one must be injected with the front end that "
        "provides it; otherwise correct the marker. Nothing was written.")


# Link-type slugs shown as human labels in an item block's link section. Any
# type not listed falls back to its title-cased slug, so the section is total.
def _link_label(ltype: str) -> str:
    return ltype.replace("_", " ").capitalize()


def render_item(project, uid: str, resolver: "TargetResolver | None" = None) -> str:
    if resolver is None:
        resolver = TargetResolver(project)
    item = project.get(uid)
    if item is None:
        raise InjectError(
            f"tl:item references '{uid}' which is not in the graph — the document "
            "points at an item that no longer exists (drift)")
    if item.is_deleted:
        raise InjectError(
            f"tl:item references '{uid}' which is deleted — remove the marker or "
            "point it at a live item")
    # Identity is resolved, not taken from the item (SR-0187). The default returns
    # the UID unchanged, so a project that does not compose is unaffected; a front
    # end rendering an item drawn from another graph states it under the identity
    # the citing document uses, so a mirrored clause cannot be published under a
    # UID that collides with an unrelated local item of the same number.
    head = (f"**{resolver.display(item.uid)} — {item.title or '(untitled)'}** — "
            f"`{item.type}`, status `{item.status}`")
    lines = [head, ""]
    if item.text:
        lines += [f"> {ln}" if ln else ">" for ln in item.text.splitlines()]
        lines.append("")
    if item.rationale:
        lines.append(f"*Rationale:* {' '.join(item.rationale.split())}")
        lines.append("")
    link_lines = _item_link_lines(item, resolver)
    if link_lines:
        lines += link_lines
        lines.append("")
    if item.attrs:
        lines.append(" · ".join(f"**{k}**: {v}" for k, v in item.attrs.items()))
        lines.append("")
    return "\n".join(lines).rstrip()


def _item_link_lines(item, resolver: "TargetResolver") -> list[str]:
    """The item's outgoing links grouped by link type, one line per type, each
    target rendered through the resolver (SR-0113). An item with no outgoing links
    renders no link section, so an unlinked item's block is unchanged."""
    grouped: dict[str, list[str]] = {}
    for link in item.links:
        grouped.setdefault(link.type, []).append(resolver.link_display(link.target))
    return [f"*{_link_label(ltype)}:* {', '.join(targets)}"
            for ltype, targets in grouped.items()]


def matching(project, expr: str) -> list:
    """Live items matching an SR-0045 filter, in UID order. A malformed filter is
    fatal here (unlike coverage rules) so a broken document is fixed, not silently
    empty."""
    idx = Index.build(project)
    try:
        items = [it for it in project.items()
                 if not it.is_deleted and eval_filter(it, expr, idx)]
    except FilterError as e:
        raise InjectError(f"bad filter '{expr}': {e}") from e
    return sorted(items, key=lambda it: it.uid)


def _is_live(project, uid: str) -> bool:
    """True if uid names an item present in the graph that is neither deleted nor
    invalidated — i.e. something that genuinely realizes/relates, not a dead
    record. The dead set is resolved from the project's status roles (SR-0131),
    never a value fixed in code."""
    it = project.get(uid)
    return (it is not None and not it.is_deleted
            and it.status not in project.schema.dead_statuses())


def _cell(value: str) -> str:
    """Escape a table cell so a pipe or newline in content cannot break the row."""
    return value.replace("|", "\\|").replace("\n", " ").strip()


def _render_table(project, expr: str) -> str:
    rows = matching(project, expr)
    out = ["| UID | Type | Status | Title |", "|---|---|---|---|"]
    for it in rows:
        out.append(f"| {it.uid} | {it.type} | {it.status} | "
                   f"{_cell(it.title or '')} |")
    if not rows:
        out.append("| _(no matching items)_ |  |  |  |")
    return "\n".join(out)


def _render_count(project, expr: str) -> str:
    """The number of live items matching an SR-0045 filter, as a bare integer
    (SR-0109). Only live items count — a rejected or deleted item is not part of
    the graph a reader tallies, the same terminal-status set the matrix renderers
    use. A malformed filter fails injection; a filter matching nothing renders 0."""
    rows = [it for it in matching(project, expr) if _is_live(project, it.uid)]
    return str(len(rows))


def _target_cell(resolver: "TargetResolver", uid: str,
                 primary: str, secondary: str | None) -> str:
    """Render one link target per the display spec (SR-0110). Each token is the
    literal ``uid`` (the resolver's display form) or an attribute name. An empty
    parenthesised secondary is dropped so a missing source_ref never renders as
    bare brackets."""
    def _tok(tok: str) -> str:
        return resolver.display(uid) if tok == "uid" else (resolver.attr(uid, tok) or "")
    head = _tok(primary)
    if secondary is None:
        return head
    tail = _tok(secondary)
    return f"{head} ({tail})" if tail else head


def _render_matrix(project, arg: str, resolver: "TargetResolver") -> str:
    """A traceability matrix. Default form: each matching item with what it grounds
    up to and what verifies it. With an incoming:/outgoing:<link_type> selector
    (SR-0099): each matching item and the items linked to it in that direction —
    e.g. incoming:implements over user_requirements lists each UR's realizers. An
    optional @<primary>(<secondary>) suffix (SR-0110) chooses how each target is
    rendered — its UID, an attribute such as source_ref, or UID plus attribute."""
    direction, ltype, primary, secondary, expr = _parse_matrix_arg(arg)
    idx = Index.build(project)
    rows = matching(project, expr)

    if direction is not None:
        header = f"{ltype.capitalize()} ({direction})"
        out = [f"| UID | Title | {header} |", "|---|---|---|"]
        for it in rows:
            links = (idx.in_links(it.uid, {ltype}) if direction == "incoming"
                     else idx.out_links(it.uid, {ltype}))
            # Only live items count as realizers — a rejected or deleted item
            # does not realize anything (SR-0099). Liveness and attributes are
            # resolved through the resolver so borrowed targets resolve (SR-0110).
            cells = ", ".join(
                _target_cell(resolver, u, primary, secondary)
                for u, _k in links if resolver.present(u)) or "—"
            out.append(f"| {it.uid} | {_cell(it.title or '')} | {cells} |")
        if not rows:
            out.append("| _(no matching items)_ |  |  |")
        return "\n".join(out)

    ground = project.schema.ground_link_types
    out = ["| UID | Title | Traces to | Verified by |", "|---|---|---|---|"]
    for it in rows:
        up = ", ".join(t for t, _k in idx.out_links(it.uid, ground)) or "—"
        ver = ", ".join(s for s, _k in idx.in_links(it.uid, {"verifies"})) or "—"
        out.append(f"| {it.uid} | {_cell(it.title or '')} | {up} | {ver} |")
    if not rows:
        out.append("| _(no matching items)_ |  |  |  |")
    return "\n".join(out)


def _render_catalog(project, expr: str, resolver: "TargetResolver") -> str:
    """A full-item catalogue (SR-0111): every item matching an SR-0045 filter,
    rendered as the same full block a tl:item marker produces, in UID order. With
    a catch-all filter this is a self-maintaining master reference document. Each
    block renders its links through the resolver (SR-0113), so a borrowed clause a
    local item satisfies shows its reference number."""
    rows = matching(project, expr)
    if not rows:
        return "_(no matching items)_"
    return "\n\n".join(render_item(project, it.uid, resolver) for it in rows)


def _render_unused(project, expr: str) -> str:
    """A report of items matching the filter that no *narrative* document cites
    (SR-0112) — items referenced only by a tl:catalog mirror, or by nothing, still
    count as unused. Requires configured [docs] paths to know what references
    exist; without them the report cannot be computed."""
    if not project.schema.docs_paths:
        return ("_(no [docs] paths configured — tl:unused cannot tell which items "
                "are referenced)_")
    # A catalogue mirror is not *narrative* use of the items it lists, so it is
    # excluded on top of every non-publishing directive (this report's own listing,
    # a graph/chart/stats summary): only prose and item blocks count as a citation.
    referenced = _scan_refs(project, publishing_only=True, exclude=frozenset({"catalog"}))
    rows = [it for it in matching(project, expr) if it.uid not in referenced]
    out = ["| UID | Type | Status | Title |", "|---|---|---|---|"]
    for it in rows:
        out.append(f"| {it.uid} | {it.type} | {it.status} | "
                   f"{_cell(it.title or '')} |")
    if not rows:
        out.append("| _(every matching item is referenced)_ |  |  |  |")
    return "\n".join(out)


# --------------------------------------------------------- diagrams & statistics

# A fixed fill/stroke palette (SR-0115). Item-type classes take colours by their
# sorted position so a type keeps its colour across renders; external targets get a
# dashed grey so borrowed clauses read as "not ours" at a glance.
_GRAPH_PALETTE = (
    "fill:#dbeafe,stroke:#2563eb,color:#1e3a8a",
    "fill:#dcfce7,stroke:#16a34a,color:#14532d",
    "fill:#fef9c3,stroke:#ca8a04,color:#713f12",
    "fill:#fae8ff,stroke:#c026d3,color:#701a75",
    "fill:#ffe4e6,stroke:#e11d48,color:#881337",
    "fill:#e0e7ff,stroke:#4f46e5,color:#312e81",
    "fill:#ccfbf1,stroke:#0d9488,color:#134e4a",
)
_GRAPH_EXTERNAL_STYLE = "fill:#f3f4f6,stroke:#6b7280,color:#374151,stroke-dasharray:4 3"


def _mm_id(uid: str) -> str:
    """A Mermaid-safe node id: a namespace colon or any punctuation becomes ``_`` so
    a borrowed target such as ``asvs:SR-0172`` cannot break the diagram."""
    return re.sub(r"[^0-9A-Za-z_]", "_", uid)


def _mm_class(name: str) -> str:
    """A Mermaid-safe classDef name derived from an item type or ``external``."""
    return re.sub(r"[^0-9A-Za-z_]", "_", name) or "cls"


def _mm_label(uid: str, title: str | None) -> str:
    """A one-line node label ``UID — Title``, stripped of characters that would
    break a Mermaid ``"..."`` label and truncated so a long title stays readable."""
    text = " ".join((title or "").split())
    text = text.replace('"', "'").replace("[", "(").replace("]", ")")
    text = text.replace("<", "(").replace(">", ")").replace("|", "/")
    if len(text) > 44:
        text = text[:43].rstrip() + "…"
    return f"{uid} — {text}" if text else uid


def _parse_graph_arg(arg: str) -> tuple[bool, str]:
    """Split a tl:graph argument into (collapse_external, filter). An optional
    leading ``collapse-external`` flag folds borrowed clauses into one node per
    source namespace (SR-0118); the remainder is the SR-0045 filter, defaulting to
    ``true`` (every item)."""
    stripped = arg.strip()
    m = re.match(r"^collapse-external\b\s*(.*)$", stripped, re.DOTALL)
    if m:
        return True, (m.group(1).strip() or "true")
    return False, (stripped or "true")


def _external_ns(tgt: str) -> str:
    """The source namespace an external target belongs to — the part before the
    colon of a namespace-qualified target (``asvs:SR-0172`` → ``asvs``), or the
    whole target when it is not namespace-qualified."""
    return tgt.split(":", 1)[0] if is_namespace_qualified(tgt) else tgt


def _render_graph(project, arg: str) -> str:
    """A Mermaid flowchart of the matching items and their outgoing-link targets
    (SR-0115), coloured by item type with external targets set apart. The chart
    flows top-down (``TD``) — the layout GitHub's Mermaid build lays out reliably;
    left-to-right (``LR``) and the ELK engine both fail to render there. With a
    leading ``collapse-external`` flag every borrowed clause folds into one node
    per source namespace (SR-0118), so a graph that references many external
    clauses stays narrow instead of sprawling into a wide row of clause boxes. A
    malformed filter fails injection; an empty match renders a placeholder."""
    collapse, expr = _parse_graph_arg(arg)
    rows = matching(project, expr)
    if not rows:
        return "_(no matching items to graph)_"
    idx = Index.build(project)
    nodes: dict[str, tuple[str, str]] = {}     # id-key -> (label, class)
    classes: set[str] = set()
    edges: list[tuple[str, str, str]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for it in rows:
        nodes[it.uid] = (_mm_label(it.uid, it.title), it.type)
        classes.add(it.type)
    for it in rows:
        for tgt, ltype in idx.out_links(it.uid):
            tit = project.get(tgt)
            is_external = tit is None or tit.is_deleted
            if is_external and collapse:
                ns = _external_ns(tgt)
                dst = f"@ns:{ns}"          # cannot collide with a real UID
                if dst not in nodes:
                    nodes[dst] = (ns.upper(), "external")
                    classes.add("external")
            elif is_external:
                dst = tgt
                if dst not in nodes:
                    nodes[dst] = (tgt, "external")
                    classes.add("external")
            else:
                dst = tgt
                if dst not in nodes:
                    nodes[dst] = (_mm_label(tgt, tit.title), tit.type)
                    classes.add(tit.type)
            edge = (it.uid, ltype, dst)
            if edge not in seen_edges:      # a source draws one edge per standard
                seen_edges.add(edge)
                edges.append(edge)
    lines = ["flowchart TD"]
    for uid, (label, cls) in nodes.items():
        lines.append(f'    {_mm_id(uid)}["{label}"]:::{_mm_class(cls)}')
    for src, ltype, tgt in edges:
        lines.append(f"    {_mm_id(src)} -->|{ltype}| {_mm_id(tgt)}")
    palette_types = sorted(c for c in classes if c != "external")
    for i, cls in enumerate(palette_types):
        lines.append(f"    classDef {_mm_class(cls)} "
                     f"{_GRAPH_PALETTE[i % len(_GRAPH_PALETTE)]}")
    if "external" in classes:
        lines.append(f"    classDef external {_GRAPH_EXTERNAL_STYLE}")
    return "```mermaid\n" + "\n".join(lines) + "\n```"


def _parse_chart_arg(arg: str) -> tuple[str, str]:
    """Split a tl:chart argument into (key, filter). The key is the first token; the
    remainder is the filter, defaulting to ``true`` (every item)."""
    m = re.match(r"^(\S+)\s*(.*)$", arg.strip(), re.DOTALL)
    if not m:
        raise InjectError("tl:chart needs a grouping key, e.g. 'tl:chart type'")
    return m.group(1), (m.group(2).strip() or "true")


def _chart_groups(project, key: str, rows: list) -> tuple[list[str], list[int], str]:
    """(labels, counts, title) for a bar chart of ``rows`` grouped by ``key``
    (SR-0116). ``degree`` buckets nodes by total link count; ``type``/``status`` and
    any attribute group by that value. Items lacking an attribute key are skipped."""
    if key == "degree":
        idx = Index.build(project)
        counts: dict[int, int] = {}
        for it in rows:
            d = len(idx.out_links(it.uid)) + len(idx.in_links(it.uid))
            counts[d] = counts.get(d, 0) + 1
        order = sorted(counts)
        return [str(d) for d in order], [counts[d] for d in order], "Nodes by degree"
    counts_s: dict[str, int] = {}
    for it in rows:
        if key in ("type", "status"):
            val = getattr(it, key)
        else:
            raw = it.attrs.get(key)
            val = None if raw is None else str(raw)
        if val is None:
            continue
        counts_s[val] = counts_s.get(val, 0) + 1
    order_s = sorted(counts_s)
    return order_s, [counts_s[v] for v in order_s], f"Items by {key}"


def _render_chart(project, arg: str) -> str:
    """A Mermaid bar chart of the live-item count grouped by a key (SR-0116). A
    malformed filter fails injection; a key no item exhibits renders a placeholder."""
    key, expr = _parse_chart_arg(arg)
    rows = [it for it in matching(project, expr) if _is_live(project, it.uid)]
    labels, counts, title = _chart_groups(project, key, rows)
    if not labels:
        return f"_(no data to chart for '{key}')_"
    xs = ", ".join(f'"{lbl}"' for lbl in labels)
    ys = ", ".join(str(c) for c in counts)
    body = ("xychart-beta\n"
            f'    title "{title}"\n'
            f"    x-axis [{xs}]\n"
            '    y-axis "count"\n'
            f"    bar [{ys}]")
    return f"```mermaid\n{body}\n```"


def _ground_depth(project, idx: "Index") -> dict[str, int]:
    """The grounding depth of every item — the longest chain of grounding links up
    to a root — memoised. A root type is depth 0; an item with no grounding path is
    omitted, so a mean is taken only over items that actually reach a root."""
    roots = project.schema.root_types
    ground = project.schema.ground_link_types
    depth: dict[str, int] = {}

    def _walk(uid: str, seen: frozenset[str]) -> int | None:
        if uid in depth:
            return depth[uid]
        it = project.get(uid)
        if it is None or uid in seen:
            return None
        if it.type in roots:
            depth[uid] = 0
            return 0
        best: int | None = None
        for tgt, _k in idx.out_links(uid, ground):
            sub = _walk(tgt, seen | {uid})
            if sub is not None:
                best = sub + 1 if best is None else max(best, sub + 1)
        if best is not None:
            depth[uid] = best
        return best

    for it in project.items():
        if not it.is_deleted:
            _walk(it.uid, frozenset())
    return depth


def _render_stats(project, expr: str) -> str:
    """A compact Markdown summary of the graph's complexity over the matching items
    (SR-0117): item and link totals by type, grounding depth, the most-connected
    items, and the degree distribution. Malformed filter fails; empty match →
    placeholder."""
    rows = [it for it in matching(project, expr) if _is_live(project, it.uid)]
    if not rows:
        return "_(no matching items to summarise)_"
    idx = Index.build(project)
    by_type: dict[str, int] = {}
    by_link: dict[str, int] = {}
    degrees: dict[str, int] = {}
    for it in rows:
        by_type[it.type] = by_type.get(it.type, 0) + 1
        for _t, ltype in idx.out_links(it.uid):
            by_link[ltype] = by_link.get(ltype, 0) + 1
        degrees[it.uid] = len(idx.out_links(it.uid)) + len(idx.in_links(it.uid))

    depth_all = _ground_depth(project, idx)
    depths = [depth_all[it.uid] for it in rows if it.uid in depth_all]

    def _breakdown(d: dict[str, int]) -> str:
        return " · ".join(f"{k} {v}" for k, v in
                          sorted(d.items(), key=lambda kv: (-kv[1], kv[0])))

    lines = [
        f"- **Items:** {len(rows)} — {_breakdown(by_type)}",
        f"- **Links:** {sum(by_link.values())}" +
        (f" — {_breakdown(by_link)}" if by_link else " — none"),
    ]
    if depths:
        lines.append(f"- **Grounding depth:** max {max(depths)} · "
                     f"mean {sum(depths) / len(depths):.1f}")
    top = sorted(degrees.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    if top and top[0][1] > 0:
        lines.append("- **Most connected:** " +
                     " · ".join(f"{u} ({d})" for u, d in top if d > 0))
    dist: dict[int, int] = {}
    for d in degrees.values():
        dist[d] = dist.get(d, 0) + 1
    lines.append("- **Degree distribution:** " +
                 " · ".join(f"{d} → {n}" for d, n in sorted(dist.items())))
    return "\n".join(lines)


# ---------------------------------------------------- core directive registry

def _selects_uid(project, arg: str) -> Iterable[str]:
    """A ``tl:item`` names its UID directly."""
    return (arg,)


def _selects_filter(project, arg: str) -> Iterable[str]:
    """The directive's argument is an SR-0045 filter; it draws on every item the
    filter selects."""
    return [it.uid for it in matching(project, arg)]


def _selects_matrix(project, arg: str) -> Iterable[str]:
    """A ``tl:matrix`` argument may carry a relationship selector before its
    filter (SR-0099); only the filter part selects items."""
    return _selects_filter(project, _parse_matrix_arg(arg)[4])


def _ignores_resolver(fn):
    """Adapt a renderer that needs no target resolver to the registry's uniform
    ``(project, arg, resolver)`` call."""
    def _call(project, arg, resolver):
        return fn(project, arg)
    return _call


# Each core directive declared exactly once (SR-0186): its renderer, whether the
# items it selects are published for the coverage rule (SR-0096), and how its
# argument names them. `unused`, `graph`, `chart` and `stats` select items to
# derive a report, a picture or a statistic rather than to put their content in
# front of a reader, so they do not publish (SR-0112/0115/0116/0117).
register_directive("item", render_item, selects=_selects_uid)
register_directive("table", _ignores_resolver(_render_table),
                   selects=_selects_filter)
register_directive("matrix", _render_matrix, selects=_selects_matrix)
register_directive("count", _ignores_resolver(_render_count),
                   selects=_selects_filter)
register_directive("catalog", _render_catalog, selects=_selects_filter)
register_directive("unused", _ignores_resolver(_render_unused),
                   publishes=False, selects=_selects_filter)
register_directive("graph", _ignores_resolver(_render_graph),
                   publishes=False, selects=_selects_filter)
register_directive("chart", _ignores_resolver(_render_chart),
                   publishes=False, selects=_selects_filter)
register_directive("stats", _ignores_resolver(_render_stats),
                   publishes=False, selects=_selects_filter)
