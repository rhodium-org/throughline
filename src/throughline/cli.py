# Copyright (c) 2026 Time Back Solutions Limited
# SPDX-License-Identifier: Apache-2.0
"""``tl`` — the command-line entry point (arch doc 07 §6, SR-0060).

Exit codes are a stable contract: 0 = ok, 1 = findings at error severity,
2 = usage/internal error. Everything that mutates disk routes through the
storage layer so the on-disk format stays deterministic (SR-0072).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path

from .fingerprint import fingerprint
from .graph import Index
from .grounding import (
    GroundingError,
    invalidate,
    ratify,
    reaches_root,
    scout_ingest,
)
from .model import Document, Link
from .storage import (
    MANIFEST_NAME,
    ProjectError,
    baseline_statuses,
    init_project,
    load_project,
    load_project_at_ref,
    write_item,
    write_manifest,
)
from .uid import UidError, next_uid, parse_uid
from .validate import ERROR, FilterError, eval_filter, validate

OK, FINDINGS, USAGE = 0, 1, 2


def _version() -> str:
    try:
        return _pkg_version("throughline")
    except PackageNotFoundError:  # pragma: no cover - running from a source tree
        return "0.0.0+unknown"


def _err(msg: str) -> int:
    print(f"tl: {msg}", file=sys.stderr)
    return USAGE


# --------------------------------------------------------------------- commands

class _ScanProgress:
    """Live progress for the init descendant scan (SR-0077). Silent unless the
    scan runs long enough to matter, and only on an interactive terminal — so
    scripts and CI see nothing on stderr and stdout stays clean. Shows a real
    running directory count, never a fabricated percentage."""

    _FRAMES = "|/-\\"

    def __init__(self, stream=sys.stderr, delay: float = 0.5,
                 interval: float = 0.1) -> None:
        self._stream = stream
        self._enabled = stream.isatty()
        self._delay = delay
        self._interval = interval
        self._start = time.monotonic()
        self._last = 0.0
        self._frame = 0
        self._active = False

    def __call__(self, scanned: int) -> None:
        if not self._enabled:
            return
        now = time.monotonic()
        if now - self._start < self._delay or now - self._last < self._interval:
            return
        self._last = now
        spin = self._FRAMES[self._frame % len(self._FRAMES)]
        self._frame += 1
        self._active = True
        self._stream.write(
            f"\r{spin} scanning for existing projects… {scanned:,} dirs"
        )
        self._stream.flush()

    def clear(self) -> None:
        if self._active:
            self._stream.write("\r\033[K")  # carriage return + clear-to-eol
            self._stream.flush()
            self._active = False


def cmd_init(args) -> int:
    progress = _ScanProgress()
    try:
        init_project(args.path, name=args.name, force=args.force,
                     on_progress=progress)
    except ProjectError as e:
        progress.clear()
        return _err(str(e))
    progress.clear()
    print(f"initialised throughline project at {Path(args.path).resolve()}")
    return OK


def cmd_doc_new(args) -> int:
    root = Path(args.path)
    doc_dir = root / args.dir
    if (doc_dir / MANIFEST_NAME).exists():
        return _err(f"{doc_dir} already has a {MANIFEST_NAME}")
    doc_dir.mkdir(parents=True, exist_ok=True)
    doc = Document(prefix=args.prefix, title=args.title or args.prefix,
                   digits=args.digits, parent=args.parent, path=doc_dir)
    write_manifest(doc)
    print(f"created document {args.prefix} at {doc_dir}")
    return OK


def _ground_candidates(project, schema, new_uid: str):
    """Items a new non-root could ground against: roots, plus anything already
    grounded (SR-0073). Roots first, then by type/uid, so the closest 'why' is
    at the top of the picker."""
    idx = Index.build(project)
    cands = []
    for it in project.items():
        if it.uid == new_uid or it.is_deleted:
            continue
        is_root = schema.is_root(it)
        if is_root or reaches_root(idx, schema, it.uid):
            cands.append((0 if is_root else 1, it.type, it.uid, it))
    cands.sort(key=lambda t: (t[0], t[1], t[2]))
    return [it for _, _, _, it in cands]


def _prompt_grounding(project, schema, item, default_type: str):
    """Interactive parent picker (SR-0073). Non-blocking: skip is always
    available and EOF/empty input skips, so scripted/piped use never hangs.
    Returns a list of (target_uid, link_type)."""
    cands = _ground_candidates(project, schema, item.uid)
    if not cands:
        print(f"note: no grounded 'why' exists yet — {item.uid} will be an "
              f"ungrounded root candidate; add a link later with `tl link`.",
              file=sys.stderr)
        return []
    print(f"\n{item.uid} ({item.type}) needs a grounding link to justify it.")
    print("choose a parent to reach 'why':")
    for i, it in enumerate(cands, 1):
        tag = "root" if schema.is_root(it) else "grounded"
        print(f"  {i:>2}. {it.uid}  [{it.type}/{tag}] {it.title}".rstrip())
    try:
        raw = input(f"parent number, or [s]kip [s]: ").strip()
    except EOFError:
        return []
    if not raw or raw.lower() == "s":
        return []
    try:
        pick = cands[int(raw) - 1]
    except (ValueError, IndexError):
        print("skipped (unrecognised choice)", file=sys.stderr)
        return []
    try:
        ltype = input(f"link type [{default_type}]: ").strip() or default_type
    except EOFError:
        ltype = default_type
    return [(pick.uid, ltype)]


def cmd_new(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    doc = project.documents.get(args.prefix)
    if doc is None:
        return _err(f"no document with prefix '{args.prefix}' (run `tl doc new`)")
    if args.uid:
        try:
            pfx, _ = parse_uid(args.uid)
        except UidError as e:
            return _err(str(e))
        if pfx != args.prefix:
            return _err(f"--uid {args.uid} does not match prefix {args.prefix}")
        if project.get(args.uid) is not None:
            return _err(f"{args.uid} already exists")
        uid = args.uid
    else:
        uid = next_uid(doc)
    from .model import Item
    item = Item(uid=uid, type=args.type, status=args.status,
                title=args.title or "", text=args.text or "")
    if args.origin:
        item.attrs["origin"] = args.origin
    item._doc_prefix = doc.prefix

    # Grounding-assisted authoring (SR-0073): attach a parent at birth so the
    # item is justified the moment it exists, rather than being created orphaned
    # and only caught later by `check`. Roots are exempt — they *are* the 'why'.
    schema = project.schema
    default_type = args.ground_type or "derives_from"
    grounds: list[tuple[str, str]] = []
    if args.ground:
        # Explicit grounding is ALWAYS honored — even for root types (e.g. a
        # business_need that `derives_from` the vision). An explicitly requested
        # link is authoring intent and must never be silently discarded; if it
        # cannot be added we fail loudly, we do not drop it (SR-0091, fail-fast).
        for target in args.ground:
            dst = project.get(target)
            if dst is None:
                return _err(f"grounding target {target} does not exist")
            grounds.append((target, default_type))
    elif not schema.is_root(item) and not args.no_interactive \
            and sys.stdin.isatty() and sys.stdout.isatty():
        # No explicit parent: offer to attach one for non-roots only. Roots are
        # exempt from the prompt because they *are* the 'why'.
        grounds = _prompt_grounding(project, schema, item, default_type)

    for target, ltype in grounds:
        item.links.append(Link(target=target, type=ltype))

    doc.items[uid] = item
    path = write_item(item, doc)
    print(f"created {uid} -> {path}")
    for target, ltype in grounds:
        print(f"  grounded: {uid} --{ltype}--> {target}")
    return OK


def cmd_link(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    src = project.get(args.src)
    if src is None:
        return _err(f"source {args.src} does not exist")
    dst = project.get(args.dst)
    if dst is None:
        return _err(f"target {args.dst} does not exist")
    stamp = fingerprint(dst, project.schema) if args.stamp else None
    src.links.append(Link(target=args.dst, type=args.type, stamp=stamp))
    write_item(src, project.document_of(src.uid))
    print(f"linked {args.src} --{args.type}--> {args.dst}"
          + (" (stamped)" if stamp else ""))
    return OK


def cmd_delete(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    item = project.get(args.uid)
    if item is None:
        return _err(f"{args.uid} does not exist")
    item.status = "deleted"
    item.deleted = {"reason": args.reason or "unspecified"}
    write_item(item, project.document_of(item.uid))
    print(f"tombstoned {args.uid} (UID retired, never reused)")
    return OK


def cmd_review(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    targets = list(project.items()) if args.all_clean else [project.get(args.uid)]
    if not args.all_clean and targets[0] is None:
        return _err(f"{args.uid} does not exist")
    n = 0
    for item in targets:
        if item is None or item.is_deleted:
            continue
        fp = fingerprint(item, project.schema)
        if item.reviewed != fp:
            item.reviewed = fp
            write_item(item, project.document_of(item.uid))
            n += 1
    print(f"marked {n} item(s) reviewed at current content")
    return OK


def _by_count(pairs) -> str:
    """'a 3 · b 1' from an iterable of names, most-frequent first."""
    from collections import Counter
    c = Counter(pairs)
    return " · ".join(f"{name} {n}" for name, n in c.most_common())


def _check_summary(project) -> list[str]:
    """Human-readable picture of what check validated (SR-0078): what is in the
    graph, how it is linked, and whether every requirement traces to a root."""
    schema = project.schema
    idx = Index.build(project)
    live = [it for it in project.items() if not it.is_deleted]

    links = [l.type for it in live for l in it.links]
    non_roots = [it for it in live if not schema.is_root(it)]
    grounded = sum(1 for it in non_roots if reaches_root(idx, schema, it.uid))
    delivery = [it for it in live if it.type in schema.delivery_roots]
    served = sum(
        1 for it in delivery
        if any(lt in schema.ground_link_types for _o, lt in idx.in_links(it.uid))
    )

    name = schema.name or Path(project.path).name
    lines = [
        "",
        f"tl check · {name}",
        f"  Items      {len(live)} live   {_by_count(it.type for it in live)}",
        f"  Status     {_by_count(it.status for it in live)}",
        f"  Links      {len(links)}        {_by_count(links)}",
        f"  Grounding  {grounded}/{len(non_roots)} non-root items trace to a "
        f"root · {served}/{len(delivery)} delivery roots served",
    ]
    return lines


def cmd_check(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    baseline = None
    if project.schema.transitions is not None and args.base:
        baseline = baseline_statuses(project, args.base)
    findings = validate(project, strict=args.strict, baseline=baseline)
    if args.format == "json":
        print(json.dumps([f.to_dict() for f in findings], indent=2))
        return FINDINGS if any(f.severity == ERROR for f in findings) else OK

    for f in sorted(findings, key=lambda x: (x.severity != ERROR, x.uid)):
        print(f)
    sys.stdout.flush()
    errs = sum(1 for f in findings if f.severity == ERROR)
    warns = len(findings) - errs
    if not args.quiet:
        for line in _check_summary(project):
            print(line, file=sys.stderr)
    tally = f"\n{errs} error(s), {warns} warning(s)"
    if not args.quiet and errs == 0:
        tally += "  — graph is sound" + (" (strict)" if args.strict else "")
    print(tally, file=sys.stderr)
    return FINDINGS if any(f.severity == ERROR for f in findings) else OK


def cmd_query(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    candidates = [it for it in project.items()
                  if args.all or not it.is_deleted]
    try:
        matched = [it for it in candidates if eval_filter(it, args.expr)]
    except FilterError as e:
        return _err(f"bad filter expression: {e}")
    matched.sort(key=lambda it: it.uid)

    if args.format == "json":
        print(json.dumps([it.to_dict() for it in matched], indent=2, default=str))
    else:
        for it in matched:
            title = f"  {it.title}" if it.title else ""
            print(f"{it.uid}  [{it.type}/{it.status}]{title}")
        sys.stdout.flush()
        print(f"\n{len(matched)} item(s)", file=sys.stderr)
    return OK


def cmd_shape(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    shape = Index.build(project).link_shape()
    rows = sorted(shape.items(), key=lambda kv: (-kv[1], kv[0][1], kv[0][0]))

    if args.format == "json":
        print(json.dumps(
            [{"from": s, "link": lt, "to": t, "count": n}
             for (s, lt, t), n in rows], indent=2))
        return OK
    # Align every column so the arrows and counts read as a clean table.
    labels = [(s, f"-[{lt}]->", t or "<external>", n) for (s, lt, t), n in rows]
    sw = max((len(s) for s, _, _, _ in labels), default=0)
    aw = max((len(a) for _, a, _, _ in labels), default=0)
    dw = max((len(d) for _, _, d, _ in labels), default=0)
    for src, arrow, dst, n in labels:
        print(f"  {src:<{sw}}  {arrow:<{aw}}  {dst:<{dw}}  x{n}")
    sys.stdout.flush()
    print(f"\n{len(rows)} distinct link shape(s)", file=sys.stderr)
    return OK


def _mermaid_types(idx) -> str | None:
    """A Mermaid flowchart of the type model: item types as nodes, one labelled
    edge per (source type, link type, target type) observed in the graph. Edges
    to external/unknown targets are omitted (SR-0086)."""
    edges = sorted({(s, lt, t) for (s, lt, t) in idx.link_shape() if t is not None})
    if not edges:
        return None
    lines = ["flowchart LR"]
    lines += [f"    {s} -->|{lt}| {t}" for s, lt, t in edges]
    return "\n".join(lines)


def _mermaid_transitions(schema) -> str | None:
    """A Mermaid state diagram of the declared status lifecycle (SR-0086).
    ``None`` when the project declares no [transitions]."""
    if not schema.transitions:
        return None
    lines = ["stateDiagram-v2"]
    for frm in sorted(schema.transitions):
        for to in sorted(schema.transitions[frm]):
            lines.append(f"    {frm} --> {to}")
    return "\n".join(lines)


def cmd_diagram(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    idx = Index.build(project)
    blocks = []  # (heading, mermaid-source-or-None, empty-note)
    if args.kind in ("types", "both"):
        blocks.append(("Type model", _mermaid_types(idx),
                       "no links in the graph yet"))
    if args.kind in ("transitions", "both"):
        blocks.append(("Status transitions", _mermaid_transitions(project.schema),
                       "no [transitions] declared"))

    emitted = 0
    for heading, src, empty in blocks:
        if args.format == "markdown":
            print(f"### {heading}\n")
            print(f"```mermaid\n{src}\n```\n" if src else f"_{empty}_\n")
        elif src is not None:
            if emitted:
                print()
            print(src)
        else:
            print(f"%% {heading}: {empty}", file=sys.stderr)
        emitted += src is not None
    sys.stdout.flush()
    return OK


# ------------------------------------------------------------- document render

def _doc_order(project) -> list:
    """Documents in a stable, readable order — shallower in the parent chain
    first (roots before what derives from them), then by prefix (SR-0089)."""
    docs = project.documents

    def depth(prefix: str, seen: tuple = ()) -> int:
        d = docs.get(prefix)
        if d is None or not d.parent or d.parent in seen or d.parent not in docs:
            return 0
        return 1 + depth(d.parent, seen + (prefix,))

    return sorted(docs.values(), key=lambda d: (depth(d.prefix), d.prefix))


def _item_md(item) -> str:
    lines = [f"### {item.uid} — {item.title or '(untitled)'}", ""]
    meta = f"`{item.type}` · status `{item.status}`"
    if not item.normative:
        meta += " · non-normative"
    lines += [meta, ""]
    if item.text:
        lines += [f"> {ln}" if ln else ">" for ln in item.text.splitlines()]
        lines.append("")
    if item.attrs:
        lines += [" · ".join(f"**{k}**: {v}" for k, v in item.attrs.items()), ""]
    if item.links:
        lines += [f"- _{l.type}_ → {l.target}" for l in item.links]
        lines.append("")
    return "\n".join(lines)


def _docs_provenance(ref: str | None, sha: str | None) -> str:
    if ref is None:
        return "_Generated by `tl docs` from the working tree._"
    return f"_Generated by `tl docs` from `{ref}` (`{(sha or '')[:12]}`)._"


def _docs_markdown(project, name: str, doc_prefix: str | None = None,
                   ref: str | None = None, sha: str | None = None) -> str:
    docs = [d for d in _doc_order(project)
            if doc_prefix is None or d.prefix == doc_prefix]
    body: list[str] = []
    total = 0
    for doc in docs:
        live = sorted((it for it in doc.items.values() if not it.is_deleted),
                      key=lambda it: it.uid)
        body += [f"## {doc.title or doc.prefix} ({doc.prefix})", ""]
        if not live:
            body += ["_No items._", ""]
            continue
        for it in live:
            body.append(_item_md(it))
            total += 1
    head = [f"# {name} — Requirements", "", _docs_provenance(ref, sha), "",
            f"_{total} live item(s) across {len(docs)} document(s)._", ""]
    return "\n".join(head + body).rstrip("\n") + "\n"


def cmd_docs(args) -> int:
    sha = None
    try:
        if args.at:
            project, sha = load_project_at_ref(args.path, args.at)
        else:
            project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    if args.doc is not None and args.doc not in project.documents:
        have = ", ".join(sorted(project.documents)) or "none"
        return _err(f"no document with prefix {args.doc!r} (have: {have})")
    name = project.schema.name or Path(args.path).resolve().name
    sys.stdout.write(_docs_markdown(project, name, doc_prefix=args.doc,
                                    ref=args.at or None, sha=sha))
    sys.stdout.flush()
    return OK


# --------------------------------------------------------------- agent context

def _fmt_set(items) -> str:
    """`` `a`, `b` `` from any iterable; ``(none)`` when empty."""
    xs = sorted(items)
    return ", ".join(f"`{x}`" for x in xs) if xs else "_(none)_"


def _ctx_idd(schema) -> str:
    """The fixed Intent-Driven Development contract, with this project's own
    root types and grounding links named so it is accurate for the project."""
    return (
        "## The contract: Intent-Driven Development\n\n"
        "This project uses **throughline**, a Git-native requirements tool. Each item "
        "is one small YAML file with a permanent UID; the files are the product "
        "and live in the repo. You work under a discipline called **Intent-Driven "
        "Development (IDD)** — the *why* axis to BDD/TDD's *what*:\n\n"
        f"- **Roots justify themselves.** The root types — {_fmt_set(schema.root_types)} "
        "— are the *why*; they may exist ungrounded.\n"
        "- **Everything else must justify itself** by reaching a root through a "
        f"**grounding link** ({_fmt_set(schema.ground_link_types)}). Those links "
        "form a DAG — circular justification is rejected.\n"
        "- **Author the why first.** Create the grounded requirement as a "
        "`draft` — throughline's version of a *red test*: specified and justified, "
        "not yet built. Implement it, then flip it to `approved`/`implemented` "
        "when the thing exists. Ground at birth with "
        "`tl new <PREFIX> --type <T> --ground <PARENT_UID>`.\n"
        f"- **AI-origin items are provisional.** An item whose `origin` is one of "
        f"{_fmt_set(schema.ai_origins)} enters `proposed` and must be **ratified** "
        "by a human (`tl ratify <UID> --by <who>`) before it counts as "
        "agreed. If you are that agent, you propose; a human accepts.\n"
        "- **`tl check` is the gate.** It validates the whole graph and "
        "returns a stable exit code (0 ok · 1 error-severity findings · 2 usage "
        "error). An ungrounded, unserved, or otherwise invalid graph **fails the "
        "build**. Keep it green; run it before you commit."
    )


def _ctx_types(schema) -> str:
    out = ["## Item types and their attributes\n"]
    if not schema.types:
        out.append("_This project declares no per-type attribute schema; item "
                    "types are free-form._")
        return "\n".join(out)
    for tname in sorted(schema.types):
        tags = []
        if tname in schema.root_types:
            tags.append("root")
        if tname in schema.delivery_roots:
            tags.append("delivery-root")
        tag = f" _({', '.join(tags)})_" if tags else ""
        out.append(f"### `{tname}`{tag}\n")
        specs = schema.attrs_for(tname)
        if not specs:
            out.append("_(no declared attributes)_\n")
            continue
        out.append("| attribute | kind | required | normative | values |")
        out.append("|---|---|---|---|---|")
        for aname in sorted(specs):
            s = specs[aname]
            vals = ", ".join(f"`{v}`" for v in s.values) if s.values else "—"
            out.append(
                f"| `{aname}` | {s.kind or 'free'} | "
                f"{'yes' if s.required else 'no'} | "
                f"{'yes' if s.normative else 'no'} | {vals} |")
        out.append("")
    return "\n".join(out).rstrip()


def _ctx_links(schema) -> str:
    out = ["## Links\n"]
    if schema.link_types is None:
        out.append("- **Link vocabulary:** unconstrained (any link type is legal).")
    else:
        out.append(f"- **Link vocabulary:** {_fmt_set(schema.link_types)}.")
    out.append(f"- **Grounding links** (these carry justification): "
               f"{_fmt_set(schema.ground_link_types)}.")
    if schema.link_rules:
        out.append("- **Endpoint rules** — a link of a constrained type may only "
                   "join the item types shown (unlisted link types are "
                   "unconstrained):\n")
        out.append("| link | from | to |")
        out.append("|---|---|---|")
        for lt in sorted(schema.link_rules):
            rule = schema.link_rules[lt]
            frm = _fmt_set(rule.frm) if rule.frm is not None else "_any_"
            to = _fmt_set(rule.to) if rule.to is not None else "_any_"
            out.append(f"| `{lt}` | {frm} | {to} |")
    else:
        out.append("- **Endpoint rules:** none declared — any type may sit at "
                   "either end of any link.")
    return "\n".join(out)


def _ctx_status(schema) -> str:
    out = ["## Status and lifecycle\n"]
    if schema.statuses is None:
        out.append("- **Statuses:** unconstrained (any status string is legal).")
    else:
        out.append(f"- **Statuses:** {_fmt_set(schema.statuses)}.")
    if schema.transitions:
        out.append("- **Allowed transitions** (a status may only move along "
                   "these edges; staying put is always allowed):\n")
        for frm in sorted(schema.transitions):
            tos = schema.transitions[frm]
            out.append(f"  - `{frm}` → {_fmt_set(tos)}")
        out.append("\n  Render this as a diagram with "
                   "`tl diagram transitions`.")
    else:
        out.append("- **Transitions:** none declared — any status may change to "
                   "any other.")
    return "\n".join(out)


def _ctx_grounding(schema) -> str:
    return (
        "## Grounding configuration\n\n"
        f"- **Root types** (may be ungrounded): {_fmt_set(schema.root_types)}\n"
        f"- **Delivery roots** (must be *served* — something must derive from / "
        f"mitigate them): {_fmt_set(schema.delivery_roots)}\n"
        f"- **Grounding link types:** {_fmt_set(schema.ground_link_types)}\n"
        f"- **AI origins** (items with these origins enter `proposed` and need "
        f"human ratification): {_fmt_set(schema.ai_origins)}"
    )


def _ctx_coverage(schema) -> str:
    out = ["## Coverage rules\n"]
    if not schema.coverage:
        out.append("_No `[[rules.coverage]]` declared._")
        return "\n".join(out)
    out.append("Each rule requires the matching items to have the stated link "
               "(unmet → a `coverage` finding):\n")
    for rule in schema.coverage:
        filt = rule.get("filter", "*")
        needs = rule.get("needs", "?")
        sev = rule.get("severity", "error")
        out.append(f"- items where `{filt}` **need** `{needs}` "
                   f"(severity: {sev})")
    return "\n".join(out)


_CTX_FORMAT = (
    "## The on-disk format\n\n"
    "A project is a directory: `throughline.toml` (config) + one folder per document, "
    "each with a `.document.yml` manifest and one `<UID>.yml` per item. **An item "
    "looks like this** (attributes under `attrs:` are the project-defined ones "
    "listed above for that type):\n\n"
    "```yaml\n"
    "uid: FR-0022                 # permanent, immutable, never reused\n"
    "type: requirement            # one of the item types above\n"
    "status: approved             # one of the declared statuses\n"
    "title: Guided setup wizard\n"
    "text: The system shall walk a new user through setup in 3 steps.\n"
    "normative: true              # content changes mark dependents suspect\n"
    "links:\n"
    "  - target: BN-0003          # a grounding link up to a root\n"
    "    type: derives_from\n"
    "  - target: ASM-0002\n"
    "    type: assumes\n"
    "    stamp: sha256:…          # target fingerprint when last confirmed\n"
    "attrs:\n"
    "  priority: must             # project-defined attributes (see the type above)\n"
    "```\n\n"
    "Never invent a UID or edit a manifest by hand — let the CLI allocate."
)

_CTX_COMMANDS = (
    "## Commands you will use\n\n"
    "```\n"
    "tl new <PREFIX> --type <T> [--title …] [--text …] --ground <PARENT_UID>\n"
    "tl link <SRC> <DST> --type <kind>       # add a typed link\n"
    "tl check [--strict] [--format json]     # THE GATE — run before committing\n"
    "tl ratify <UID> --by <who>              # a human accepts a proposed item\n"
    "tl trace <UID> [--direction in|out]     # walk an item to its 'why'\n"
    "tl blast <UID>                          # what depends on an item\n"
    "tl shape [--format json]                # observed (from)-[link]->(to) triples\n"
    "tl diagram [types|transitions|both]     # Mermaid of the model / lifecycle\n"
    "tl docs [--doc PREFIX] [--at REF]       # render a Markdown requirements document\n"
    "tl context                              # regenerate this brief\n"
    "```"
)


def _ctx_snapshot(project) -> str:
    live = [it for it in project.items() if not it.is_deleted]
    out = ["## Live snapshot of this graph\n"]
    out.append(f"- **Items:** {len(live)} live — {_by_count(it.type for it in live)}")
    out.append(f"- **Status:** {_by_count(it.status for it in live)}")
    shape = Index.build(project).link_shape()
    if shape:
        out.append("- **Link shape** (source → link → target · count):")
        for (s, lt, t), n in sorted(shape.items(), key=lambda kv: (-kv[1], kv[0])):
            out.append(f"  - `{s}` -[{lt}]-> `{t or '<external>'}` × {n}")
    else:
        out.append("- **Link shape:** no links yet.")
    return "\n".join(out)


def _context_markdown(project) -> str:
    schema = project.schema
    name = schema.name or Path(project.path).name
    header = (
        f"# Working in the throughline project: {name}\n\n"
        "You are an AI agent working in a project managed by **throughline**. This "
        "brief is **generated from the project's live `throughline.toml`** — it "
        "reflects the rules the validator actually enforces. If the "
        "configuration changes, regenerate it with `tl context`. "
        "If a capability isn't described here, it isn't in the tool."
    )
    sections = [
        header,
        _ctx_idd(schema),
        _ctx_types(schema),
        _ctx_links(schema),
        _ctx_status(schema),
        _ctx_grounding(schema),
        _ctx_coverage(schema),
        _CTX_FORMAT,
        _CTX_COMMANDS,
        _ctx_snapshot(project),
    ]
    return "\n\n".join(sections) + "\n"


def cmd_context(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    sys.stdout.write(_context_markdown(project))
    sys.stdout.flush()
    return OK


def cmd_trace(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    if project.get(args.uid) is None:
        return _err(f"{args.uid} does not exist")
    idx = Index.build(project)
    seen: set[str] = set()

    def walk(uid: str, depth: int, prefix: str) -> None:
        if uid in seen or (args.depth and depth > args.depth):
            item = project.get(uid)
            marker = " (cycle)" if uid in seen else ""
            print(f"{prefix}{uid}{marker}")
            return
        seen.add(uid)
        item = project.get(uid)
        label = f"{uid}  [{item.type}/{item.status}] {item.title}".rstrip()
        print(f"{prefix}{label}")
        edges = (idx.in_links(uid) if args.direction == "in" else idx.out_links(uid))
        for i, (other, ltype) in enumerate(edges):
            last = i == len(edges) - 1
            branch = "└─" if last else "├─"
            child_prefix = prefix.replace("├─", "│ ").replace("└─", "  ")
            print(f"{child_prefix}{branch}({ltype}) ", end="")
            walk(other, depth + 1, "")

    walk(args.uid, 0, "")
    return OK


def cmd_blast(args) -> int:
    try:
        project = load_project(args.path)
    except ProjectError as e:
        return _err(str(e))
    if project.get(args.uid) is None:
        return _err(f"{args.uid} does not exist")
    idx = Index.build(project)
    affected = idx.impact(args.uid)
    if args.format == "json":
        print(json.dumps(affected, indent=2))
    else:
        print(f"{args.uid} — blast radius: {len(affected)} dependent item(s)")
        for uid in affected:
            it = project.get(uid)
            print(f"  {uid}  [{it.type}/{it.status}] {it.title}".rstrip())
    return OK


def cmd_ratify(args) -> int:
    try:
        project = load_project(args.path)
        item = ratify(project, args.uid, by=args.by)
    except (ProjectError, GroundingError) as e:
        return _err(str(e))
    write_item(item, project.document_of(item.uid))
    print(f"{args.uid} ratified by {args.by}")
    return OK


def cmd_invalidate(args) -> int:
    try:
        project = load_project(args.path)
        affected = invalidate(project, args.uid, reason=args.reason or "")
    except (ProjectError, GroundingError) as e:
        return _err(str(e))
    write_item(project.get(args.uid), project.document_of(args.uid))
    for uid in affected:
        write_item(project.get(uid), project.document_of(uid))
    print(f"{args.uid} invalidated; {len(affected)} dependent(s) marked suspect")
    for uid in affected:
        print(f"  {uid}")
    return OK


def cmd_scout(args) -> int:
    try:
        project = load_project(args.path)
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    except (ProjectError, OSError, json.JSONDecodeError) as e:
        return _err(str(e))
    summary = scout_ingest(project, report)
    for prefix, uid in sorted(summary["touched"]):
        item = project.get(uid)
        if item is not None:
            write_item(item, project.document_of(uid))
    print(f"scout ingest: {len(summary['roots_proposed'])} root(s) proposed, "
          f"{len(summary['ambiguities_flagged'])} ambiguity(ies) flagged, "
          f"{len(summary['gaps'])} coverage gap(s)")
    return OK


# ------------------------------------------------------------------------ parse

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tl", description=__doc__.splitlines()[0])
    p.add_argument("--version", action="version", version=f"tl {_version()}")
    p.add_argument("-C", "--path", default=".", help="project root (default: .)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a new project")
    s.add_argument("--name", default="Example")
    s.add_argument("--force", action="store_true",
                   help="create even if nested inside an existing project")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("doc", help="document operations")
    dsub = s.add_subparsers(dest="doc_cmd", required=True)
    d = dsub.add_parser("new", help="create a document/manifest")
    d.add_argument("prefix")
    d.add_argument("dir", help="directory (relative to project root)")
    d.add_argument("--title", default="")
    d.add_argument("--digits", type=int, default=4)
    d.add_argument("--parent", default=None)
    d.set_defaults(func=cmd_doc_new)

    s = sub.add_parser("new", help="allocate + create an item")
    s.add_argument("prefix")
    s.add_argument("--uid", default=None, help="explicit UID (must match prefix)")
    s.add_argument("--type", default="requirement")
    s.add_argument("--status", default="draft")
    s.add_argument("--title", default="")
    s.add_argument("--text", default="")
    s.add_argument("--origin", default=None, help="human|ai|hybrid")
    s.add_argument("--ground", action="append", metavar="UID",
                   help="parent to ground against at creation (repeatable)")
    s.add_argument("--ground-type", default=None,
                   help="grounding link type (default: derives_from)")
    s.add_argument("--no-interactive", action="store_true",
                   help="never prompt for a parent (for scripts/CI)")
    s.set_defaults(func=cmd_new)

    s = sub.add_parser("link", help="add a typed link SRC -> DST")
    s.add_argument("src")
    s.add_argument("dst")
    s.add_argument("--type", required=True)
    s.add_argument("--stamp", action="store_true",
                   help="record target fingerprint (suspect tracking)")
    s.set_defaults(func=cmd_link)

    s = sub.add_parser("delete", help="tombstone an item (never erased)")
    s.add_argument("uid")
    s.add_argument("--reason", default="")
    s.set_defaults(func=cmd_delete)

    s = sub.add_parser("review", help="mark item(s) reviewed at current content")
    s.add_argument("uid", nargs="?", default=None)
    s.add_argument("--all-clean", action="store_true")
    s.set_defaults(func=cmd_review)

    s = sub.add_parser("check", help="validate the graph (CI gate)")
    s.add_argument("--strict", action="store_true", help="warnings become errors")
    s.add_argument("--base", default="HEAD", metavar="REF",
                   help="git ref for the status-transition baseline "
                        "(default HEAD; empty to disable)")
    s.add_argument("--format", choices=["text", "json"], default="text")
    s.add_argument("--quiet", "-q", action="store_true",
                   help="suppress the graph summary (findings + tally only)")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("query", aliases=["ls"],
                       help="list items matching a filter expression")
    s.add_argument("expr", nargs="?", default="",
                   help="SR-0045 filter, e.g. \"status == 'draft'\" or "
                        "\"type == 'system_requirement' and "
                        "attrs.get('priority') == 'must'\"; omit to list all")
    s.add_argument("--format", choices=["text", "json"], default="text")
    s.add_argument("--all", action="store_true",
                   help="include deleted (tombstoned) items")
    s.set_defaults(func=cmd_query)

    s = sub.add_parser("shape",
                       help="report the graph's (from)-[link]->(to) type shape")
    s.add_argument("--format", choices=["text", "json"], default="text")
    s.set_defaults(func=cmd_shape)

    s = sub.add_parser("diagram",
                       help="emit Mermaid diagrams of the type model and status lifecycle")
    s.add_argument("kind", nargs="?", choices=["types", "transitions", "both"],
                   default="both")
    s.add_argument("--format", choices=["markdown", "mermaid"], default="markdown")
    s.set_defaults(func=cmd_diagram)

    s = sub.add_parser(
        "docs",
        help="render a Markdown requirements document from the graph")
    s.add_argument("--doc", default=None, metavar="PREFIX",
                   help="limit to one document by its UID prefix")
    s.add_argument("--at", default=None, metavar="REF",
                   help="render as the graph stood at a git revision")
    s.set_defaults(func=cmd_docs)

    s = sub.add_parser(
        "context",
        help="emit an agent-facing Markdown brief (IDD + this project's model)")
    s.set_defaults(func=cmd_context)

    s = sub.add_parser("trace", help="print the link tree from a UID")
    s.add_argument("uid")
    s.add_argument("--direction", choices=["in", "out"], default="out")
    s.add_argument("--depth", type=int, default=0, help="0 = unbounded")
    s.set_defaults(func=cmd_trace)

    s = sub.add_parser("blast", help="show the blast radius (dependents) of a UID")
    s.add_argument("uid")
    s.add_argument("--format", choices=["text", "json"], default="text")
    s.set_defaults(func=cmd_blast)

    s = sub.add_parser("ratify", help="a human takes accountability for an item")
    s.add_argument("uid")
    s.add_argument("--by", required=True, help="ratifier identity")
    s.set_defaults(func=cmd_ratify)

    s = sub.add_parser("invalidate", help="falsify an item; cascade suspect")
    s.add_argument("uid")
    s.add_argument("--reason", default="")
    s.set_defaults(func=cmd_invalidate)

    s = sub.add_parser("scout", help="ingest a scout report (proposes; humans ratify)")
    s.add_argument("report", help="path to a scout report JSON")
    s.set_defaults(func=cmd_scout)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:  # pragma: no cover
        return USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
